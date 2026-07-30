#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def add_recorder_decl(text: str) -> str:
    declaration = "extern void a52_ackfr_record(const char *fmt, ...);\n"
    if declaration in text:
        return text
    includes = list(re.finditer(r"^#include[^\n]*\n", text, flags=re.MULTILINE))
    if not includes:
        raise SystemExit("pinctrl-msm includes: no include block found")
    pos = includes[-1].end()
    return text[:pos] + "\n" + declaration + text[pos:]


def patch_trace_helper(text: str) -> str:
    return one(
        text,
        "};\n\n#define MSM_ACCESSOR(name) \\\n",
        "};\n\n"
        "static bool a52_lagoon_pinctrl_trace(const struct msm_pinctrl *pctrl)\n"
        "{\n"
        "\treturn pctrl && pctrl->dev && pctrl->dev->of_node &&\n"
        "\t\tof_device_is_compatible(pctrl->dev->of_node,\n"
        "\t\t\t\t\t\"qcom,lagoon-pinctrl\");\n"
        "}\n\n"
        "#define A52_PINTRACE(pctrl, fmt, ...) do { \\\n"
        "\tif (a52_lagoon_pinctrl_trace(pctrl)) \\\n"
        "\t\ta52_ackfr_record(fmt, ##__VA_ARGS__); \\\n"
        "} while (0)\n\n"
        "#define MSM_ACCESSOR(name) \\\n",
        "Lagoon trace helper",
    )


def patch_gpio_init(text: str) -> str:
    text = one(
        text,
        "\tbool skip;\n\n\tif (WARN_ON(ngpio > MAX_NR_GPIO))\n\t\treturn -EINVAL;\n",
        "\tbool skip;\n\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio enter ng=%u\", ngpio);\n"
        "\tif (WARN_ON(ngpio > MAX_NR_GPIO)) {\n"
        "\t\tA52_PINTRACE(pctrl, \"PINCTRL gpio invalid-ng rc=%d\", -EINVAL);\n"
        "\t\treturn -EINVAL;\n"
        "\t}\n",
        "gpio entry",
    )

    text = one(
        text,
        "\tif (msm_gpio_needs_valid_mask(pctrl))\n\t\tchip->init_valid_mask = msm_gpio_init_valid_mask;\n\n\tpctrl->irq_chip.name = \"msmgpio\";\n",
        "\tif (msm_gpio_needs_valid_mask(pctrl))\n"
        "\t\tchip->init_valid_mask = msm_gpio_init_valid_mask;\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio template mask=%u\",\n"
        "\t\tchip->init_valid_mask != NULL);\n\n"
        "\tpctrl->irq_chip.name = \"msmgpio\";\n",
        "gpio template",
    )

    text = one(
        text,
        "\tnp = of_parse_phandle(pctrl->dev->of_node, \"wakeup-parent\", 0);\n"
        "\tif (np) {\n"
        "\t\tchip->irq.parent_domain = irq_find_matching_host(np,\n"
        "\t\t\t\t\t\t DOMAIN_BUS_WAKEUP);\n"
        "\t\tof_node_put(np);\n"
        "\t\tif (!chip->irq.parent_domain)\n"
        "\t\t\treturn -EPROBE_DEFER;\n"
        "\t\tchip->irq.child_to_parent_hwirq = msm_gpio_wakeirq;\n"
        "\t\tpctrl->irq_chip.irq_eoi = irq_chip_eoi_parent;\n",
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio wake-parse enter\");\n"
        "\tnp = of_parse_phandle(pctrl->dev->of_node, \"wakeup-parent\", 0);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio wake-parse np=%u\", np != NULL);\n"
        "\tif (np) {\n"
        "\t\tchip->irq.parent_domain = irq_find_matching_host(np,\n"
        "\t\t\t\t\t\t DOMAIN_BUS_WAKEUP);\n"
        "\t\tA52_PINTRACE(pctrl, \"PINCTRL gpio wake-domain ok=%u\",\n"
        "\t\t\tchip->irq.parent_domain != NULL);\n"
        "\t\tof_node_put(np);\n"
        "\t\tif (!chip->irq.parent_domain) {\n"
        "\t\t\tA52_PINTRACE(pctrl, \"PINCTRL gpio wake-domain rc=%d\",\n"
        "\t\t\t\t-EPROBE_DEFER);\n"
        "\t\t\treturn -EPROBE_DEFER;\n"
        "\t\t}\n"
        "\t\tchip->irq.child_to_parent_hwirq = msm_gpio_wakeirq;\n"
        "\t\tpctrl->irq_chip.irq_eoi = irq_chip_eoi_parent;\n",
        "wakeup parent",
    )

    text = one(
        text,
        "\t\tskip = irq_domain_qcom_handle_wakeup(chip->irq.parent_domain);\n"
        "\t\tfor (i = 0; skip && i < pctrl->soc->nwakeirq_map; i++) {\n",
        "\t\tA52_PINTRACE(pctrl, \"PINCTRL gpio wake-handle enter\");\n"
        "\t\tskip = irq_domain_qcom_handle_wakeup(chip->irq.parent_domain);\n"
        "\t\tA52_PINTRACE(pctrl, \"PINCTRL gpio wake-handle skip=%u map=%u\",\n"
        "\t\t\tskip, pctrl->soc->nwakeirq_map);\n"
        "\t\tfor (i = 0; skip && i < pctrl->soc->nwakeirq_map; i++) {\n",
        "wakeup handling",
    )

    text = one(
        text,
        "\tgirq->parents = devm_kcalloc(pctrl->dev, 1, sizeof(*girq->parents),\n"
        "\t\t\t\t     GFP_KERNEL);\n"
        "\tif (!girq->parents)\n"
        "\t\treturn -ENOMEM;\n",
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio parent-alloc enter\");\n"
        "\tgirq->parents = devm_kcalloc(pctrl->dev, 1, sizeof(*girq->parents),\n"
        "\t\t\t\t     GFP_KERNEL);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio parent-alloc ok=%u\",\n"
        "\t\tgirq->parents != NULL);\n"
        "\tif (!girq->parents)\n"
        "\t\treturn -ENOMEM;\n",
        "IRQ parent allocation",
    )

    text = one(
        text,
        "\tret = gpiochip_add_data(&pctrl->chip, pctrl);\n"
        "\tif (ret) {\n",
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio chip-add enter irq=%d\",\n"
        "\t\tpctrl->irq);\n"
        "\tret = gpiochip_add_data(&pctrl->chip, pctrl);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio chip-add exit rc=%d\", ret);\n"
        "\tif (ret) {\n",
        "gpiochip registration",
    )

    text = one(
        text,
        "\tif (!of_property_read_bool(pctrl->dev->of_node, \"gpio-ranges\")) {\n"
        "\t\tret = gpiochip_add_pin_range(&pctrl->chip,\n"
        "\t\t\tdev_name(pctrl->dev), 0, 0, chip->ngpio);\n"
        "\t\tif (ret) {\n",
        "\tif (!of_property_read_bool(pctrl->dev->of_node, \"gpio-ranges\")) {\n"
        "\t\tA52_PINTRACE(pctrl, \"PINCTRL gpio range-add enter\");\n"
        "\t\tret = gpiochip_add_pin_range(&pctrl->chip,\n"
        "\t\t\tdev_name(pctrl->dev), 0, 0, chip->ngpio);\n"
        "\t\tA52_PINTRACE(pctrl, \"PINCTRL gpio range-add exit rc=%d\", ret);\n"
        "\t\tif (ret) {\n",
        "pin range registration",
    )

    text = one(
        text,
        "\t}\n\n\treturn 0;\n}\n\nstatic int msm_ps_hold_restart",
        "\t}\n\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL gpio exit rc=0\");\n"
        "\treturn 0;\n"
        "}\n\n"
        "static int msm_ps_hold_restart",
        "gpio completion",
    )
    return text


