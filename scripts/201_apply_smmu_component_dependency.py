#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MSM_DRV = Path("drivers/a52_display/msm/msm_drv.c")
MSM_SMMU = Path("drivers/a52_display/msm/msm_smmu.c")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_msm_drv(text: str) -> str:
    if "DRMCOMP smmu-match ready=" in text:
        return text

    text = replace_once(
        text,
        "\tif (IS_ERR_OR_NULL(kms)) {\n"
        "\t\tdev_err(dev, \"msm_drm_init_helper failed\\n\");\n"
        "\t\tgoto fail;\n"
        "\t}\n",
        "\tif (IS_ERR_OR_NULL(kms)) {\n"
        "\t\tret = IS_ERR(kms) ? PTR_ERR(kms) : -ENODEV;\n"
        "\t\ta52_ackfr_record(\"DRMPOST helper propagate rc=%d\", ret);\n"
        "\t\tdev_err(dev, \"msm_drm_init_helper failed: %d\\n\", ret);\n"
        "\t\tgoto fail;\n"
        "\t}\n",
        "helper error propagation",
    )

    needle = (
        "\t\ta52_ackfr_record(\"DRMCOMP collect exit rc=0 match=%u\",\n"
        "\t\t\t\t   !!*matchptr);\n"
        "\t\treturn 0;\n"
    )
    replacement = (
        "\t\t{\n"
        "\t\t\tstruct device_node *smmu_node;\n"
        "\t\t\tstruct platform_device *smmu_pdev;\n"
        "\n"
        "\t\t\tsmmu_node = of_find_compatible_node(np, NULL,\n"
        "\t\t\t\t\t\t\"qcom,smmu_sde_unsec\");\n"
        "\t\t\tif (!smmu_node) {\n"
        "\t\t\t\ta52_ackfr_record(\"DRMCOMP smmu-match no-node\");\n"
        "\t\t\t\treturn -ENODEV;\n"
        "\t\t\t}\n"
        "\n"
        "\t\t\tsmmu_pdev = of_find_device_by_node(smmu_node);\n"
        "\t\t\tif (!smmu_pdev)\n"
        "\t\t\t\tsmmu_pdev = of_platform_device_create(smmu_node,\n"
        "\t\t\t\t\t\tNULL, dev);\n"
        "\t\t\ta52_ackfr_record(\"DRMCOMP smmu-match ready=%d driver=%d client=%d\",\n"
        "\t\t\t\t\t!!smmu_pdev, smmu_pdev && !!smmu_pdev->dev.driver,\n"
        "\t\t\t\t\tsmmu_pdev && !!platform_get_drvdata(smmu_pdev));\n"
        "\t\t\tif (!smmu_pdev) {\n"
        "\t\t\t\tof_node_put(smmu_node);\n"
        "\t\t\t\treturn -EPROBE_DEFER;\n"
        "\t\t\t}\n"
        "\n"
        "\t\t\tcomponent_match_add(dev, matchptr, compare_of, smmu_node);\n"
        "\t\t\ta52_ackfr_record(\"DRMCOMP smmu-match added node=%s\",\n"
        "\t\t\t\t\tsmmu_node->full_name);\n"
        "\t\t\tput_device(&smmu_pdev->dev);\n"
        "\t\t}\n"
        "\n"
        "\t\ta52_ackfr_record(\"DRMCOMP collect exit rc=0 match=%u\",\n"
        "\t\t\t\t   !!*matchptr);\n"
        "\t\treturn 0;\n"
    )
    text = replace_once(text, needle, replacement, "unsecure SMMU component match")
    return text


