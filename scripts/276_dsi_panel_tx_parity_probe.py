#!/usr/bin/env python3
from __future__ import annotations

import difflib
import hashlib
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root> <touchgrass-root>")

ROOT = Path(sys.argv[1])
TG = Path(sys.argv[2])
GKI = ROOT / "drivers/a52_display/msm/dsi/dsi_panel.c"
TGF = TG / "techpack/display/msm/dsi/dsi_panel.c"
OUT = Path("phase276-dsi-panel-tx-parity-before.txt")
FUNC = "dsi_panel_tx_cmd_set"
START = "#if defined(CONFIG_DISPLAY_SAMSUNG)\nint dsi_panel_tx_cmd_set(struct dsi_panel *panel,"


def extract(text: str) -> str:
    start = text.find(START)
    if start < 0:
        raise RuntimeError("Samsung dsi_panel_tx_cmd_set start not found")
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError("dsi_panel_tx_cmd_set opening brace not found")
    depth = 0
    in_str = in_chr = esc = False
    for i in range(brace, len(text)):
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
    raise RuntimeError("unterminated dsi_panel_tx_cmd_set")


g = extract(GKI.read_text(encoding="utf-8", errors="strict"))
t = extract(TGF.read_text(encoding="utf-8", errors="strict"))
match = g == t
sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
lines = [
    f"function={FUNC}",
    f"gki_path={GKI}",
    f"touchgrass_path={TGF}",
    f"gki_sha256={sha(g)}",
    f"touchgrass_sha256={sha(t)}",
    f"exact_match={1 if match else 0}",
]
if not match:
    lines.append("--- touchgrass-vs-gki-diff ---")
    lines.extend(difflib.unified_diff(
        t.splitlines(), g.splitlines(),
        fromfile="TouchGrass:dsi_panel_tx_cmd_set",
        tofile="GKI:dsi_panel_tx_cmd_set",
        lineterm="",
    ))
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(OUT.read_text(), end="")
raise SystemExit(0 if match else 2)
