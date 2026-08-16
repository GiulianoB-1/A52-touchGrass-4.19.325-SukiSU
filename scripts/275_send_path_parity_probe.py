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
PAIRS = [
    (
        "ss_send_cmd",
        ROOT / "drivers/a52_display/msm/samsung/ss_dsi_panel_common.c",
        TG / "techpack/display/msm/samsung/ss_dsi_panel_common.c",
    ),
    (
        "ss_wrapper_dsi_panel_tx_cmd_set",
        ROOT / "drivers/a52_display/msm/samsung/ss_wrapper_common.c",
        TG / "techpack/display/msm/samsung/ss_wrapper_common.c",
    ),
]
OUT = Path("phase275-send-path-parity-before.txt")


def extract_function(text: str, name: str) -> str:
    # Source definitions may carry qualifiers such as __mockable. Require a
    # definition opening, not a prototype, and then brace-match the body.
    pattern = (r"^[ \t]*(?:static[ \t]+)?int(?:[ \t]+__mockable)?[ \t]+"
               + re.escape(name) + r"\s*\([^;]*?\)\s*\{")
    matches = list(re.finditer(pattern, text, re.M | re.S))
    if len(matches) != 1:
        raise RuntimeError(f"definition count for {name}: {len(matches)}")
    m = matches[0]
    start = m.start()
    brace = text.find("{", m.start(), m.end())
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
    raise RuntimeError(f"unterminated function: {name}")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


lines: list[str] = []
all_match = True
for name, gki_path, tg_path in PAIRS:
    gki = extract_function(gki_path.read_text(encoding="utf-8", errors="strict"), name)
    tg = extract_function(tg_path.read_text(encoding="utf-8", errors="strict"), name)
    if name == "ss_send_cmd":
        # Existing pre-P275 display recorder calls are observational. Remove
        # only those two known calls before comparing the Samsung function.
        gki = re.sub(
            r'^\s*a52_ackfr_record\("DISP SS_CMD start[^;]*?;\s*\n',
            '', gki, flags=re.M | re.S,
        )
        gki = re.sub(
            r'^\s*a52_ackfr_record\("DISP SS_CMD done[^;]*?;\s*\n',
            '', gki, flags=re.M | re.S,
        )
    match = gki == tg
    all_match &= match
    lines += [
        f"function={name}",
        f"gki_path={gki_path}",
        f"touchgrass_path={tg_path}",
        f"gki_sha256={sha(gki)}",
        f"touchgrass_sha256={sha(tg)}",
        f"exact_match={1 if match else 0}",
    ]
    if not match:
        lines.append(f"--- {name}: touchgrass-vs-gki-diff ---")
        lines.extend(difflib.unified_diff(
            tg.splitlines(), gki.splitlines(),
            fromfile=f"TouchGrass:{name}", tofile=f"GKI:{name}", lineterm="",
        ))
    lines.append("")

lines.append(f"all_exact_match={1 if all_match else 0}")
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(OUT.read_text(), end="")
raise SystemExit(0 if all_match else 2)
