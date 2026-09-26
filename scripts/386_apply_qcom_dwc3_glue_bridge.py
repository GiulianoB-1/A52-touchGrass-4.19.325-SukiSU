#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE386_QCOM_DWC3_GLUE_BRIDGE_V1"
QCOM = Path("drivers/usb/dwc3/dwc3-qcom.c")
SIMPLE = Path("drivers/usb/dwc3/dwc3-of-simple.c")
CORE = Path("drivers/usb/dwc3/core.c")


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase386 {label}: expected 1 anchor, found {count}")
    return text.replace(old, new, 1)


def patch_simple(text: str) -> str:
    if "A52_PHASE386_OF_SIMPLE_RETIRED_V1" in text:
        return text
    old = '\t{ .compatible = "qcom,dwc-usb3-msm" },\n'
    new = '\t/* A52_PHASE386_OF_SIMPLE_RETIRED_V1: qcom,dwc-usb3-msm uses dwc3-qcom */\n'
    return one(text, old, new, "remove downstream compatible from OF-simple")


def patch_qcom(text: str) -> str:
    if MARK in text:
        return text

    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc not in text:
        text = one(text, '#include "core.h"\n',
                   '#include "core.h"\n' + inc,
                   "qcom recorder include")

    marker = '''
/* A52_PHASE386_QCOM_DWC3_GLUE_BRIDGE_V1
 *
 * Samsung/Qualcomm downstream DT uses one 1 MiB parent window beginning at
 * 0xa600000 and places QSCRATCH at +0xf8800 while the snps,dwc3 child reuses
 * the beginning of that parent window.  Upstream dwc3-qcom normally reserves
 * the parent resource directly, which would overlap the child core resource.
 * For qcom,dwc-usb3-msm only, map just the QSCRATCH subwindow without claiming
 * the full parent resource.
 */
static const char a52_r386_marker[] __used =
    "A52_PHASE386_QCOM_DWC3_GLUE_BRIDGE_V1";

static inline void a52_r386_stage(unsigned int stage, int ret)
{
    /* Reuse the Phase385b fixed non-circular slot.  0x1xx = qcom glue. */
    a52_ackfr_sticky385_ofsimple(stage, ret);
}

'''
    text = one(text,
               'static inline void dwc3_qcom_setbits(void __iomem *base, u32 offset, u32 val)\n',
               marker + 'static inline void dwc3_qcom_setbits(void __iomem *base, u32 offset, u32 val)\n',
               "qcom bridge marker")

    old = '''\tplatform_set_drvdata(pdev, qcom);
\tqcom->dev = &pdev->dev;

'''
    new = '''\tplatform_set_drvdata(pdev, qcom);
\tqcom->dev = &pdev->dev;
\ta52_r386_stage(0x100U, 0);

'''
    text = one(text, old, new, "qcom probe entry")

    old = '''\tret = reset_control_deassert(qcom->resets);
\tif (ret) {
\t\tdev_err(&pdev->dev, "failed to deassert resets, err=%d\\n", ret);
\t\treturn ret;
\t}

\tret = dwc3_qcom_clk_init(qcom, of_clk_get_parent_count(np));
'''
    new = '''\tret = reset_control_deassert(qcom->resets);
\tif (ret) {
\t\ta52_r386_stage(0x10fU, ret);
\t\tdev_err(&pdev->dev, "failed to deassert resets, err=%d\\n", ret);
\t\treturn ret;
\t}
\ta52_r386_stage(0x110U, 0);

\tret = dwc3_qcom_clk_init(qcom, of_clk_get_parent_count(np));
'''
    text = one(text, old, new, "qcom reset stage")

    old = '''\tif (ret) {
\t\tdev_err(dev, "failed to get clocks\\n");
\t\treturn ret;
\t}

\tres = platform_get_resource(pdev, IORESOURCE_MEM, 0);

\tif (np) {
\t\tparent_res = res;
\t} else {
\t\tmemcpy(&local_res, res, sizeof(struct resource));
\t\tparent_res = &local_res;

\t\tparent_res->start = res->start +
\t\t\tqcom->acpi_pdata->qscratch_base_offset;
\t\tparent_res->end = parent_res->start +
\t\t\tqcom->acpi_pdata->qscratch_base_size;
\t}

\tqcom->qscratch_base = devm_ioremap_resource(dev, parent_res);
\tif (IS_ERR(qcom->qscratch_base)) {
\t\tdev_err(dev, "failed to map qscratch, err=%d\\n", ret);
\t\tret = PTR_ERR(qcom->qscratch_base);
\t\tgoto clk_disable;
\t}

'''
    new = '''\tif (ret) {
\t\ta52_r386_stage(0x11fU, ret);
\t\tdev_err(dev, "failed to get clocks\\n");
\t\treturn ret;
\t}
\ta52_r386_stage(0x120U, 0);

\tres = platform_get_resource(pdev, IORESOURCE_MEM, 0);

\tif (np && of_device_is_compatible(np, "qcom,dwc-usb3-msm")) {
\t\tif (!res ||
\t\t    resource_size(res) < SDM845_QSCRATCH_BASE_OFFSET +
\t\t\t\t\t SDM845_QSCRATCH_SIZE) {
\t\t\tret = -EINVAL;
\t\t\ta52_r386_stage(0x12fU, ret);
\t\t\tgoto clk_disable;
\t\t}

\t\tqcom->qscratch_base = devm_ioremap(dev,
\t\t\tres->start + SDM845_QSCRATCH_BASE_OFFSET,
\t\t\tSDM845_QSCRATCH_SIZE);
\t\tif (!qcom->qscratch_base) {
\t\t\tret = -ENOMEM;
\t\t\ta52_r386_stage(0x12eU, ret);
\t\t\tgoto clk_disable;
\t\t}
\t\ta52_r386_stage(0x130U, 0);
\t} else {
\t\tif (np) {
\t\t\tparent_res = res;
\t\t} else {
\t\t\tmemcpy(&local_res, res, sizeof(struct resource));
\t\t\tparent_res = &local_res;

\t\t\tparent_res->start = res->start +
\t\t\t\tqcom->acpi_pdata->qscratch_base_offset;
\t\t\tparent_res->end = parent_res->start +
\t\t\t\tqcom->acpi_pdata->qscratch_base_size;
\t\t}

\t\tqcom->qscratch_base = devm_ioremap_resource(dev, parent_res);
\t\tif (IS_ERR(qcom->qscratch_base)) {
\t\t\tret = PTR_ERR(qcom->qscratch_base);
\t\t\tdev_err(dev, "failed to map qscratch, err=%d\\n", ret);
\t\t\tgoto clk_disable;
\t\t}
\t}

'''
    text = one(text, old, new, "downstream qscratch mapping")

    old = '''\tret = dwc3_qcom_setup_irq(pdev);
\tif (ret) {
\t\tdev_err(dev, "failed to setup IRQs, err=%d\\n", ret);
\t\tgoto clk_disable;
\t}

'''
    new = '''\tret = dwc3_qcom_setup_irq(pdev);
\tif (ret) {
\t\ta52_r386_stage(0x13fU, ret);
\t\tdev_err(dev, "failed to setup IRQs, err=%d\\n", ret);
\t\tgoto clk_disable;
\t}
\ta52_r386_stage(0x140U, 0);

'''
    text = one(text, old, new, "qcom irq stage")

    old = '''\tif (np)
\t\tret = dwc3_qcom_of_register_core(pdev);
\telse
\t\tret = dwc3_qcom_acpi_register_core(pdev);

\tif (ret) {
\t\tdev_err(dev, "failed to register DWC3 Core, err=%d\\n", ret);
\t\tgoto clk_disable;
\t}

'''
    new = '''\tif (np)
\t\tret = dwc3_qcom_of_register_core(pdev);
\telse
\t\tret = dwc3_qcom_acpi_register_core(pdev);

\ta52_r386_stage(0x150U, ret);
\tif (ret) {
\t\tdev_err(dev, "failed to register DWC3 Core, err=%d\\n", ret);
\t\tgoto clk_disable;
\t}

'''
    text = one(text, old, new, "qcom core registration stage")

    old = '''\tret = dwc3_qcom_register_extcon(qcom);
\tif (ret)
\t\tgoto depopulate;

\tdevice_init_wakeup(&pdev->dev, 1);
'''
    new = '''\tret = dwc3_qcom_register_extcon(qcom);
\ta52_r386_stage(0x170U, ret);
\tif (ret)
\t\tgoto depopulate;

\tdevice_init_wakeup(&pdev->dev, 1);
'''
    text = one(text, old, new, "qcom extcon stage")

    old = '''\tpm_runtime_forbid(dev);

\treturn 0;
'''
    new = '''\tpm_runtime_forbid(dev);
\ta52_r386_stage(0x1ffU, 0);

\treturn 0;
'''
    text = one(text, old, new, "qcom success stage")

    old = '''static const struct of_device_id dwc3_qcom_of_match[] = {
\t{ .compatible = "qcom,dwc3" },
'''
    new = '''static const struct of_device_id dwc3_qcom_of_match[] = {
\t{ .compatible = "qcom,dwc-usb3-msm" },
\t{ .compatible = "qcom,dwc3" },
'''
    return one(text, old, new, "qcom downstream compatible")


