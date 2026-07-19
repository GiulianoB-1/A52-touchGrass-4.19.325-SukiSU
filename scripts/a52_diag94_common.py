#!/usr/bin/env python3
from __future__ import annotations


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_first_supported(
    text: str, candidates: tuple[str, ...], replacement_builder, label: str
) -> str:
    matches = [candidate for candidate in candidates if candidate in text]
    if len(matches) != 1:
        raise SystemExit(
            f"{label}: expected exactly one supported anchor, found {len(matches)}"
        )
    anchor = matches[0]
    return replace_once(text, anchor, replacement_builder(anchor), label)


def triplet(fmt: str, args: str, indent: str, prefix: str = "A52UFS") -> str:
    return "".join(
        f'{indent}a52_persistent_diag_mark("{prefix} copy={copy} {fmt}\\n", {args});\n'
        for copy in (1, 2, 3)
    )


def declare_helper(text: str, anchors: tuple[str, ...], label: str) -> str:
    declaration = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    if declaration in text:
        return text
    for anchor in anchors:
        if anchor in text:
            return replace_once(text, anchor, anchor + declaration, label)
    raise SystemExit(f"{label}: no supported include anchor")


def instrument_qcom(qcom: str) -> str:
    qcom = declare_helper(
        qcom,
        (
            '#include "ufs_quirks.h"\n',
            '#include "ufshci.h"\n',
            '#include "ufs-qcom.h"\n',
        ),
        "declare persistent diagnostic helper in ufs-qcom.c",
    )

    dev_anchor = "\tstruct device *dev = &pdev->dev;\n"
    qcom = replace_once(
        qcom,
        dev_anchor,
        dev_anchor
        + '\tconst char *a52_status = "<absent>";\n'
        + '\tconst char *a52_compat = "<absent>";\n'
        + "\tint a52_available = 0;\n",
        "add UFS Qualcomm probe diagnostic locals",
    )

    probe_anchor = "\t/* Perform generic probe */\n"
    begin = triplet(
        "PROBE_BEGIN dev=%s node=%s avail=%d status=%s compat=%s",
        'dev_name(dev), dev->of_node ? dev->of_node->full_name : "<none>", '
        "a52_available, a52_status, a52_compat",
        "\t",
    )
    qcom = replace_once(
        qcom,
        probe_anchor,
        "\tif (dev->of_node) {\n"
        "\t\ta52_available = of_device_is_available(dev->of_node);\n"
        '\t\tof_property_read_string(dev->of_node, "status", &a52_status);\n'
        '\t\tof_property_read_string(dev->of_node, "compatible", &a52_compat);\n'
        "\t}\n"
        + begin
        + "\n"
        + probe_anchor,
        "instrument UFS Qualcomm probe entry",
    )

    init_anchor = "\terr = ufshcd_pltfrm_init(pdev, &ufs_hba_qcom_vops);\n"
    end = triplet(
        "PROBE_END dev=%s ret=%d drvdata=%s driver=%s",
        'dev_name(dev), err, platform_get_drvdata(pdev) ? "present" : "none", '
        'dev->driver ? dev->driver->name : "<unbound>"',
        "\t",
    )
    qcom = replace_once(
        qcom,
        init_anchor,
        init_anchor + end,
        "instrument UFS Qualcomm probe result",
    )
    return qcom


def instrument_platform_glue(pltfrm: str) -> str:
    pltfrm = declare_helper(
        pltfrm,
        (
            '#include "ufshcd.h"\n',
            '#include "ufshcd-pltfrm.h"\n',
            "#include <linux/platform_device.h>\n",
        ),
        "declare persistent diagnostic helper in ufshcd-pltfrm.c",
    )

    mmio_block = (
        "\tmmio_base = devm_platform_ioremap_resource(pdev, 0);\n"
        "\tif (IS_ERR(mmio_base)) {\n"
        "\t\terr = PTR_ERR(mmio_base);\n"
        "\t\tgoto out;\n"
        "\t}\n"
    )
    pltfrm = replace_once(
        pltfrm,
        mmio_block,
        mmio_block
        + triplet(
            "PLTFRM stage=mmio ret=0 dev=%s mmio=%p",
            "dev_name(dev), mmio_base",
            "\t",
        ),
        "instrument platform MMIO mapping",
    )

    irq_block = (
        "\tirq = platform_get_irq(pdev, 0);\n"
        "\tif (irq < 0) {\n"
        "\t\terr = irq;\n"
        "\t\tgoto out;\n"
        "\t}\n"
    )
    pltfrm = replace_once(
        pltfrm,
        irq_block,
        irq_block
        + triplet(
            "PLTFRM stage=irq ret=0 dev=%s irq=%d",
            "dev_name(dev), irq",
            "\t",
        ),
        "instrument platform IRQ acquisition",
    )

    alloc_block = (
        "\terr = ufshcd_alloc_host(dev, &hba);\n"
        "\tif (err) {\n"
        '\t\tdev_err(&pdev->dev, "Allocation failed\\n");\n'
        "\t\tgoto out;\n"
        "\t}\n"
    )
    pltfrm = replace_once(
        pltfrm,
        alloc_block,
        alloc_block
        + triplet(
            "PLTFRM stage=alloc_host ret=%d dev=%s hba=%p host_no=%d",
            "err, dev_name(dev), hba, hba->host ? hba->host->host_no : -1",
            "\t",
        ),
        "instrument UFS host allocation",
    )

    clock_block = (
        "\terr = ufshcd_parse_clock_info(hba);\n"
        "\tif (err) {\n"
        '\t\tdev_err(&pdev->dev, "%s: clock parse failed %d\\n",\n'
        "\t\t\t__func__, err);\n"
        "\t\tgoto dealloc_host;\n"
        "\t}\n"
    )
    pltfrm = replace_once(
        pltfrm,
        clock_block,
        clock_block
        + triplet(
            "PLTFRM stage=parse_clocks ret=%d dev=%s",
            "err, dev_name(dev)",
            "\t",
        ),
        "instrument UFS clock parsing",
    )

    regulator_block = (
        "\terr = ufshcd_parse_regulator_info(hba);\n"
        "\tif (err) {\n"
        '\t\tdev_err(&pdev->dev, "%s: regulator init failed %d\\n",\n'
        "\t\t\t__func__, err);\n"
        "\t\tgoto dealloc_host;\n"
        "\t}\n"
    )
    pltfrm = replace_once(
        pltfrm,
        regulator_block,
        regulator_block
        + triplet(
            "PLTFRM stage=parse_regulators ret=%d dev=%s",
            "err, dev_name(dev)",
            "\t",
        ),
        "instrument UFS regulator parsing",
    )

    init_block = (
        "\terr = ufshcd_init(hba, mmio_base, irq);\n"
        "\tif (err) {\n"
        '\t\tdev_err(dev, "Initialization failed\\n");\n'
        "\t\tgoto dealloc_host;\n"
        "\t}\n"
    )
    pltfrm = replace_once(
        pltfrm,
        init_block,
        init_block
        + triplet(
            "PLTFRM stage=ufshcd_init ret=%d dev=%s state=%d version=0x%x cap=0x%x",
            "err, dev_name(dev), hba->ufshcd_state, hba->ufs_version, hba->capabilities",
            "\t",
        ),
        "instrument generic UFS initialization",
    )

    return pltfrm
