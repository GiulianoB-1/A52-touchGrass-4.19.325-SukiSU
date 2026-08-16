#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root> <touchgrass-root>")

gki = Path(sys.argv[1])
tg = Path(sys.argv[2])

def read(root: Path, rel: str) -> str:
    p = root / rel
    if not p.is_file():
        raise RuntimeError(f"missing source file: {p}")
    return p.read_text(encoding="utf-8", errors="strict")

def macro_block(text: str, name: str) -> str:
    lines = text.splitlines()
    pat = re.compile(rf"^\s*#\s*define\s+{re.escape(name)}(?:\s|\()")
    for i, line in enumerate(lines):
        if pat.search(line):
            out = [line]
            j = i
            while out[-1].rstrip().endswith("\\"):
                j += 1
                if j >= len(lines):
                    break
                out.append(lines[j])
            return "\n".join(out)
    return "<ABSENT>"

def enum_mode_bad(text: str) -> str:
    for line in text.splitlines():
        if "MODE_BAD" in line:
            return line.strip()
    return "<ABSENT>"

def validate_driver_body(text: str) -> str:
    needle = "drm_mode_validate_driver("
    pos = text.find(needle)
    if pos < 0:
        return "<ABSENT>"
    start = text.rfind("\n", 0, pos) + 1
    brace = text.find("{", pos)
    if brace < 0:
        return "<MALFORMED>"
    depth = 0
    end = brace
    for i in range(brace, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return text[start:end]

hdr_rel = "include/uapi/drm/drm_mode.h"
modes_rel = "drivers/gpu/drm/drm_modes.c"

gki_hdr = read(gki, hdr_rel)
tg_hdr = read(tg, hdr_rel)
gki_modes = read(gki, modes_rel)
tg_modes = read(tg, modes_rel)

names = [
    "DRM_MODE_FLAG_SUPPORTS_RGB",
    "DRM_MODE_FLAG_SUPPORTS_YUV",
    "DRM_MODE_FLAG_VID_MODE_PANEL",
    "DRM_MODE_FLAG_CMD_MODE_PANEL",
    "DRM_MODE_FLAG_SEAMLESS",
    "DRM_MODE_FLAG_ALL",
]

out = []
out.append("PHASE272 DRM FLAG PARITY PROBE")
out.append(f"GKI={gki.resolve()}")
out.append(f"TOUCHGRASS={tg.resolve()}")
out.append("")
for name in names:
    out.append(f"=== GKI {name} ===")
    out.append(macro_block(gki_hdr, name))
    out.append(f"=== TOUCHGRASS {name} ===")
    out.append(macro_block(tg_hdr, name))
    out.append("")
out.append("=== GKI MODE_BAD ===")
out.append(enum_mode_bad(gki_hdr))
out.append("=== TOUCHGRASS MODE_BAD ===")
out.append(enum_mode_bad(tg_hdr))
out.append("")
out.append("=== GKI drm_mode_validate_driver ===")
out.append(validate_driver_body(gki_modes))
out.append("")
out.append("=== TOUCHGRASS drm_mode_validate_driver ===")
out.append(validate_driver_body(tg_modes))
out.append("")

gki_all = macro_block(gki_hdr, "DRM_MODE_FLAG_ALL")
tg_all = macro_block(tg_hdr, "DRM_MODE_FLAG_ALL")
gki_cmd = macro_block(gki_hdr, "DRM_MODE_FLAG_CMD_MODE_PANEL")
tg_cmd = macro_block(tg_hdr, "DRM_MODE_FLAG_CMD_MODE_PANEL")

summary = {
    "gki_cmd_macro_present": gki_cmd != "<ABSENT>",
    "tg_cmd_macro_present": tg_cmd != "<ABSENT>",
    "gki_all_mentions_cmd": "DRM_MODE_FLAG_CMD_MODE_PANEL" in gki_all or "(1<<30)" in gki_all or "(1 << 30)" in gki_all,
    "tg_all_mentions_cmd": "DRM_MODE_FLAG_CMD_MODE_PANEL" in tg_all or "(1<<30)" in tg_all or "(1 << 30)" in tg_all,
    "gki_basic_unknown_flag_check": "mode->flags & ~DRM_MODE_FLAG_ALL" in gki_modes,
    "tg_basic_unknown_flag_check": "mode->flags & ~DRM_MODE_FLAG_ALL" in tg_modes,
}
out.append("=== SUMMARY ===")
for k, v in summary.items():
    out.append(f"{k}={int(v)}")

Path("phase272-probe.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))

if not summary["tg_cmd_macro_present"]:
    raise SystemExit("TouchGrass reference unexpectedly lacks DRM_MODE_FLAG_CMD_MODE_PANEL")
if not summary["tg_all_mentions_cmd"]:
    raise SystemExit("TouchGrass reference unexpectedly excludes CMD_MODE_PANEL from DRM_MODE_FLAG_ALL")
if not summary["gki_basic_unknown_flag_check"]:
    raise SystemExit("Pinned GKI no longer has the expected DRM_MODE_FLAG_ALL basic-validation gate")

print("PHASE272_SOURCE_PARITY_PROBE=PASS")
