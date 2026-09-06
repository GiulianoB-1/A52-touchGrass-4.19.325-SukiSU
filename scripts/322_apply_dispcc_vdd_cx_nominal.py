#!/usr/bin/env python3
import argparse
from pathlib import Path

MARKER = "A52_PHASE322_DISPCC_VDD_CX_NOMINAL_VOTE_AB_V1"


def apply(text: str) -> str:
    if MARKER in text:
        raise SystemExit("Phase322: marker already present")
    if "A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1" in text:
        raise SystemExit("Phase322: Phase321 ESC0 experiment leaked into baseline")
    if ".safe_src_index = 0," in text or ".ops = &clk_rcg2_shared_ops," in text:
        raise SystemExit("Phase322: ESC0 shared-safe delta present; expected Phase319 baseline")

    inc_anchor = "#include <linux/regmap.h>\n"
    if text.count(inc_anchor) != 1:
        raise SystemExit("Phase322: linux/regmap.h include anchor count != 1")
    includes = (
        "#include <linux/regmap.h>\n"
        "#include <linux/regulator/consumer.h>\n\n"
        "#include <dt-bindings/regulator/qcom,rpmh-regulator-levels.h>\n"
    )
    text = text.replace(inc_anchor, includes, 1)

    probe_anchor = "static int disp_cc_lagoon_probe(struct platform_device *pdev)\n{\n\tstruct regmap *regmap;\n\tint ret;\n"
    if text.count(probe_anchor) != 1:
        raise SystemExit("Phase322: probe declaration anchor count != 1")
    probe_head = (
        "static int disp_cc_lagoon_probe(struct platform_device *pdev)\n"
        "{\n"
        "\tstruct regmap *regmap;\n"
        "\tstruct regulator *vdd_cx;\n"
        "\tint ret;\n"
        "\tint vdd_rc;\n"
    )
    text = text.replace(probe_anchor, probe_head, 1)

    body_anchor = "\ta52_ackfr_record(\"DISPCC probe enter dev=%s node=%s\",\n"
    if text.count(body_anchor) != 1:
        raise SystemExit("Phase322: probe body anchor count != 1")
    vote = (
        f"\t/* {MARKER}\n"
        "\t * Diagnostic upper-bound vote for the missing downstream clock-VDD\n"
        "\t * framework behavior. Hold DISP_CC vdd_cx at NOMINAL for the\n"
        "\t * provider lifetime; no clock topology/rate/DSI/PHY changes.\n"
        "\t */\n"
        "\ta52_ackfr_record(\"P276 322V s=0\");\n"
        "\tvdd_cx = devm_regulator_get(&pdev->dev, \"vdd_cx\");\n"
        "\tvdd_rc = IS_ERR(vdd_cx) ? PTR_ERR(vdd_cx) : 0;\n"
        "\ta52_ackfr_record(\"P276 322V s=1 rc=%d\", vdd_rc);\n"
        "\tif (IS_ERR(vdd_cx))\n"
        "\t\treturn PTR_ERR(vdd_cx);\n\n"
        "\tvdd_rc = regulator_set_voltage(vdd_cx,\n"
        "\t\tRPMH_REGULATOR_LEVEL_NOM, INT_MAX);\n"
        "\ta52_ackfr_record(\"P276 322V s=2 rc=%d\", vdd_rc);\n"
        "\tif (vdd_rc)\n"
        "\t\treturn vdd_rc;\n\n"
        "\tvdd_rc = regulator_enable(vdd_cx);\n"
        "\ta52_ackfr_record(\"P276 322V s=3 rc=%d\", vdd_rc);\n"
        "\tif (vdd_rc)\n"
        "\t\treturn vdd_rc;\n\n"
    )
    text = text.replace(body_anchor, vote + body_anchor, 1)
    return text


def validate(before: str, after: str):
    for token, delta in (
        (MARKER, 1),
        ("#include <linux/regulator/consumer.h>", 1),
        ("#include <dt-bindings/regulator/qcom,rpmh-regulator-levels.h>", 1),
        ("struct regulator *vdd_cx;", 1),
        ("int vdd_rc;", 1),
        ("devm_regulator_get(&pdev->dev, \"vdd_cx\")", 1),
        ("regulator_set_voltage(vdd_cx,", 1),
        ("RPMH_REGULATOR_LEVEL_NOM, INT_MAX", 1),
        ("regulator_enable(vdd_cx)", 1),
        ("P276 322V s=0", 1),
        ("P276 322V s=1 rc=%d", 1),
        ("P276 322V s=2 rc=%d", 1),
        ("P276 322V s=3 rc=%d", 1),
    ):
        got = after.count(token) - before.count(token)
        if got != delta:
            raise SystemExit(f"Phase322: unexpected delta for {token!r}: {got}, wanted {delta}")

    forbidden = (
        "A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1",
        ".safe_src_index = 0,",
        ".ops = &clk_rcg2_shared_ops,",
        ".vdd_class",
        ".num_rate_max",
        ".rate_max",
    )
    for token in forbidden:
        if after.count(token) != before.count(token):
            raise SystemExit("Phase322: forbidden framework/topology delta: " + token)

    # This experiment must not alter existing low-level clock/MMIO/DSI behavior.
    for token in (
        "DSI_W32(", "writel(", "writel_relaxed(", "regmap_write(",
        "regmap_update_bits(", "clk_set_rate(", "clk_set_parent(",
        "clk_prepare_enable(", "clk_disable_unprepare(", "reset_control_",
        "udelay(", "ndelay(", "usleep_range(", "msleep(",
    ):
        if after.count(token) != before.count(token):
            raise SystemExit("Phase322: forbidden functional delta: " + token)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    p = Path(args.file)
    text = p.read_text()
    if args.check_only:
        required = (
            MARKER,
            "devm_regulator_get(&pdev->dev, \"vdd_cx\")",
            "RPMH_REGULATOR_LEVEL_NOM, INT_MAX",
            "regulator_enable(vdd_cx)",
            "P276 322V s=3 rc=%d",
        )
        for token in required:
            if token not in text:
                raise SystemExit("Phase322 check: missing " + token)
        if "A52_PHASE321_ESC0_SHARED_SAFE_LIFECYCLE_AB_V1" in text:
            raise SystemExit("Phase322 check: Phase321 marker present")
        print("Phase322 DISPCC VDD_CX NOMINAL vote check: PASS")
        return

    before = text
    after = apply(before)
    validate(before, after)
    p.write_text(after)
    print("Phase322 DISPCC VDD_CX NOMINAL vote patch: PASS")


if __name__ == "__main__":
    main()
