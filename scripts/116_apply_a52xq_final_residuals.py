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


def restore_qseecom_ion_flag(gki: Path) -> int:
    path = gki / "a52-compat/include/linux/ion_kernel.h"
    content = read(path)
    marker = "A52_PHASE14_QSEECOM_ION_FLAG"
    if marker in content:
        return 0

    block = """/* A52_PHASE14_QSEECOM_ION_FLAG: legacy ION ABI value. */
#ifndef ION_FLAG_CACHED
#define ION_FLAG_CACHED 1
#endif
"""
    end = content.rfind("#endif")
    if end < 0:
        raise SystemExit(f"missing include guard terminator in {path}")

    content = content[:end] + block + "\n" + content[end:]
    write(path, content)
    return 1


def mark_qseecom_callback_maybe_unused(gki: Path) -> int:
    path = gki / "drivers/a52_secure/qseecom.c"
    content = read(path)
    old = "static int qseecom_destroy_bridge_callback("
    new = "static int __maybe_unused qseecom_destroy_bridge_callback("
    if new in content:
        return 0
    if content.count(old) != 1:
        raise SystemExit(
            "expected exactly one qseecom_destroy_bridge_callback definition"
        )

    write(path, content.replace(old, new, 1))
    return 1


def replace_legacy_header(
    gki: Path,
    name: str,
    candidates: tuple[str, ...],
    fallback_includes: tuple[str, ...],
) -> dict[str, object]:
    wrapper = gki / "a52-compat/include/linux" / name
    selected = next(
        (candidate for candidate in candidates if (gki / candidate).is_file()),
        None,
    )
    guard = "__A52_COMPAT_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()

    if selected is not None:
        relative = "../../../" + selected
        body = (
            "/* A52_PHASE14_DIRECT_HEADER_REDIRECT */\n"
            f'#include "{relative}"\n'
        )
        mode = "direct-redirect"
    else:
        relative = None
        body = "/* A52_PHASE14_LEGACY_HEADER_SHIM */\n" + "".join(
            f"#include <{header}>\n" for header in fallback_includes
        )
        mode = "compatibility-shim"

    content = f"""/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef {guard}
#define {guard}
{body}#endif
"""
    previous = read(wrapper) if wrapper.is_file() else ""
    changed = previous != content
    if changed:
        write(wrapper, content)
    return {
        "changed": changed,
        "mode": mode,
        "target": selected,
        "relative_include": relative,
        "fallback_includes": list(fallback_includes) if selected is None else [],
    }


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


def count_smc_instructions(content: str) -> int:
    return len(re.findall(r'"smc[ \t]+#0\\n"', content))


def remove_legacy_scm_asmeq(gki: Path) -> int:
    path = gki / "drivers/a52_secure/a52_legacy_scm.c"
    content = read(path)
    occurrences = content.count("__asmeq(")
    if occurrences == 0:
        return 0

    pattern = re.compile(r"(?m)^[ \t]*__asmeq\([^\n]*\)[ \t]*\n")
    content, removed = pattern.subn("", content)
    if removed != occurrences or "__asmeq(" in content:
        raise SystemExit(
            f"failed to remove all legacy SCM __asmeq assertions: "
            f"found={occurrences}, removed={removed}"
        )
    if content.count("asm volatile(") < 3 or count_smc_instructions(content) < 3:
        raise SystemExit("legacy SCM SMC instructions were not preserved")

    write(path, content)
    return removed


