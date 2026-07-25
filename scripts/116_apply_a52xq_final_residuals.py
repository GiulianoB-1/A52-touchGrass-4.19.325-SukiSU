#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DISPLAY_ROOTS = ("drivers/a52_display", "techpack/display")


def read(path: Path) -> str:
    return path.read_text(errors="replace")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def remove_ion_flag_cached(gki: Path) -> int:
    path = gki / "a52-port-compat.h"
    content = read(path)
    changes = 0

    guarded = re.compile(
        r"(?m)^#ifndef[ \t]+ION_FLAG_CACHED[ \t]*\n"
        r"#define[ \t]+ION_FLAG_CACHED[^\n]*\n"
        r"#endif[^\n]*\n?"
    )
    content, count = guarded.subn("", content)
    changes += count

    content, count = re.subn(
        r"(?m)^#define[ \t]+ION_FLAG_CACHED(?:[ \t].*)?\n?",
        "",
        content,
    )
    changes += count

    if "#define ION_FLAG_CACHED" in content:
        raise SystemExit("failed to remove the compatibility ION_FLAG_CACHED macro")

    if changes:
        write(path, content)
    return changes


def redirect_header(gki: Path, name: str, candidates: tuple[str, ...]) -> dict[str, object]:
    wrapper = gki / "a52-compat/include/linux" / name
    selected = None
    for candidate in candidates:
        if (gki / candidate).is_file():
            selected = candidate
            break
    if selected is None:
        raise SystemExit(f"no in-tree target exists for compatibility header {name}")

    # The wrapper sits at a52-compat/include/linux/<name>.
    relative = "../../../" + selected
    guard = "__A52_COMPAT_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
    content = f"""/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef {guard}
#define {guard}
/* A52_PHASE14_DIRECT_HEADER_REDIRECT */
#include \"{relative}\"
#endif
"""
    previous = read(wrapper) if wrapper.is_file() else ""
    changed = previous != content
    if changed:
        write(wrapper, content)
    return {"changed": changed, "target": selected, "relative_include": relative}


def initialize_declaration_in_function(
    path: Path,
    signature: str,
    declaration: str,
    initialized: str,
) -> int:
    content = read(path)
    start = content.find(signature)
    if start < 0:
        raise SystemExit(f"missing function {signature} in {path}")

    next_function = content.find("\nstatic ", start + len(signature))
    end = len(content) if next_function < 0 else next_function
    section = content[start:end]

    if initialized in section:
        return 0
    if section.count(declaration) != 1:
        raise SystemExit(
            f"expected exactly one declaration {declaration!r} in {signature} at {path}"
        )

    section = section.replace(declaration, initialized, 1)
    content = content[:start] + section + content[end:]
    write(path, content)
    return 1


def patch_sde_crtc(gki: Path) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    found = False
    for root in DISPLAY_ROOTS:
        path = gki / root / "msm/sde/sde_crtc.c"
        if not path.is_file():
            continue
        found = True
        changes = {
            "dest_scaler_hw_ds": initialize_declaration_in_function(
                path,
                "static int _sde_crtc_check_dest_scaler_data(",
                "\tstruct sde_hw_ds *hw_ds;",
                "\tstruct sde_hw_ds *hw_ds = NULL;",
            ),
            "dest_scaler_cfg": initialize_declaration_in_function(
                path,
                "static int _sde_crtc_check_dest_scaler_data(",
                "\tstruct sde_hw_ds_cfg *cfg;",
                "\tstruct sde_hw_ds_cfg *cfg = NULL;",
            ),
            "atomic_check_plane": initialize_declaration_in_function(
                path,
                "static int _sde_crtc_atomic_check_pstates(",
                "\tstruct drm_plane *plane;",
                "\tstruct drm_plane *plane = NULL;",
            ),
        }
        report[str(path.relative_to(gki))] = changes

    if not found:
        raise SystemExit("no staged SDE CRTC source found")
    return report


def validate(gki: Path) -> dict[str, bool]:
    compat = read(gki / "a52-port-compat.h")
    dma_contiguous = read(gki / "a52-compat/include/linux/dma-contiguous.h")
    dma_debug = read(gki / "a52-compat/include/linux/dma-debug.h")

    sde_sources = [
        gki / root / "msm/sde/sde_crtc.c"
        for root in DISPLAY_ROOTS
        if (gki / root / "msm/sde/sde_crtc.c").is_file()
    ]
    sde_text = "\n".join(read(path) for path in sde_sources)

    return {
        "ion_macro_removed": "#define ION_FLAG_CACHED" not in compat,
        "dma_contiguous_redirected": (
            "A52_PHASE14_DIRECT_HEADER_REDIRECT" in dma_contiguous
            and "include_next" not in dma_contiguous
        ),
        "dma_debug_redirected": (
            "A52_PHASE14_DIRECT_HEADER_REDIRECT" in dma_debug
            and "include_next" not in dma_debug
        ),
        "hw_ds_initialized": "struct sde_hw_ds *hw_ds = NULL;" in sde_text,
        "cfg_initialized": "struct sde_hw_ds_cfg *cfg = NULL;" in sde_text,
        "plane_initialized_in_atomic_check": (
            "static int _sde_crtc_atomic_check_pstates(" in sde_text
            and "struct drm_plane *plane = NULL;" in sde_text
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    args.output.mkdir(parents=True, exist_ok=True)

    report = {
        "status": "phase14-final-residuals-staged",
        "flashable": False,
        "hardware_validated": False,
        "scope": "the nine compiler errors remaining after Workflow 115",
        "ion_macro_removals": remove_ion_flag_cached(gki),
        "header_redirects": {
            "dma-contiguous.h": redirect_header(
                gki,
                "dma-contiguous.h",
                ("include/linux/dma-contiguous.h",),
            ),
            "dma-debug.h": redirect_header(
                gki,
                "dma-debug.h",
                ("include/linux/dma-debug.h", "kernel/dma/debug.h"),
            ),
        },
        "sde_crtc": patch_sde_crtc(gki),
        "fallbacks": [
            "No new runtime fallback was added in Workflow 116.",
            "The existing Workflow 115 diagnostic fallbacks remain unvalidated.",
        ],
    }
    report["validation"] = validate(gki)

    bad = [name for name, passed in report["validation"].items() if not passed]
    report_path = args.output / "phase14-final-residuals-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if bad:
        raise SystemExit("Workflow 116 staging validation failed: " + ", ".join(bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
