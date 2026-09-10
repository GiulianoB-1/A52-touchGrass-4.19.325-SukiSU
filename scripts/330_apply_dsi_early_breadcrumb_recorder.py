#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
MARK = "A52_PHASE330_DSI_EARLY_BREADCRUMB_RECORDER_V1"

OLD_MARK = " * A52_PHASE329_DSI_DEBUGBUS_SELECTOR_READBACK_V1\n"
NEW_MARK = OLD_MARK + " * A52_PHASE330_DSI_EARLY_BREADCRUMB_RECORDER_V1\n"

OLD_ENTRY = """\tif (!a52_p293_gdm_trace_active() || !ctrl || !ctrl->base)\n\t\treturn;\n\n\tsaved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);\n"""
NEW_ENTRY = """\tif (!a52_p293_gdm_trace_active() || !ctrl || !ctrl->base)\n\t\treturn;\n\n\ta52_ackfr_record(\"P276 330A q=%u\", point);\n\tsaved = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);\n\ta52_ackfr_record(\"P276 330B q=%u c=%x\", point, saved);\n"""

OLD_LOOP = """\tfor (i = 0; i < 6; i++) {\n\t\tDSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p319_selectors[i]);\n\t\twmb();\n\t\tctl[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);\n\t\tv[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);\n\t}\n"""
NEW_LOOP = """\tfor (i = 0; i < 6; i++) {\n\t\tDSI_W32(ctrl, DSI_DEBUG_BUS_CTL, a52_p319_selectors[i]);\n\t\twmb();\n\t\tctl[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);\n\t\tv[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);\n\t\ta52_ackfr_record(\"P276 330C q=%u i=%u c=%x v=%x\",\n\t\t\tpoint, i, ctl[i], v[i]);\n\t}\n"""

OLD_RESTORE = """\tDSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);\n\twmb();\n\trestored_ctl = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);\n\trestored_status = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);\n\n"""
NEW_RESTORE = """\tDSI_W32(ctrl, DSI_DEBUG_BUS_CTL, saved);\n\twmb();\n\trestored_ctl = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);\n\trestored_status = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);\n\ta52_ackfr_record(\"P276 330D q=%u c=%x z=%x\",\n\t\tpoint, restored_ctl, restored_status);\n\n"""


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase330 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE329_DSI_DEBUGBUS_SELECTOR_READBACK_V1" not in text:
        raise SystemExit("Phase330 requires the Phase329 selector readback observer")
    text = one(text, OLD_MARK, NEW_MARK, "marker")
    text = one(text, OLD_ENTRY, NEW_ENTRY, "entry breadcrumbs")
    text = one(text, OLD_LOOP, NEW_LOOP, "per-selector breadcrumbs")
    text = one(text, OLD_RESTORE, NEW_RESTORE, "restore breadcrumb")
    return text


def validate(text: str) -> None:
    required = (
        MARK,
        "A52_PHASE329_DSI_DEBUGBUS_SELECTOR_READBACK_V1",
        "P276 330A q=%u",
        "P276 330B q=%u c=%x",
        "P276 330C q=%u i=%u c=%x v=%x",
        "P276 330D q=%u c=%x z=%x",
        "P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x",
        "ctl[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_CTL);",
        "v[i] = DSI_R32(ctrl, DSI_DEBUG_BUS_STATUS);",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase330 required token missing: " + token)
    for token in ("P276 330A q=%u", "P276 330B q=%u c=%x", "P276 330C q=%u i=%u c=%x v=%x", "P276 330D q=%u c=%x z=%x"):
        if text.count(token) != 1:
            raise SystemExit(f"Phase330 marker count for {token!r} is not exactly one")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    path = ns.root / HWC
    if not path.is_file():
        raise SystemExit("Phase330 source missing: " + str(path))
    text = path.read_text()
    if not ns.check_only:
        text = patch(text)
        path.write_text(text)
    validate(path.read_text())
    print("Phase330 DSI early breadcrumb recorder: PASS")


if __name__ == "__main__":
    main()
