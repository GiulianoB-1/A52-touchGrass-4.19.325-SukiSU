#!/usr/bin/env python3
import argparse
from pathlib import Path

TARGETS = {
    "byte0": {
        "name": "disp_cc_mdss_byte0_clk_src",
        "ops": "clk_byte2_ops",
        "parent_map": "disp_cc_parent_map_1",
        "cmd": "0x10c4",
        "marker": "A52_PHASE324_BYTE0_PARENT_ENABLE_ONLY_V1",
    },
    "pclk0": {
        "name": "disp_cc_mdss_pclk0_clk_src",
        "ops": "clk_pixel_ops",
        "parent_map": "disp_cc_parent_map_5",
        "cmd": "0x1064",
        "marker": "A52_PHASE324_PCLK0_PARENT_ENABLE_ONLY_V1",
    },
}

ALL_EXPERIMENT_MARKERS = (
    "A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1",
    "A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1",
    "A52_PHASE323_DSI_PHY_PARENT_ENABLE_AB_V1",
    "A52_PHASE324_BYTE0_PARENT_ENABLE_ONLY_V1",
    "A52_PHASE324_PCLK0_PARENT_ENABLE_ONLY_V1",
)

BASE_FLAGS = ".flags = CLK_SET_RATE_PARENT | CLK_GET_RATE_NOCACHE,"
TEST_FLAGS = ".flags = CLK_SET_RATE_PARENT | CLK_GET_RATE_NOCACHE | CLK_OPS_PARENT_ENABLE,"


def get_block(text: str, name: str):
    head = f"static struct clk_rcg2 {name} = {{"
    start = text.find(head)
    if start < 0:
        raise SystemExit(f"Phase324: missing target {name}")
    if text.find(head, start + 1) >= 0:
        raise SystemExit(f"Phase324: duplicate target {name}")
    end = text.find("\n};", start)
    if end < 0:
        raise SystemExit(f"Phase324: unterminated target {name}")
    end += 3
    return start, end, text[start:end]


def validate_phase319_baseline(text: str):
    for marker in ALL_EXPERIMENT_MARKERS:
        if marker in text:
            raise SystemExit("Phase324: experiment marker leaked into Phase319 runtime baseline: " + marker)

    for key, cfg in TARGETS.items():
        _, _, block = get_block(text, cfg["name"])
        required = (
            f'.cmd_rcgr = {cfg["cmd"]},',
            f'.parent_map = {cfg["parent_map"]},',
            BASE_FLAGS,
            f'.ops = &{cfg["ops"]},',
        )
        for token in required:
            if block.count(token) != 1:
                raise SystemExit(f"Phase324: {key} baseline mismatch for {token!r}")
        if "CLK_OPS_PARENT_ENABLE" in block:
            raise SystemExit(f"Phase324: {key} is already parent-enabled")


def patch(text: str, target: str) -> str:
    validate_phase319_baseline(text)
    cfg = TARGETS[target]
    start, end, block = get_block(text, cfg["name"])
    exact_old = "\t\t" + BASE_FLAGS
    exact_new = "\t\t" + TEST_FLAGS
    if block.count(exact_old) != 1:
        raise SystemExit(f"Phase324: {target} expected one exact flags line")
    marker = (
        f'/* {cfg["marker"]}\n'
        f' * Diagnostic split of Phase323: enable parent only for {target.upper()}.\n'
        ' * No other clock, DSI, PHY, regulator, timing or MMIO behavior changes.\n'
        ' */\n'
    )
    head = f"static struct clk_rcg2 {cfg['name']} = {{"
    block2 = block.replace(head, marker + head, 1).replace(exact_old, exact_new, 1)
    return text[:start] + block2 + text[end:]