def patch_probe(text: str) -> str:
    text = one(
        text,
        "\tpctrl = devm_kzalloc(&pdev->dev, sizeof(*pctrl), GFP_KERNEL);\n"
        "\tif (!pctrl)\n"
        "\t\treturn -ENOMEM;\n",
        "\tpctrl = devm_kzalloc(&pdev->dev, sizeof(*pctrl), GFP_KERNEL);\n"
        "\tif (!pctrl)\n"
        "\t\treturn -ENOMEM;\n"
        "\tif (pdev->dev.of_node && of_device_is_compatible(\n"
        "\t\t\tpdev->dev.of_node, \"qcom,lagoon-pinctrl\"))\n"
        "\t\ta52_ackfr_record(\"PINCTRL msm alloc ok=1\");\n",
        "probe allocation",
    )

    text = one(
        text,
        "\traw_spin_lock_init(&pctrl->lock);\n\n\tif (soc_data->tiles) {\n",
        "\traw_spin_lock_init(&pctrl->lock);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm state np=%u ng=%u tiles=%u\",\n"
        "\t\tpctrl->soc->npins, pctrl->soc->ngpios, pctrl->soc->ntiles);\n\n"
        "\tif (soc_data->tiles) {\n",
        "probe state",
    )

    text = one(
        text,
        "\t\tfor (i = 0; i < soc_data->ntiles; i++) {\n"
        "\t\t\tres = platform_get_resource_byname(pdev, IORESOURCE_MEM,\n"
        "\t\t\t\t\t\t\t   soc_data->tiles[i]);\n"
        "\t\t\tpctrl->regs[i] = devm_ioremap_resource(&pdev->dev, res);\n"
        "\t\t\tif (IS_ERR(pctrl->regs[i]))\n"
        "\t\t\t\treturn PTR_ERR(pctrl->regs[i]);\n"
        "\t\t}\n",
        "\t\tfor (i = 0; i < soc_data->ntiles; i++) {\n"
        "\t\t\tres = platform_get_resource_byname(pdev, IORESOURCE_MEM,\n"
        "\t\t\t\t\t\t\t   soc_data->tiles[i]);\n"
        "\t\t\tA52_PINTRACE(pctrl, \"PINCTRL msm map tile=%d res=%u\",\n"
        "\t\t\t\ti, res != NULL);\n"
        "\t\t\tpctrl->regs[i] = devm_ioremap_resource(&pdev->dev, res);\n"
        "\t\t\tA52_PINTRACE(pctrl, \"PINCTRL msm map tile=%d rc=%ld\", i,\n"
        "\t\t\t\tIS_ERR(pctrl->regs[i]) ? PTR_ERR(pctrl->regs[i]) : 0L);\n"
        "\t\t\tif (IS_ERR(pctrl->regs[i]))\n"
        "\t\t\t\treturn PTR_ERR(pctrl->regs[i]);\n"
        "\t\t}\n",
        "tile mapping",
    )

    text = one(
        text,
        "\t} else {\n"
        "\t\tres = platform_get_resource(pdev, IORESOURCE_MEM, 0);\n"
        "\t\tpctrl->regs[0] = devm_ioremap_resource(&pdev->dev, res);\n"
        "\t\tif (IS_ERR(pctrl->regs[0]))\n"
        "\t\t\treturn PTR_ERR(pctrl->regs[0]);\n\n"
        "\t\tpctrl->phys_base[0] = res->start;\n"
        "\t}\n\n"
        "\tmsm_pinctrl_setup_pm_reset(pctrl);\n",
        "\t} else {\n"
        "\t\tres = platform_get_resource(pdev, IORESOURCE_MEM, 0);\n"
        "\t\tA52_PINTRACE(pctrl, \"PINCTRL msm map single res=%u\", res != NULL);\n"
        "\t\tpctrl->regs[0] = devm_ioremap_resource(&pdev->dev, res);\n"
        "\t\tA52_PINTRACE(pctrl, \"PINCTRL msm map single rc=%ld\",\n"
        "\t\t\tIS_ERR(pctrl->regs[0]) ? PTR_ERR(pctrl->regs[0]) : 0L);\n"
        "\t\tif (IS_ERR(pctrl->regs[0]))\n"
        "\t\t\treturn PTR_ERR(pctrl->regs[0]);\n\n"
        "\t\tpctrl->phys_base[0] = res->start;\n"
        "\t}\n\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm pmreset enter\");\n"
        "\tmsm_pinctrl_setup_pm_reset(pctrl);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm pmreset exit\");\n",
        "single map and PM reset",
    )

    text = one(
        text,
        "\tpctrl->irq = platform_get_irq(pdev, 0);\n"
        "\tif (pctrl->irq < 0)\n"
        "\t\treturn pctrl->irq;\n",
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm irq enter\");\n"
        "\tpctrl->irq = platform_get_irq(pdev, 0);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm irq exit rc=%d\", pctrl->irq);\n"
        "\tif (pctrl->irq < 0)\n"
        "\t\treturn pctrl->irq;\n",
        "platform IRQ",
    )

    text = one(
        text,
        "\tpctrl->pctrl = devm_pinctrl_register(&pdev->dev, &pctrl->desc, pctrl);\n"
        "\tif (IS_ERR(pctrl->pctrl)) {\n",
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm pctl-register enter\");\n"
        "\tpctrl->pctrl = devm_pinctrl_register(&pdev->dev, &pctrl->desc, pctrl);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm pctl-register exit rc=%ld\",\n"
        "\t\tIS_ERR(pctrl->pctrl) ? PTR_ERR(pctrl->pctrl) : 0L);\n"
        "\tif (IS_ERR(pctrl->pctrl)) {\n",
        "pinctrl registration",
    )

    text = one(
        text,
        "\tret = msm_gpio_init(pctrl);\n"
        "\tif (ret)\n"
        "\t\treturn ret;\n\n"
        "\tplatform_set_drvdata(pdev, pctrl);\n",
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm gpio-init enter\");\n"
        "\tret = msm_gpio_init(pctrl);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm gpio-init exit rc=%d\", ret);\n"
        "\tif (ret)\n"
        "\t\treturn ret;\n\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm drvdata enter\");\n"
        "\tplatform_set_drvdata(pdev, pctrl);\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm drvdata exit\");\n",
        "GPIO init and drvdata",
    )

    text = one(
        text,
        "\tdev_dbg(&pdev->dev, \"Probed Qualcomm pinctrl driver\\n\");\n\n"
        "\treturn 0;\n",
        "\tdev_dbg(&pdev->dev, \"Probed Qualcomm pinctrl driver\\n\");\n"
        "\tA52_PINTRACE(pctrl, \"PINCTRL msm probe exit rc=0\");\n\n"
        "\treturn 0;\n",
        "probe completion",
    )
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    path = args.root / "drivers/pinctrl/qcom/pinctrl-msm.c"
    text = path.read_text(encoding="utf-8")
    text = add_recorder_decl(text)
    text = patch_trace_helper(text)
    text = patch_gpio_init(text)
    text = patch_probe(text)
    path.write_text(text, encoding="utf-8")
    print("phase188 Qualcomm pinctrl stage instrumentation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
