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


def verify_touchgrass(text: str) -> None:
    for name, bit in EXPECTED.items():
        got = macro_bit(text, name)
        if got != bit:
            raise RuntimeError(f"TouchGrass {name}: expected bit {bit}, got {got}")

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

    for name in EXPECTED:
        if re.search(rf"^\s*#\s*define\s+{re.escape(name)}\b", text, re.MULTILINE):
            raise RuntimeError(f"GKI already defines {name}; refusing ambiguous partial parity patch")

    lines = text.splitlines()
    i, j = macro_block_bounds(lines, "DRM_MODE_FLAG_ALL")
    block = lines[i : j + 1]

    if not any("DRM_MODE_FLAG_3D_MASK" in line for line in block):
        raise RuntimeError("DRM_MODE_FLAG_ALL anchor lacks DRM_MODE_FLAG_3D_MASK")
    if any(name in "\n".join(block) for name in EXPECTED):
        raise RuntimeError("DRM_MODE_FLAG_ALL already contains vendor flag text")

    defs = [
        "",
        f"/* {MARKER}",
        " * Qualcomm/SDE display mode flags carried by the A52 vendor display stack.",
        " * Numeric values and DRM_MODE_FLAG_ALL membership match the exact",
        " * TouchGrass A52 4.19 reference. SEAMLESS is defined but intentionally",
        " * not accepted by DRM_MODE_FLAG_ALL, matching that reference.",
        " */",
        "#define DRM_MODE_FLAG_SUPPORTS_RGB\t\t(1<<23)",
        "#define DRM_MODE_FLAG_SUPPORTS_YUV\t\t(1<<24)",
        "#define DRM_MODE_FLAG_VID_MODE_PANEL\t(1<<29)",
        "#define DRM_MODE_FLAG_CMD_MODE_PANEL\t(1<<30)",
        "#define DRM_MODE_FLAG_SEAMLESS\t\t\t(1<<31)",
        "",
    ]
    lines[i:i] = defs
    i += len(defs)
    j += len(defs)

    # Insert the four TouchGrass-accepted vendor flags immediately before the
    # final 3D mask term. Preserve the existing macro formatting otherwise.
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

    additions = [f"{indent}{name} |\\" for name in ACCEPTED]
    lines[insert_at:insert_at] = additions
    return "\n".join(lines) + "\n"


def verify_patched(text: str) -> None:
    if MARKER not in text:
        raise RuntimeError("Phase272 marker missing after patch")
    for name, bit in EXPECTED.items():
        got = macro_bit(text, name)
        if got != bit:
            raise RuntimeError(f"patched GKI {name}: expected bit {bit}, got {got}")
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
        print("Phase272 applied exact TouchGrass DRM vendor mode-flag parity")
    else:
        print("Phase272 DRM vendor mode-flag parity already present")
    print(MARKER)


if __name__ == "__main__":
    main()
