#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
MARK = "A52_PHASE329_DSI_DEBUGBUS_SELECTOR_READBACK_V1"
OLD_DECL = "\tu32 saved, restored_ctl, restored_status, i;\n\tu32 v[6];\n"
NEW_DECL = "\tu32 saved, restored_ctl, restored_status, i;\n\tu32 v[6], ctl[6];\n"
OLD_LOOP = """\tfor (i = 0; i < 6; i++) {\n\t\tDSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p319_selectors[i]);\n\t\twmb();\n\t\tv[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);\n\t}\n"""
NEW_LOOP = """\tfor (i = 0; i < 6; i++) {\n\t\tDSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p319_selectors[i]);\n\t\twmb();\n\t\tctl[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);\n\t\tv[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);\n\t}\n"""
OLD_RECORD = """\ta52_ackfr_record(\"P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x\",\n\t\tpoint, saved, v[0], v[1], v[2], v[3], v[4], v[5], restored_status,\n\t\trestored_ctl);\n"""
NEW_RECORD = OLD_RECORD + """\ta52_ackfr_record(\"P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x\",\n\t\tpoint, ctl[0], ctl[1], ctl[2], ctl[3], ctl[4], ctl[5]);\n"""


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase329 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1" not in text:
        raise SystemExit("Phase329 requires the Phase319 six-selector observer")
    # Insert a marker in the existing observer comment. No control-flow or
    # hardware behavior changes beyond reading back the selector register.
    anchor = "/* A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1\n"
    text = one(text, anchor,
               anchor + " * A52_PHASE329_DSI_DEBUGBUS_SELECTOR_READBACK_V1\n",
               "marker")
    text = one(text, OLD_DECL, NEW_DECL, "local readback array")
    text = one(text, OLD_LOOP, NEW_LOOP, "selector readback")
    text = one(text, OLD_RECORD, NEW_RECORD, "recorder marker")
    return text


def validate(text: str) -> None:
    required = (
        MARK,
        "A52_PHASE319_DSI_SIXPOINT_TEMPORAL_OBSERVER_V1",
        "ctl[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);",
        "P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x",
        "P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x",
        "0x0171, 0x0181, 0x0191, 0x01a1, 0x01e1, 0x0211",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase329 required token missing: " + token)
    if text.count("ctl[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);") != 1:
        raise SystemExit("Phase329 selector CTL readback count is not exactly one")
    if text.count("P276 329C q=%u") != 1:
        raise SystemExit("Phase329 recorder marker count is not exactly one")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    path = ns.root / HWC
    if not path.is_file():
        raise SystemExit("Phase329 source missing: " + str(path))
    text = path.read_text()
    if not ns.check_only:
        text = patch(text)
        path.write_text(text)
    validate(path.read_text())
    print("Phase329 DSI debug-bus selector readback observer: PASS")


if __name__ == "__main__":
    main()
