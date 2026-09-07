#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def function(text: str, name: str) -> str:
    needle = name + "("
    pos = text.find(needle)
    if pos < 0:
        return f"<missing {name}>\n"
    start = text.rfind("\n", 0, pos) + 1
    brace = text.find("{", pos)
    if brace < 0:
        return f"<unterminated {name}>\n"
    depth = 0
    i = brace
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1] + "\n"
        i += 1
    return f"<unterminated {name}>\n"


def struct_block(text: str, name: str) -> str:
    needle = f"static struct clk_rcg2 {name} = {{"
    start = text.find(needle)
    if start < 0:
        return f"<missing {name}>\n"
    end = text.find("\n};", start)
    if end < 0:
        return f"<unterminated {name}>\n"
    return text[start:end + 3] + "\n"


def yn(v: bool) -> str:
    return "yes" if v else "no"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gki", type=Path, required=True)
    ap.add_argument("--tg", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    gki_rcg2 = (args.gki / "drivers/clk/qcom/clk-rcg2.c").read_text()
    gki_rcgh = (args.gki / "drivers/clk/qcom/clk-rcg.h").read_text()
    gki_disp = (args.gki / "drivers/clk/qcom/dispcc-lagoon.c").read_text()
    tg_rcg2 = (args.tg / "drivers/clk/qcom/clk-rcg2.c").read_text()
    tg_rcgh = (args.tg / "drivers/clk/qcom/clk-rcg.h").read_text()
    tg_disp = (args.tg / "drivers/clk/qcom/dispcc-lagoon.c").read_text()

    args.out.mkdir(parents=True, exist_ok=True)

    names = [
        "clk_rcg2_current_config",
        "clk_byte2_set_rate",
        "clk_pixel_set_rate",
        "clk_rcg2_enable",
        "clk_rcg2_disable",
        "clk_rcg2_shared_enable",
        "clk_rcg2_shared_disable",
    ]

    report = []
    report.append("PHASE325 TOUCHGRASS -> GKI RCG SEMANTIC AUDIT\n")
    report.append("FACTS\n")
    facts = {
        "tg_struct_enable_safe_config": "enable_safe_config" in tg_rcgh,
        "gki_struct_enable_safe_config": "enable_safe_config" in gki_rcgh,
        "gki_struct_safe_src_index": "safe_src_index" in gki_rcgh,
        "gki_struct_parked_cfg": "parked_cfg" in gki_rcgh,
        "tg_has_current_config_helper": "clk_rcg2_current_config(" in tg_rcg2,
        "gki_has_current_config_helper": "clk_rcg2_current_config(" in gki_rcg2,
        "tg_byte2_calls_current_config": "clk_rcg2_current_config(rcg, &f)" in function(tg_rcg2, "clk_byte2_set_rate"),
        "gki_byte2_calls_current_config": "clk_rcg2_current_config(rcg, &f)" in function(gki_rcg2, "clk_byte2_set_rate"),
        "tg_pixel_calls_current_config": "clk_rcg2_current_config(rcg, &f)" in function(tg_rcg2, "clk_pixel_set_rate"),
        "gki_pixel_calls_current_config": "clk_rcg2_current_config(rcg, &f)" in function(gki_rcg2, "clk_pixel_set_rate"),
        "tg_byte0_safe_config": "enable_safe_config = true" in struct_block(tg_disp, "disp_cc_mdss_byte0_clk_src"),
        "tg_pclk0_safe_config": "enable_safe_config = true" in struct_block(tg_disp, "disp_cc_mdss_pclk0_clk_src"),
        "gki_byte0_parent_enable": "CLK_OPS_PARENT_ENABLE" in struct_block(gki_disp, "disp_cc_mdss_byte0_clk_src"),
        "gki_pclk0_parent_enable": "CLK_OPS_PARENT_ENABLE" in struct_block(gki_disp, "disp_cc_mdss_pclk0_clk_src"),
    }
    for k, v in facts.items():
        report.append(f"{k}={yn(v)}\n")

    report.append("\nTARGET BLOCKS\n")
    for target in ("disp_cc_mdss_byte0_clk_src", "disp_cc_mdss_pclk0_clk_src", "disp_cc_mdss_esc0_clk_src"):
        report.append(f"\n--- TouchGrass {target} ---\n")
        report.append(struct_block(tg_disp, target))
        report.append(f"\n--- GKI {target} ---\n")
        report.append(struct_block(gki_disp, target))

    report.append("\nFUNCTIONS\n")
    for name in names:
        report.append(f"\n===== TouchGrass {name} =====\n")
        report.append(function(tg_rcg2, name))
        report.append(f"\n===== GKI {name} =====\n")
        report.append(function(gki_rcg2, name))

    # Decision-grade summary. These are observations, not assumptions.
    report.append("\nDECISION\n")
    if facts["tg_byte2_calls_current_config"] and not facts["gki_byte2_calls_current_config"]:
        report.append("PROVEN_DELTA_BYTE2=TouchGrass skips redundant RCG update; GKI does not.\n")
    else:
        report.append("PROVEN_DELTA_BYTE2=not-proven\n")
    if facts["tg_pixel_calls_current_config"] and not facts["gki_pixel_calls_current_config"]:
        report.append("PROVEN_DELTA_PIXEL=TouchGrass skips redundant RCG update; GKI does not.\n")
    else:
        report.append("PROVEN_DELTA_PIXEL=not-proven\n")
    if facts["tg_struct_enable_safe_config"] and not facts["gki_struct_enable_safe_config"]:
        report.append("PROVEN_DELTA_SAFE_LIFECYCLE=TouchGrass enable_safe_config API absent from GKI struct; requires semantic mapping, not field copy.\n")
    else:
        report.append("PROVEN_DELTA_SAFE_LIFECYCLE=not-proven\n")

    out = "".join(report)
    (args.out / "REPORT.txt").write_text(out)
    print(out)

    # Save exact sources used so the audit is independently reviewable.
    for label, src in (
        ("gki-clk-rcg2.c", args.gki / "drivers/clk/qcom/clk-rcg2.c"),
        ("gki-clk-rcg.h", args.gki / "drivers/clk/qcom/clk-rcg.h"),
        ("gki-dispcc-lagoon.c", args.gki / "drivers/clk/qcom/dispcc-lagoon.c"),
        ("tg-clk-rcg2.c", args.tg / "drivers/clk/qcom/clk-rcg2.c"),
        ("tg-clk-rcg.h", args.tg / "drivers/clk/qcom/clk-rcg.h"),
        ("tg-dispcc-lagoon.c", args.tg / "drivers/clk/qcom/dispcc-lagoon.c"),
    ):
        (args.out / label).write_bytes(src.read_bytes())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
