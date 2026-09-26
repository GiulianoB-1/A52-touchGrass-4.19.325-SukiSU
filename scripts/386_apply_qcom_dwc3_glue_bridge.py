#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE386_QCOM_DWC3_GLUE_BRIDGE_V2"
QCOM = Path("drivers/usb/dwc3/dwc3-qcom.c")
SIMPLE = Path("drivers/usb/dwc3/dwc3-of-simple.c")
CORE = Path("drivers/usb/dwc3/core.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase386 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch_simple(text: str) -> str:
    if "A52_PHASE386_OF_SIMPLE_RETIRED_V1" in text:
        return text
    return one(
        text,
        '\t{ .compatible = "qcom,dwc-usb3-msm" },\n',
        '\t/* A52_PHASE386_OF_SIMPLE_RETIRED_V1: handled by dwc3-qcom */\n',
        "retire OF-simple compatible",
    )


def patch_qcom(text: str) -> str:
    if MARK in text:
        return text

    if "#include <linux/a52_ack_secure_flight_recorder.h>\n" not in text:
        text = one(
            text, '#include "core.h"\n',
            '#include "core.h"\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
            "recorder include",
        )

    marker = r'''
/* A52_PHASE386_QCOM_DWC3_GLUE_BRIDGE_V2 */
static const char a52_r386_marker[] __used =
	"A52_PHASE386_QCOM_DWC3_GLUE_BRIDGE_V2";

static inline void a52_r386_stage(unsigned int stage, int ret)
{
	a52_ackfr_sticky385_ofsimple(stage, ret);
}

'''
    text = one(
        text,
        'static inline void dwc3_qcom_setbits(void __iomem *base, u32 offset, u32 val)\n',
        marker + 'static inline void dwc3_qcom_setbits(void __iomem *base, u32 offset, u32 val)\n',
        "marker/helper",
    )

    text = one(
        text,
        '\tplatform_set_drvdata(pdev, qcom);\n\tqcom->dev = &pdev->dev;\n',
        '\tplatform_set_drvdata(pdev, qcom);\n\tqcom->dev = &pdev->dev;\n'
        '\ta52_r386_stage(0x100U, 0);\n',
        "probe entry",
    )

    text = one(
        text,
        '\tret = reset_control_deassert(qcom->resets);\n'
        '\tif (ret) {\n'
        '\t\tdev_err(&pdev->dev, "failed to deassert resets, err=%d\\n", ret);\n'
        '\t\treturn ret;\n'
        '\t}\n',
        '\tret = reset_control_deassert(qcom->resets);\n'
        '\tif (ret) {\n'
        '\t\ta52_r386_stage(0x10fU, ret);\n'
        '\t\tdev_err(&pdev->dev, "failed to deassert resets, err=%d\\n", ret);\n'
        '\t\treturn ret;\n'
        '\t}\n'
        '\ta52_r386_stage(0x110U, 0);\n',
        "reset stage",
    )

    text = one(
        text,
        '\tret = dwc3_qcom_clk_init(qcom, of_clk_get_parent_count(np));\n'
        '\tif (ret) {\n'
        '\t\tdev_err(dev, "failed to get clocks\\n");\n'
        '\t\treturn ret;\n'
        '\t}\n',
        '\tret = dwc3_qcom_clk_init(qcom, of_clk_get_parent_count(np));\n'
        '\tif (ret) {\n'
        '\t\ta52_r386_stage(0x11fU, ret);\n'
        '\t\tdev_err(dev, "failed to get clocks\\n");\n'
        '\t\treturn ret;\n'
        '\t}\n'
        '\ta52_r386_stage(0x120U, 0);\n',
        "clock stage",
    )

    # Downstream Samsung DT gives one large parent resource. For this compatible,
    # map only the QSCRATCH subwindow and leave the child DWC3 core resource free.
    anchor = '\tres = platform_get_resource(pdev, IORESOURCE_MEM, 0);\n\n'
    pre = r'''	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);

	if (np && of_device_is_compatible(np, "qcom,dwc-usb3-msm")) {
		if (!res || resource_size(res) <
		    SDM845_QSCRATCH_BASE_OFFSET + SDM845_QSCRATCH_SIZE) {
			ret = -EINVAL;
			a52_r386_stage(0x12fU, ret);
			goto free_urs;
		}
		qcom->qscratch_base = devm_ioremap(dev,
			res->start + SDM845_QSCRATCH_BASE_OFFSET,
			SDM845_QSCRATCH_SIZE);
		if (!qcom->qscratch_base) {
			ret = -ENOMEM;
			a52_r386_stage(0x12eU, ret);
			goto free_urs;
		}
		a52_r386_stage(0x130U, 0);
	} else {
'''
    text = one(text, anchor, pre, "qscratch special-case open")

    close_anchor = (
        '\tqcom->qscratch_base = devm_ioremap_resource(dev, parent_res);\n'
        '\tif (IS_ERR(qcom->qscratch_base)) {\n'
        '\t\tdev_err(dev, "failed to map qscratch, err=%d\\n", ret);\n'
        '\t\tret = PTR_ERR(qcom->qscratch_base);\n'
        '\t\tgoto free_urs;\n'
        '\t}\n\n'
        '\tret = dwc3_qcom_setup_irq(pdev);\n'
    )
    close_new = (
        '\tqcom->qscratch_base = devm_ioremap_resource(dev, parent_res);\n'
        '\tif (IS_ERR(qcom->qscratch_base)) {\n'
        '\t\tdev_err(dev, "failed to map qscratch, err=%d\\n", ret);\n'
        '\t\tret = PTR_ERR(qcom->qscratch_base);\n'
        '\t\tgoto free_urs;\n'
        '\t}\n'
        '\t}\n\n'
        '\tret = dwc3_qcom_setup_irq(pdev);\n'
    )
    text = one(text, close_anchor, close_new, "qscratch special-case close")

    text = one(
        text,
        '\tret = dwc3_qcom_setup_irq(pdev);\n'
        '\tif (ret) {\n'
        '\t\tdev_err(dev, "failed to setup IRQs, err=%d\\n", ret);\n'
        '\t\tgoto free_urs;\n'
        '\t}\n',
        '\tret = dwc3_qcom_setup_irq(pdev);\n'
        '\tif (ret) {\n'
        '\t\ta52_r386_stage(0x13fU, ret);\n'
        '\t\tdev_err(dev, "failed to setup IRQs, err=%d\\n", ret);\n'
        '\t\tgoto free_urs;\n'
        '\t}\n'
        '\ta52_r386_stage(0x140U, 0);\n',
        "IRQ stage",
    )

    core_call = (
        '\tif (np)\n\t\tret = dwc3_qcom_of_register_core(pdev);\n'
        '\telse\n\t\tret = dwc3_qcom_acpi_register_core(pdev);\n\n'
    )
    text = one(
        text, core_call,
        core_call + '\ta52_r386_stage(0x150U, ret);\n',
        "core registration stage",
    )

    text = one(
        text,
        '\tret = dwc3_qcom_register_extcon(qcom);\n'
        '\tif (ret)\n\t\tgoto interconnect_exit;\n',
        '\tret = dwc3_qcom_register_extcon(qcom);\n'
        '\ta52_r386_stage(0x170U, ret);\n'
        '\tif (ret)\n\t\tgoto interconnect_exit;\n',
        "extcon stage",
    )

    text = one(
        text,
        '\tpm_runtime_forbid(dev);\n\n\treturn 0;\n',
        '\tpm_runtime_forbid(dev);\n'
        '\ta52_r386_stage(0x1ffU, 0);\n\n\treturn 0;\n',
        "success stage",
    )

    text = one(
        text,
        'static const struct of_device_id dwc3_qcom_of_match[] = {\n'
        '\t{ .compatible = "qcom,dwc3" },\n',
        'static const struct of_device_id dwc3_qcom_of_match[] = {\n'
        '\t{ .compatible = "qcom,dwc-usb3-msm" },\n'
        '\t{ .compatible = "qcom,dwc3" },\n',
        "downstream compatible",
    )
    return text


def patch_core(text: str) -> str:
    if "A52_PHASE386_CORE_STAGE_V2" in text:
        return text
    text = one(
        text,
        '\tret = dwc3_get_dr_mode(dwc);\n'
        '\tif (ret)\n\t\tgoto err3;\n\n'
        '\tret = dwc3_alloc_scratch_buffers(dwc);\n'
        '\tif (ret)\n\t\tgoto err3;\n\n'
        '\tret = dwc3_core_init(dwc);\n',
        '\tret = dwc3_get_dr_mode(dwc);\n'
        '\tif (ret) {\n'
        '\t\ta52_ackfr_sticky385_ofsimple(0x20fU, ret);\n'
        '\t\tgoto err3;\n'
        '\t}\n\n'
        '\tret = dwc3_alloc_scratch_buffers(dwc);\n'
        '\tif (ret) {\n'
        '\t\ta52_ackfr_sticky385_ofsimple(0x210U, ret);\n'
        '\t\tgoto err3;\n'
        '\t}\n\n'
        '\t/* A52_PHASE386_CORE_STAGE_V2 */\n'
        '\ta52_ackfr_sticky385_ofsimple(0x201U, 0);\n'
        '\tret = dwc3_core_init(dwc);\n'
        '\ta52_ackfr_sticky385_ofsimple(0x202U, ret);\n',
        "core-init stage",
    )
    text = one(
        text,
        '\tdwc3_check_params(dwc);\n\tdwc3_debugfs_init(dwc);\n\n'
        '\tret = dwc3_core_init_mode(dwc);\n',
        '\tdwc3_check_params(dwc);\n\tdwc3_debugfs_init(dwc);\n\n'
        '\ta52_ackfr_sticky385_ofsimple(0x203U, 0);\n'
        '\tret = dwc3_core_init_mode(dwc);\n'
        '\ta52_ackfr_sticky385_ofsimple(0x204U, ret);\n',
        "mode-init stage",
    )
    return text


def validate(root: Path) -> None:
    q = (root / QCOM).read_text()
    s = (root / SIMPLE).read_text()
    c = (root / CORE).read_text()
    for token in (
        MARK, '"A52_PHASE386_QCOM_DWC3_GLUE_BRIDGE_V2"',
        'compatible = "qcom,dwc-usb3-msm"',
        'a52_r386_stage(0x150U, ret)',
        'SDM845_QSCRATCH_BASE_OFFSET',
    ):
        if token not in q:
            raise SystemExit("Phase386 qcom token missing: " + token)
    for token in (
        "A52_PHASE386_CORE_STAGE_V2",
        "a52_ackfr_sticky385_ofsimple(0x202U, ret)",
        "a52_ackfr_sticky385_ofsimple(0x204U, ret)",
    ):
        if token not in c:
            raise SystemExit("Phase386 core token missing: " + token)
    if '\t{ .compatible = "qcom,dwc-usb3-msm" },' in s:
        raise SystemExit("Phase386 OF-simple still owns qcom,dwc-usb3-msm")
    if "A52_PHASE386_OF_SIMPLE_RETIRED_V1" not in s:
        raise SystemExit("Phase386 OF-simple retirement marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root
    if ns.check_only:
        validate(root)
        print("Phase386 QCOM DWC3 glue bridge audit: PASS")
        return 0

    for rel, fn in ((SIMPLE, patch_simple), (QCOM, patch_qcom), (CORE, patch_core)):
        p = root / rel
        if not p.is_file():
            raise SystemExit("Phase386 missing source: " + str(rel))
        p.write_text(fn(p.read_text()))
    validate(root)
    print("Phase386 QCOM DWC3 glue bridge applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
