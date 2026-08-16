#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root> <touchgrass-root>")

GKI = Path(sys.argv[1])
TG = Path(sys.argv[2])
REL = Path("include/uapi/drm/drm_mode.h")
GKI_HDR = GKI / REL
TG_HDR = TG / REL
MARKER = "A52_PHASE272_DRM_VENDOR_MODE_FLAG_PARITY_V1"

EXPECTED = {
    "DRM_MODE_FLAG_SUPPORTS_RGB": 23,
    "DRM_MODE_FLAG_SUPPORTS_YUV": 24,
    "DRM_MODE_FLAG_VID_MODE_PANEL": 29,
    "DRM_MODE_FLAG_CMD_MODE_PANEL": 30,
    "DRM_MODE_FLAG_SEAMLESS": 31,
}
ACCEPTED = (
    "DRM_MODE_FLAG_SUPPORTS_RGB",
    "DRM_MODE_FLAG_SUPPORTS_YUV",
    "DRM_MODE_FLAG_VID_MODE_PANEL",
    "DRM_MODE_FLAG_CMD_MODE_PANEL",
)


def macro_bit(text: str, name: str) -> int | None:
    m = re.search(
        rf"^\s*#\s*define\s+{re.escape(name)}\s+\(\s*1\s*<<\s*(\d+)\s*\)",
        text,
        re.MULTILINE,
    )
    return int(m.group(1)) if m else None


def macro_block_bounds(lines: list[str], name: str) -> tuple[int, int]:
    pat = re.compile(rf"^\s*#\s*define\s+{re.escape(name)}(?:\s|\()")
    for i, line in enumerate(lines):
        if not pat.search(line):
            continue
        j = i
        while lines[j].rstrip().endswith("\\"):
            j += 1
            if j >= len(lines):
                raise RuntimeError(f"unterminated {name} macro")
        return i, j
    raise RuntimeError(f"missing {name}")


def verify_exact_definitions(text: str, who: str) -> None:
    for name, bit in EXPECTED.items():
        got = macro_bit(text, name)
        if got != bit:
            raise RuntimeError(f"{who} {name}: expected bit {bit}, got {got}")


def verify_touchgrass(text: str) -> None:
    verify_exact_definitions(text, "TouchGrass")

    lines = text.splitlines()
    i, j = macro_block_bounds(lines, "DRM_MODE_FLAG_ALL")
    block = "\n".join(lines[i : j + 1])
    for name in ACCEPTED:
        if name not in block:
            raise RuntimeError(f"TouchGrass DRM_MODE_FLAG_ALL lacks {name}")
    if "DRM_MODE_FLAG_SEAMLESS" in block:
        raise RuntimeError("TouchGrass DRM_MODE_FLAG_ALL unexpectedly includes SEAMLESS")


def patch_gki(text: str) -> str:
    if MARKER in text:
        return text

    # Phase271 reconstruction already carries the Qualcomm/SDE flag definitions.
    # Verify their exact bit assignments instead of trying to redefine them.
    verify_exact_definitions(text, "GKI")

    lines = text.splitlines()
    i, j = macro_block_bounds(lines, "DRM_MODE_FLAG_ALL")
    block_text = "\n".join(lines[i : j + 1])

    if not any("DRM_MODE_FLAG_3D_MASK" in line for line in lines[i : j + 1]):
        raise RuntimeError("DRM_MODE_FLAG_ALL anchor lacks DRM_MODE_FLAG_3D_MASK")
    if "DRM_MODE_FLAG_SEAMLESS" in block_text:
        raise RuntimeError("GKI DRM_MODE_FLAG_ALL unexpectedly includes SEAMLESS")

    marker_lines = [
        f"/* {MARKER}",
        " * Qualcomm/SDE display mode flag definitions are already present in",
        " * the reconstructed Phase271 GKI header with the exact TouchGrass bits.",
        " * Restore only TouchGrass DRM_MODE_FLAG_ALL membership here.",
        " * SEAMLESS remains defined but intentionally excluded from the mask.",
        " */",
    ]
    lines[i:i] = marker_lines
    i += len(marker_lines)
    j += len(marker_lines)

    # Insert only missing TouchGrass-accepted flags immediately before the 3D
    # mask term. Preserve all existing DRM_MODE_FLAG_ALL formatting otherwise.
    current_block = "\n".join(lines[i : j + 1])
    missing = [name for name in ACCEPTED if name not in current_block]

    insert_at = None
    indent = "\t\t\t "
    for k in range(i, j + 1):
        if "DRM_MODE_FLAG_3D_MASK" in lines[k]:
            insert_at = k
            m = re.match(r"^(\s*)", lines[k])
            if m:
                indent = m.group(1)
            break
    if insert_at is None:
        raise RuntimeError("could not locate 3D mask insertion point")

    additions = [f"{indent}{name} |\\" for name in missing]
    lines[insert_at:insert_at] = additions
    return "\n".join(lines) + "\n"


def verify_patched(text: str) -> None:
    if MARKER not in text:
        raise RuntimeError("Phase272 marker missing after patch")
    verify_exact_definitions(text, "patched GKI")
    lines = text.splitlines()
    i, j = macro_block_bounds(lines, "DRM_MODE_FLAG_ALL")
    block = "\n".join(lines[i : j + 1])
    for name in ACCEPTED:
        if name not in block:
            raise RuntimeError(f"patched DRM_MODE_FLAG_ALL lacks {name}")
    if "DRM_MODE_FLAG_SEAMLESS" in block:
        raise RuntimeError("patched DRM_MODE_FLAG_ALL must not include SEAMLESS")


def main() -> None:
    if not GKI_HDR.is_file() or not TG_HDR.is_file():
        raise RuntimeError(f"missing header: {GKI_HDR} or {TG_HDR}")
    gki = GKI_HDR.read_text(encoding="utf-8")
    tg = TG_HDR.read_text(encoding="utf-8")
    verify_touchgrass(tg)
    patched = patch_gki(gki)
    verify_patched(patched)
    if patched != gki:
        GKI_HDR.write_text(patched, encoding="utf-8")
        print("Phase272 restored exact TouchGrass DRM_MODE_FLAG_ALL vendor membership")
    else:
        print("Phase272 DRM vendor mode-flag parity already present")
    print(MARKER)


if __name__ == "__main__":
    main()