def patch_core(text: str) -> str:
    if "A52_PHASE386_CORE_STAGE_V1" in text:
        return text

    old = '''\tret = dwc3_get_dr_mode(dwc);
\tif (ret)
\t\tgoto err_free_event_buffers;

\tret = dwc3_core_init(dwc);
\tif (ret) {
'''
    new = '''\tret = dwc3_get_dr_mode(dwc);
\tif (ret) {
\t\ta52_ackfr_sticky385_ofsimple(0x20fU, ret);
\t\tgoto err_free_event_buffers;
\t}

\t/* A52_PHASE386_CORE_STAGE_V1 */
\ta52_ackfr_sticky385_ofsimple(0x201U, 0);
\tret = dwc3_core_init(dwc);
\ta52_ackfr_sticky385_ofsimple(0x202U, ret);
\tif (ret) {
'''
    text = one(text, old, new, "DWC3 core-init result")

    old = '''\tdwc3_check_params(dwc);
\tdwc3_debugfs_init(dwc);

\tret = dwc3_core_init_mode(dwc);
\tif (ret)
\t\tgoto err_exit_debugfs;
'''
    new = '''\tdwc3_check_params(dwc);
\tdwc3_debugfs_init(dwc);

\ta52_ackfr_sticky385_ofsimple(0x203U, 0);
\tret = dwc3_core_init_mode(dwc);
\ta52_ackfr_sticky385_ofsimple(0x204U, ret);
\tif (ret)
\t\tgoto err_exit_debugfs;
'''
    return one(text, old, new, "DWC3 mode-init result")


