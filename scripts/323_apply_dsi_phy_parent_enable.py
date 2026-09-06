#!/usr/bin/env python3
import argparse
from pathlib import Path

MARKER = "A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1"
TARGETS = {
    "disp_cc_mdss_byte0_clk_src": "clk_byte2_ops",
    "disp_cc_mdss_pclk0_clk_src": "clk_pixel_ops",
}


def get_block(text: str, name: str):
    head = f"static struct clk_rcg2 {name} = {{"
    start = text.find(head)
    if start < 0:
        raise SystemExit(f"Phase323: missing target {name}")
    if text.find(head, start + 1) >= 0:
        raise SystemExit(f"Phase323: duplicate target {name}")
    end = text.find("\n};", start)
    if end < 0:
        raise SystemExit(f"Phase323: unterminated target {name}")
    end += 3
    return start, end, text[start:end]


def validate_baseline(text: str):
    if MARKER in text:
        raise SystemExit("Phase323: marker already present")
    for forbidden in (
        "A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1",
        "A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1",
    ):
        if forbidden in text:
            raise SystemExit("Phase323: prior experiment leaked into runtime baseline: " + forbidden)

    expected = {
        "disp_cc_mdss_byte0_clk_src": (
            ".cmd_rcgr = 0x10c4,",
            ".parent_map = disp_cc_parent_map_1,",
            ".flags = CLK_SET_RATE_PARENT | CLK_GET_RATE_NOCACHE,",
            ".ops = &clk_byte2_ops,",
        ),
        "disp_cc_mdss_pclk0_clk_src": (
            ".cmd_rcgr = 0x1064,",
            ".parent_map = disp_cc_parent_map_5,",
            ".flags = CLK_SET_RATE_PARENT | CLK_GET_RATE_NOCACHE,",
            ".ops = &clk_pixel_ops,",
        ),
    }
    for name, required in expected.items():
        _, _, block = get_block(text, name)
        for token in required:
            if block.count(token) != 1:
                raise SystemExit(f"Phase323: {name} baseline mismatch for {token!r}")
        if "CLK_OPS_PARENT_ENABLE" in block:
            raise SystemExit(f"Phase323: {name} already has CLK_OPS_PARENT_ENABLE")


def patch_target(text: str, name: str) -> str:
    start, end, block = get_block(text, name)
    old = "\t\t.flags = CLK_SET_RATE_PARENT | CLK_GET_RATE_NOCACHE,"
    new = "\t\t.flags = CLK_SET_RATE_PARENT | CLK_GET_RATE_NOCACHE | CLK_OPS_PARENT_ENABLE,"
    if block.count(old) != 1:
        raise SystemExit(f"Phase323: {name} expected one exact flags line")
    block2 = block.replace(old, new, 1)
    return text[:start] + block2 + text[end:]


def apply(text: str) -> str:
    validate_baseline(text)
    first = "static struct clk_rcg2 disp_cc_mdss_byte0_clk_src = {"
    marker = (
        "/* " + MARKER + "\n"
        " * Keep DSI PHY parents enabled while BYTE0/PCLK0 specialized RCG\n"
        " * set_rate/set_parent callbacks program their source registers.\n"
        " */\n"
    )
    if text.count(first) != 1:
        raise SystemExit("Phase323: BYTE0 anchor count != 1")
    out = text.replace(first, marker + first, 1)
    for name in TARGETS:
        out = patch_target(out, name)
    return out


def validate(before: str, after: str):
    if after.count(MARKER) - before.count(MARKER) != 1:
        raise SystemExit("Phase323: marker delta != 1")
    if after.count("CLK_OPS_PARENT_ENABLE") - before.count("CLK_OPS_PARENT_ENABLE") != 2:
        raise SystemExit("Phase323: global CLK_OPS_PARENT_ENABLE delta != 2")

    for name, ops in TARGETS.items():
        _, _, bb = get_block(before, name)
        _, _, ab = get_block(after, name)
        if "CLK_OPS_PARENT_ENABLE" in bb:
            raise SystemExit(f"Phase323: {name} baseline unexpectedly parent-enabled")
        if ab.count("CLK_OPS_PARENT_ENABLE") != 1:
            raise SystemExit(f"Phase323: {name} parent-enable count != 1")
        if bb.count(f".ops = &{ops},") != 1 or ab.count(f".ops = &{ops},") != 1:
            raise SystemExit(f"Phase323: {name} specialized ops changed")

    for token in (
        "clk_rcg2_shared_ops",
        ".safe_src_index",
        ".enable_safe_config",
        ".vdd_class",
        ".num_rate_max",
        ".rate_max",
        "devm_regulator_get(",
        "regulator_set_voltage(",
        "regulator_enable(",
        "regulator_disable(",
        "regmap_write(",
        "regmap_update_bits(",
        "clk_set_rate(",
        "clk_set_parent(",
        "clk_prepare_enable(",
        "clk_disable_unprepare(",
        "writel(",
        "writel_relaxed(",
        "udelay(",
        "ndelay(",
        "usleep_range(",
        "msleep(",
        "reset_control_",
    ):
        if after.count(token) != before.count(token):
            raise SystemExit("Phase323: forbidden functional/framework delta: " + token)


def check(text: str):
    if text.count(MARKER) != 1:
        raise SystemExit("Phase323 check: marker count != 1")
    for forbidden in (
        "A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1",
        "A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1",
    ):
        if forbidden in text:
            raise SystemExit("Phase323 check: prior experiment marker present: " + forbidden)
    for name, ops in TARGETS.items():
        _, _, block = get_block(text, name)
        wanted = ".flags = CLK_SET_RATE_PARENT | CLK_GET_RATE_NOCACHE | CLK_OPS_PARENT_ENABLE,"
        if block.count(wanted) != 1:
            raise SystemExit(f"Phase323 check: {name} flags mismatch")
        if block.count(f".ops = &{ops},") != 1:
            raise SystemExit(f"Phase323 check: {name} specialized ops mismatch")
    print("Phase323 DSI PHY parent-enable check: PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    p = Path(args.file)
    text = p.read_text()
    if args.check_only:
        check(text)
        return
    after = apply(text)
    validate(text, after)
    p.write_text(after)
    print("Phase323 DSI PHY parent-enable patch: PASS")


if __name__ == "__main__":
    main()
