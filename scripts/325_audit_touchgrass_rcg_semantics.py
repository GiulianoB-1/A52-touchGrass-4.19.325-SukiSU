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
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1] + "\n"
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

    gki_rcg2_path = args.gki / "drivers/clk/qcom/clk-rcg2.c"
    gki_rcgh_path = args.gki / "drivers/clk/qcom/clk-rcg.h"
    gki_disp_path = args.gki / "drivers/clk/qcom/dispcc-lagoon.c"
    tg_rcg2_path = args.tg / "drivers/clk/qcom/clk-rcg2.c"
    tg_rcgh_path = args.tg / "drivers/clk/qcom/clk-rcg.h"
    tg_disp_path = args.tg / "drivers/clk/qcom/dispcc-lagoon.c"

    gki_rcg2 = gki_rcg2_path.read_text()
    gki_rcgh = gki_rcgh_path.read_text()
    tg_rcg2 = tg_rcg2_path.read_text()
    tg_rcgh = tg_rcgh_path.read_text()
    tg_disp = tg_disp_path.read_text()

    args.out.mkdir(parents=True, exist_ok=True)

    names = [
        "clk_rcg2_current_config",
        "clk_byte2_set_rate",
        "clk_pixel_set_rate",
        "clk_rcg2_enable",
        "clk_rcg2_disable",
        "clk_rcg2_shared_set_rate",
        "clk_rcg2_shared_enable",
        "clk_rcg2_shared_disable",
    ]

    report = []
    report.append("PHASE325 TOUCHGRASS -> GKI RCG SEMANTIC AUDIT\n")
    report.append("FACTS\n")
    facts = {
        "gki_pristine_has_dispcc_lagoon": gki_disp_path.is_file(),
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
        "tg_esc0_safe_config": "enable_safe_config = true" in struct_block(tg_disp, "disp_cc_mdss_esc0_clk_src"),
    }
    for key, value in facts.items():
        report.append(f"{key}={yn(value)}\n")

    report.append("\nTOUCHGRASS LAGOON TARGET BLOCKS\n")
    for target in (
        "disp_cc_mdss_byte0_clk_src",
        "disp_cc_mdss_pclk0_clk_src",
        "disp_cc_mdss_esc0_clk_src",
    ):
        report.append(f"\n--- TouchGrass {target} ---\n")
        report.append(struct_block(tg_disp, target))

    report.append("\nFRAMEWORK FUNCTIONS\n")
    for name in names:
        report.append(f"\n===== TouchGrass {name} =====\n")
        report.append(function(tg_rcg2, name))
        report.append(f"\n===== GKI {name} =====\n")
        report.append(function(gki_rcg2, name))

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
    report.append("LAGOON_LAYER_NOTE=Pristine GKI common does not carry dispcc-lagoon.c; compare Lagoon definitions after Phase319 vendor reconstruction.\n")

    out = "".join(report)
    (args.out / "REPORT.txt").write_text(out)
    print(out)

    for label, src in (
        ("gki-clk-rcg2.c", gki_rcg2_path),
        ("gki-clk-rcg.h", gki_rcgh_path),
        ("tg-clk-rcg2.c", tg_rcg2_path),
        ("tg-clk-rcg.h", tg_rcgh_path),
        ("tg-dispcc-lagoon.c", tg_disp_path),
    ):
        (args.out / label).write_bytes(src.read_bytes())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