def validate(root: Path) -> None:
    checks = {
        SIMPLE: ("A52_PHASE386_OF_SIMPLE_RETIRED_V1",),
        QCOM: (
            MARK,
            '"A52_PHASE386_QCOM_DWC3_GLUE_BRIDGE_V1"',
            'compatible = "qcom,dwc-usb3-msm"',
            "SDM845_QSCRATCH_BASE_OFFSET",
            "a52_r386_stage(0x150U, ret)",
        ),
        CORE: (
            "A52_PHASE386_CORE_STAGE_V1",
            "a52_ackfr_sticky385_ofsimple(0x202U, ret)",
            "a52_ackfr_sticky385_ofsimple(0x204U, ret)",
        ),
    }
    for rel, tokens in checks.items():
        data = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in data:
                raise SystemExit(f"Phase386 validation missing {token} in {rel}")

    simple = (root / SIMPLE).read_text(encoding="utf-8")
    active = '\t{ .compatible = "qcom,dwc-usb3-msm" },'
    if active in simple:
        raise SystemExit("Phase386 OF-simple still actively matches qcom,dwc-usb3-msm")


def run(root: Path) -> None:
    for rel in (QCOM, SIMPLE, CORE):
        if not (root / rel).is_file():
            raise SystemExit(f"Phase386 missing source: {rel}")

    p = root / SIMPLE
    p.write_text(patch_simple(p.read_text()), encoding="utf-8")
    p = root / QCOM
    p.write_text(patch_qcom(p.read_text()), encoding="utf-8")
    p = root / CORE
    p.write_text(patch_core(p.read_text()), encoding="utf-8")
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if ns.check_only:
        validate(ns.root)
        print("Phase386 QCOM DWC3 glue bridge audit: PASS")
        return 0
    run(ns.root)
    print("Phase386 QCOM DWC3 glue bridge applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