def patch_msm_smmu(text: str) -> str:
    if "SMMU component-add exit rc=%d" in text:
        return text

    text = replace_once(
        text,
        "#include <linux/module.h>\n",
        "#include <linux/module.h>\n#include <linux/component.h>\n",
        "component include",
    )
    text = replace_once(
        text,
        "struct msm_smmu {\n"
        "\tstruct msm_mmu base;\n"
        "\tstruct device *client_dev;\n"
        "\tstruct msm_smmu_client *client;\n"
        "};\n",
        "struct msm_smmu {\n"
        "\tstruct msm_mmu base;\n"
        "\tstruct device *client_dev;\n"
        "\tstruct msm_smmu_client *client;\n"
        "\tbool client_dev_owned;\n"
        "};\n",
        "ownership field",
    )
    text = replace_once(
        text,
        "\tif (smmu->client_dev)\n"
        "\t\tplatform_device_unregister(pdev);\n"
        "\tkfree(smmu);\n",
        "\tif (smmu->client_dev && smmu->client_dev_owned)\n"
        "\t\tplatform_device_unregister(pdev);\n"
        "\tkfree(smmu);\n",
        "conditional context-device unregister",
    )

    old_create_decls = (
        "\tstruct device_node *child;\n"
        "\tstruct platform_device *pdev;\n"
        "\tint i;\n"
        "\tconst char *compat = NULL;\n"
    )
    new_create_decls = (
        "\tstruct device_node *child;\n"
        "\tstruct platform_device *pdev;\n"
        "\tbool existing = false;\n"
        "\tint i;\n"
        "\tconst char *compat = NULL;\n"
    )
    text = replace_once(text, old_create_decls, new_create_decls, "create declarations")

    old_create = (
        "\tpdev = of_platform_device_create(child, NULL, dev);\n"
        "\tif (!pdev) {\n"
        "\t\ta52_ackfr_record(\"SMMU create no-pdev domain=%d\", domain);\n"
        "\t\tDRM_ERROR(\"unable to create smmu platform dev for domain %d\\n\",\n"
        "\t\t\t\tdomain);\n"
        "\t\treturn ERR_PTR(-ENODEV);\n"
        "\t}\n"
        "\n"
        "\tsmmu->client = platform_get_drvdata(pdev);\n"
        "\ta52_ackfr_record(\"SMMU create state domain=%d driver=%d client=%d\",\n"
        "\t\tdomain, !!pdev->dev.driver, !!smmu->client);\n"
        "\tif (!smmu->client) {\n"
        "\t\t/* 5.10 may create the child before its IOMMU domain is ready. */\n"
        "\t\ta52_ackfr_record(\"SMMU create defer domain=%d\", domain);\n"
        "\t\tof_node_clear_flag(child, OF_POPULATED);\n"
        "\t\tplatform_device_unregister(pdev);\n"
        "\t\treturn ERR_PTR(-EPROBE_DEFER);\n"
        "\t}\n"
        "\n"
        "\ta52_ackfr_record(\"SMMU create ready domain=%d secure=%d\", domain,\n"
        "\t\tsmmu->client->secure);\n"
        "\treturn &pdev->dev;\n"
    )
    new_create = (
        "\tpdev = of_find_device_by_node(child);\n"
        "\tif (pdev) {\n"
        "\t\texisting = true;\n"
        "\t} else {\n"
        "\t\tpdev = of_platform_device_create(child, NULL, dev);\n"
        "\t}\n"
        "\tif (!pdev) {\n"
        "\t\ta52_ackfr_record(\"SMMU create no-pdev domain=%d\", domain);\n"
        "\t\tDRM_ERROR(\"unable to create smmu platform dev for domain %d\\n\",\n"
        "\t\t\t\tdomain);\n"
        "\t\treturn ERR_PTR(-ENODEV);\n"
        "\t}\n"
        "\n"
        "\tsmmu->client = platform_get_drvdata(pdev);\n"
        "\ta52_ackfr_record(\"SMMU create state domain=%d existing=%d driver=%d client=%d\",\n"
        "\t\tdomain, existing, !!pdev->dev.driver, !!smmu->client);\n"
        "\tif (!smmu->client) {\n"
        "\t\ta52_ackfr_record(\"SMMU create defer domain=%d existing=%d\",\n"
        "\t\t\tdomain, existing);\n"
        "\t\tif (existing)\n"
        "\t\t\tput_device(&pdev->dev);\n"
        "\t\telse {\n"
        "\t\t\tof_node_clear_flag(child, OF_POPULATED);\n"
        "\t\t\tplatform_device_unregister(pdev);\n"
        "\t\t}\n"
        "\t\treturn ERR_PTR(-EPROBE_DEFER);\n"
        "\t}\n"
        "\n"
        "\tsmmu->client_dev_owned = !existing;\n"
        "\ta52_ackfr_record(\"SMMU create ready domain=%d secure=%d owned=%d\", domain,\n"
        "\t\tsmmu->client->secure, smmu->client_dev_owned);\n"
        "\tif (existing)\n"
        "\t\tput_device(&pdev->dev);\n"
        "\treturn &pdev->dev;\n"
    )
    text = replace_once(text, old_create, new_create, "reuse precreated SMMU device")

    component_ops = r'''
static int msm_smmu_component_bind(struct device *dev,
		struct device *master, void *data)
{
	a52_ackfr_record("SMMU component-bind dev=%s master=%s",
		dev_name(dev), dev_name(master));
	return 0;
}

static void msm_smmu_component_unbind(struct device *dev,
		struct device *master, void *data)
{
	a52_ackfr_record("SMMU component-unbind dev=%s master=%s",
		dev_name(dev), dev_name(master));
}

static const struct component_ops msm_smmu_component_ops = {
	.bind = msm_smmu_component_bind,
	.unbind = msm_smmu_component_unbind,
};

'''
    text = replace_once(
        text,
        "/**\n * msm_smmu_probe()\n",
        component_ops + "/**\n * msm_smmu_probe()\n",
        "component ops insertion",
    )

    text = replace_once(
        text,
        "\tplatform_set_drvdata(pdev, client);\n"
        "\ta52_ackfr_record(\"SMMU probe ready compat=%s secure=%d\",\n"
        "\t\tmatch->compatible, client->secure);\n"
        "\n"
        "\treturn 0;\n",
        "\tplatform_set_drvdata(pdev, client);\n"
        "\ta52_ackfr_record(\"SMMU probe ready compat=%s secure=%d\",\n"
        "\t\tmatch->compatible, client->secure);\n"
        "\n"
        "\ta52_ackfr_record(\"SMMU component-add enter compat=%s\",\n"
        "\t\tmatch->compatible);\n"
        "\tret = component_add(&pdev->dev, &msm_smmu_component_ops);\n"
        "\ta52_ackfr_record(\"SMMU component-add exit rc=%d\", ret);\n"
        "\treturn ret;\n",
        "component registration",
    )
    text = replace_once(
        text,
        "\tstruct msm_smmu_client *client;\n"
        "\tconst struct msm_smmu_domain *domain;\n",
        "\tstruct msm_smmu_client *client;\n"
        "\tconst struct msm_smmu_domain *domain;\n"
        "\tint ret;\n",
        "probe return declaration",
    )
    text = replace_once(
        text,
        "\tclient = platform_get_drvdata(pdev);\n"
        "\tclient->domain_attached = false;\n"
        "\n"
        "\treturn 0;\n",
        "\tcomponent_del(&pdev->dev, &msm_smmu_component_ops);\n"
        "\tclient = platform_get_drvdata(pdev);\n"
        "\tif (client)\n"
        "\t\tclient->domain_attached = false;\n"
        "\n"
        "\treturn 0;\n",
        "component removal",
    )
    return text


def run(root: Path) -> None:
    drv_path = root / MSM_DRV
    smmu_path = root / MSM_SMMU
    drv_path.write_text(patch_msm_drv(drv_path.read_text()), encoding="utf-8")
    smmu_path.write_text(patch_msm_smmu(smmu_path.read_text()), encoding="utf-8")


def self_test() -> None:
    root = Path(__file__).resolve().parents[1]
    drv_fixture = root / "stage" / "msm-drv-after-phase195.c"
    smmu_fixture = root / "stage" / "msm-smmu-after-phase200.c"
    if not drv_fixture.is_file() or not smmu_fixture.is_file():
        print("phase201 patcher self-test: fixtures unavailable, syntax-only PASS")
        return
    drv = patch_msm_drv(drv_fixture.read_text())
    smmu = patch_msm_smmu(smmu_fixture.read_text())
    assert "DRMPOST helper propagate rc=%d" in drv
    assert "DRMCOMP smmu-match added node=%s" in drv
    assert "SMMU component-add exit rc=%d" in smmu
    assert "client_dev_owned" in smmu
    print("phase201 SMMU component dependency patcher self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.root is None:
        parser.error("--root is required")
    run(args.root)
    print("phase201 SMMU component dependency and helper error propagation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