def find_function_close(content: str, signature: str, path: Path) -> tuple[int, int]:
    start = content.find(signature)
    if start < 0:
        raise SystemExit(f"missing function {signature} in {path}")
    opening = content.find("{", start + len(signature))
    if opening < 0:
        raise SystemExit(f"missing opening brace for {signature} in {path}")

    depth = 0
    for index in range(opening, len(content)):
        char = content[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return opening, index
    raise SystemExit(f"missing closing brace for {signature} in {path}")


def guard_adreno_coresight_when_disabled(gki: Path) -> int:
    path = gki / "drivers/gpu/msm/adreno_coresight.c"
    content = read(path)
    marker = "A52_PHASE14_CONFIG_OFF_CORESIGHT"
    if marker in content:
        return 0

    signature = "int adreno_coresight_init(struct adreno_device *adreno_dev)"
    opening, closing = find_function_close(content, signature, path)
    body = content[opening + 1 : closing]
    if body.count("of_get_coresight_platform_data(") != 1:
        raise SystemExit(
            "expected exactly one legacy CoreSight platform-data call in "
            "adreno_coresight_init"
        )

    guarded = (
        "\n#ifndef CONFIG_CORESIGHT\n"
        "\t/* A52_PHASE14_CONFIG_OFF_CORESIGHT: optional tracing is disabled. */\n"
        "\treturn 0;\n"
        "#else"
        + body
        + "#endif\n"
    )
    content = content[: opening + 1] + guarded + content[closing:]
    write(path, content)
    return 1


def validate(gki: Path) -> dict[str, bool]:
    compat = read(gki / "a52-port-compat.h")
    ion_kernel = read(gki / "a52-compat/include/linux/ion_kernel.h")
    qseecom = read(gki / "drivers/a52_secure/qseecom.c")
    dma_contiguous = read(gki / "a52-compat/include/linux/dma-contiguous.h")
    dma_debug = read(gki / "a52-compat/include/linux/dma-debug.h")
    legacy_scm = read(gki / "drivers/a52_secure/a52_legacy_scm.c")
    adreno_coresight = read(gki / "drivers/gpu/msm/adreno_coresight.c")

    sde_sources = [
        gki / root / "msm/sde/sde_crtc.c"
        for root in DISPLAY_ROOTS
        if (gki / root / "msm/sde/sde_crtc.c").is_file()
    ]
    sde_text = "\n".join(read(path) for path in sde_sources)

    return {
        "ion_macro_removed": "#define ION_FLAG_CACHED" not in compat,
        "qseecom_ion_flag_restored": (
            "A52_PHASE14_QSEECOM_ION_FLAG" in ion_kernel
            and "#define ION_FLAG_CACHED 1" in ion_kernel
        ),
        "qseecom_callback_maybe_unused": (
            "static int __maybe_unused qseecom_destroy_bridge_callback(" in qseecom
        ),
        "dma_contiguous_header_replaced": (
            "A52_PHASE14_" in dma_contiguous
            and "include_next" not in dma_contiguous
        ),
        "dma_debug_header_replaced": (
            "A52_PHASE14_" in dma_debug
            and "include_next" not in dma_debug
        ),
        "legacy_scm_asmeq_removed": "__asmeq(" not in legacy_scm,
        "legacy_scm_smc_preserved": (
            legacy_scm.count("asm volatile(") >= 3
            and count_smc_instructions(legacy_scm) >= 3
        ),
        "adreno_coresight_config_off_guarded": (
            "A52_PHASE14_CONFIG_OFF_CORESIGHT" in adreno_coresight
            and "#ifndef CONFIG_CORESIGHT" in adreno_coresight
            and "of_get_coresight_platform_data(" in adreno_coresight
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
        "scope": "the five compiler errors remaining after Workflow 116 run 8",
        "ion_macro_removals": remove_ion_flag_cached(gki),
        "qseecom_ion_flag_restored": restore_qseecom_ion_flag(gki),
        "qseecom_callback_maybe_unused": mark_qseecom_callback_maybe_unused(gki),
        "header_replacements": {
            "dma-contiguous.h": replace_legacy_header(
                gki,
                "dma-contiguous.h",
                ("include/linux/dma-contiguous.h",),
                ("linux/dma-mapping.h", "linux/cma.h"),
            ),
            "dma-debug.h": replace_legacy_header(
                gki,
                "dma-debug.h",
                ("include/linux/dma-debug.h", "kernel/dma/debug.h"),
                ("linux/dma-mapping.h",),
            ),
        },
        "legacy_scm_asmeq_removed": remove_legacy_scm_asmeq(gki),
        "adreno_coresight_config_off_guarded": guard_adreno_coresight_when_disabled(gki),
        "sde_crtc": patch_sde_crtc(gki),
        "fallbacks": [
            "Removed legacy public DMA headers are represented by compile-time compatibility shims when no Android 5.10 target exists.",
            "ION_FLAG_CACHED is restored only in qseecom's ion_kernel compatibility header with its legacy ABI value 1.",
            "The unused qseecom DMA-buffer destructor callback is retained with __maybe_unused and no runtime call path is changed.",
            "Legacy SCM __asmeq compile-time assertions are removed while fixed-register variables and SMC instructions are preserved.",
            "GPU CoreSight registration is skipped only when CONFIG_CORESIGHT is disabled; optional tracing remains unavailable in that configuration.",
            "The existing Workflow 115 diagnostic runtime fallbacks remain unvalidated.",
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
