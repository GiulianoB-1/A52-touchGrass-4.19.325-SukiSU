#!/usr/bin/env python3
from __future__ import annotations

import difflib
import hashlib
import re
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root> <touchgrass-root>")

ROOT = Path(sys.argv[1])
TG = Path(sys.argv[2])
GKI_FILE = ROOT / "drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"
TG_FILE = TG / "techpack/display/msm/samsung/ss_dsi_panel_common.c"
FUNC = "ss_panel_data_read_gpara"
OUT = Path("phase274-gpara-parity-before.txt")


def extract_function(text: str, name: str) -> str:
    m = re.search(r"\bint\s+" + re.escape(name) + r"\s*\([^;]*?\)\s*\{", text, re.S)
    if not m:
        raise RuntimeError(f"function not found: {name}")
    start = m.start()
    brace = text.find("{", m.start(), m.end())
    depth = 0
    in_str = False
    in_chr = False
    esc = False
    i = brace
    while i < len(text):
        c = text[i]
        if esc:
            esc = False
        elif c == "\\" and (in_str or in_chr):
            esc = True
        elif c == '"' and not in_chr:
            in_str = not in_str
        elif c == "'" and not in_str:
            in_chr = not in_chr
        elif not in_str and not in_chr:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    raise RuntimeError(f"unterminated function: {name}")


def normalize_gki(fn: str) -> str:
    fn = re.sub(
        r'^\s*A52_ACKFR_SCOPE\("DISP",\s*"a52\.ss_panel_data_read_gpara"\);\s*\n',
        '', fn, flags=re.M,
    )
    return fn


gki_text = GKI_FILE.read_text(encoding="utf-8", errors="strict")
tg_text = TG_FILE.read_text(encoding="utf-8", errors="strict")
gki_fn_raw = extract_function(gki_text, FUNC)
tg_fn = extract_function(tg_text, FUNC)
gki_fn = normalize_gki(gki_fn_raw)
match = gki_fn == tg_fn


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


lines = [
    f"function={FUNC}",
    f"gki_path={GKI_FILE}",
    f"touchgrass_path={TG_FILE}",
    f"gki_raw_sha256={sha(gki_fn_raw)}",
    f"gki_normalized_sha256={sha(gki_fn)}",
    f"touchgrass_sha256={sha(tg_fn)}",
    f"match_after_removing_existing_scope={1 if match else 0}",
]
if not match:
    lines.append("--- normalized-gki-vs-touchgrass-diff ---")
    lines.extend(difflib.unified_diff(
        tg_fn.splitlines(), gki_fn.splitlines(),
        fromfile="TouchGrass", tofile="GKI-normalized", lineterm=""
    ))
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(OUT.read_text(), end="")
raise SystemExit(0 if match else 2)