def validate(before: str, after: str, target: str):
    cfg = TARGETS[target]
    other = TARGETS["pclk0" if target == "byte0" else "byte0"]

    if after.count(cfg["marker"]) - before.count(cfg["marker"]) != 1:
        raise SystemExit("Phase324: target marker delta != 1")
    if other["marker"] in after:
        raise SystemExit("Phase324: non-target Phase324 marker present")
    if after.count("CLK_OPS_PARENT_ENABLE") - before.count("CLK_OPS_PARENT_ENABLE") != 1:
        raise SystemExit("Phase324: global CLK_OPS_PARENT_ENABLE delta != 1")

    for key, spec in TARGETS.items():
        _, _, bb = get_block(before, spec["name"])
        _, _, ab = get_block(after, spec["name"])
        if bb.count(f'.ops = &{spec["ops"]},') != 1 or ab.count(f'.ops = &{spec["ops"]},') != 1:
            raise SystemExit(f"Phase324: {key} specialized ops changed")
        if bb.count(f'.parent_map = {spec["parent_map"]},') != 1 or ab.count(f'.parent_map = {spec["parent_map"]},') != 1:
            raise SystemExit(f"Phase324: {key} parent_map changed")
        if bb.count(f'.cmd_rcgr = {spec["cmd"]},') != 1 or ab.count(f'.cmd_rcgr = {spec["cmd"]},') != 1:
            raise SystemExit(f"Phase324: {key} cmd_rcgr changed")
        if key == target:
            if ab.count(TEST_FLAGS) != 1:
                raise SystemExit(f"Phase324: {key} target flags mismatch")
        else:
            if "CLK_OPS_PARENT_ENABLE" in ab or ab.count(BASE_FLAGS) != 1:
                raise SystemExit(f"Phase324: {key} non-target moved")

    for token in (
        "clk_rcg2_shared_ops", ".safe_src_index", ".enable_safe_config",
        ".vdd_class", ".num_rate_max", ".rate_max", "devm_regulator_get(",
        "regulator_set_voltage(", "regulator_enable(", "regulator_disable(",
        "regmap_write(", "regmap_update_bits(", "clk_set_rate(", "clk_set_parent(",
        "clk_prepare_enable(", "clk_disable_unprepare(", "writel(", "writel_relaxed(",
        "udelay(", "ndelay(", "usleep_range(", "msleep(", "reset_control_",
    ):
        if after.count(token) != before.count(token):
            raise SystemExit("Phase324: forbidden functional/framework delta: " + token)


def check(text: str, target: str):
    cfg = TARGETS[target]
    other_key = "pclk0" if target == "byte0" else "byte0"
    other = TARGETS[other_key]

    if text.count(cfg["marker"]) != 1:
        raise SystemExit("Phase324 check: target marker count != 1")
    for marker in ALL_EXPERIMENT_MARKERS:
        if marker != cfg["marker"] and marker in text:
            raise SystemExit("Phase324 check: forbidden experiment marker present: " + marker)

    _, _, target_block = get_block(text, cfg["name"])
    _, _, other_block = get_block(text, other["name"])
    if target_block.count(TEST_FLAGS) != 1:
        raise SystemExit("Phase324 check: target flags mismatch")
    if target_block.count(f'.ops = &{cfg["ops"]},') != 1:
        raise SystemExit("Phase324 check: target specialized ops mismatch")
    if "CLK_OPS_PARENT_ENABLE" in other_block or other_block.count(BASE_FLAGS) != 1:
        raise SystemExit("Phase324 check: non-target flags moved")
    if other_block.count(f'.ops = &{other["ops"]},') != 1:
        raise SystemExit("Phase324 check: non-target specialized ops mismatch")

    print(f"Phase324 {target} parent-enable-only check: PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--target", required=True, choices=sorted(TARGETS))
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    p = Path(args.file)
    text = p.read_text()
    if args.check_only:
        check(text, args.target)
        return
    after = patch(text, args.target)
    validate(text, after, args.target)
    p.write_text(after)
    print(f"Phase324 {args.target} parent-enable-only patch: PASS")


if __name__ == "__main__":
    main()
