#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE397_DOWNSTREAM_BUS_VOTES_V1"
UFS = Path("drivers/scsi/ufs/ufs-qcom.c")
USB = Path("drivers/usb/dwc3/dwc3-qcom.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase397 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch_ufs(text: str) -> str:
    if "A52_PHASE397_UFS_BUS_VOTE_V1" in text:
        return text

    text = one(
        text,
        "#include <linux/devfreq.h>\n",
        "#include <linux/devfreq.h>\n#include <linux/msm-bus.h>\n",
        "UFS msm-bus include",
    )

    anchor = "static struct ufs_qcom_host *ufs_qcom_hosts[MAX_UFS_QCOM_HOSTS];\n"
    block = r'''

/* A52_PHASE397_UFS_BUS_VOTE_V1
 *
 * The shipped A52 DT retains the downstream qcom,msm-bus UFS contract while
 * the 5.10 ufs-qcom port had lost the corresponding client registration.
 * Phase397 deliberately holds the highest DT use-case for the entire UFS
 * lifetime.  This is an A/B correctness experiment, not the final power
 * policy: it removes under-voting as a variable without guessing at 5.10
 * link-state transition semantics.
 */
struct a52_p397_ufs_bus_state {
	u32 client;
	unsigned int max_vote;
	bool registered;
};

static struct a52_p397_ufs_bus_state a52_p397_ufs_bus;

static int a52_p397_ufs_bus_register(struct platform_device *pdev)
{
	struct msm_bus_scale_pdata *pdata;
	int ret;

	pdata = msm_bus_cl_get_pdata(pdev);
	if (!pdata || pdata->num_usecases <= 0) {
		dev_err(&pdev->dev,
			"A52 P397 UFS_BUS missing downstream bus table\n");
		return -ENODATA;
	}

	a52_p397_ufs_bus.client = msm_bus_scale_register_client(pdata);
	if (!a52_p397_ufs_bus.client) {
		dev_err(&pdev->dev,
			"A52 P397 UFS_BUS client registration failed\n");
		return -EPROBE_DEFER;
	}

	a52_p397_ufs_bus.max_vote = pdata->num_usecases - 1;
	ret = msm_bus_scale_client_update_request(a52_p397_ufs_bus.client,
						 a52_p397_ufs_bus.max_vote);
	if (ret) {
		dev_err(&pdev->dev,
			"A52 P397 UFS_BUS MAX vote=%u failed ret=%d\n",
			a52_p397_ufs_bus.max_vote, ret);
		msm_bus_scale_unregister_client(a52_p397_ufs_bus.client);
		a52_p397_ufs_bus.client = 0;
		return ret;
	}

	a52_p397_ufs_bus.registered = true;
	dev_info(&pdev->dev,
		 "A52 P397 UFS_BUS active client=%u max_vote=%u cases=%d\n",
		 a52_p397_ufs_bus.client, a52_p397_ufs_bus.max_vote,
		 pdata->num_usecases);
	a52_persistent_diag_mark(
		"A52 P397 UFS_BUS active client=%u max=%u cases=%d ret=0\n",
		a52_p397_ufs_bus.client, a52_p397_ufs_bus.max_vote,
		pdata->num_usecases);
	return 0;
}

static void a52_p397_ufs_bus_unregister(struct device *dev)
{
	if (!a52_p397_ufs_bus.registered)
		return;

	msm_bus_scale_unregister_client(a52_p397_ufs_bus.client);
	dev_info(dev, "A52 P397 UFS_BUS unregister client=%u\n",
		 a52_p397_ufs_bus.client);
	a52_p397_ufs_bus.client = 0;
	a52_p397_ufs_bus.max_vote = 0;
	a52_p397_ufs_bus.registered = false;
}
'''
    text = one(text, anchor, anchor + block, "UFS helper insertion")

    init_anchor = (
        "\t/* Make a two way bind between the qcom host and the hba */\n"
        "\thost->hba = hba;\n"
        "\tufshcd_set_variant(hba, host);\n"
    )
    init_new = init_anchor + (
        "\n\t/* Phase397: restore the downstream DT bus contract before UFS traffic. */\n"
        "\terr = a52_p397_ufs_bus_register(pdev);\n"
        "\tif (err)\n"
        "\t\tgoto out_variant_clear;\n"
    )
    text = one(text, init_anchor, init_new, "UFS init registration")

    clear_anchor = "out_variant_clear:\n\tufshcd_set_variant(hba, NULL);\n"
    clear_new = (
        "out_variant_clear:\n"
        "\ta52_p397_ufs_bus_unregister(dev);\n"
        "\tufshcd_set_variant(hba, NULL);\n"
    )
    text = one(text, clear_anchor, clear_new, "UFS failure cleanup")

    exit_anchor = (
        "static void ufs_qcom_exit(struct ufs_hba *hba)\n"
        "{\n"
        "\tstruct ufs_qcom_host *host = ufshcd_get_variant(hba);\n\n"
    )
    exit_new = exit_anchor + "\ta52_p397_ufs_bus_unregister(hba->dev);\n"
    text = one(text, exit_anchor, exit_new, "UFS exit cleanup")
    return text


def patch_usb(text: str) -> str:
    if "A52_PHASE397_USB_BUS_VOTE_V1" in text:
        return text

    text = one(
        text,
        "#include <linux/interconnect.h>\n",
        "#include <linux/interconnect.h>\n#include <linux/msm-bus.h>\n",
        "USB msm-bus include",
    )

    struct_anchor = (
        "\tstruct icc_path\t\t*icc_path_ddr;\n"
        "\tstruct icc_path\t\t*icc_path_apps;\n"
    )
    struct_new = struct_anchor + (
        "\tu32\t\t\ta52_p397_bus_client;\n"
        "\tunsigned int\t\ta52_p397_bus_max_vote;\n"
        "\tbool\t\t\ta52_p397_legacy_bus;\n"
    )
    text = one(text, struct_anchor, struct_new, "USB state fields")

    helper_anchor = (
        "static inline void a52_r386_stage(unsigned int stage, int ret)\n"
        "{\n"
        "\ta52_ackfr_sticky385_ofsimple(stage, ret);\n"
        "}\n"
    )
    helper_block = r'''

/* A52_PHASE397_USB_BUS_VOTE_V1
 * The Samsung qcom,dwc-usb3-msm node still supplies qcom,msm-bus vectors.
 * The upstream dwc3-qcom ICC names do not exist in that DT.  For the A/B
 * experiment keep the legacy path at its highest use-case for the device
 * lifetime, while preserving upstream ICC behavior for non-Samsung nodes.
 */
static int a52_p397_usb_bus_register(struct platform_device *pdev,
				     struct dwc3_qcom *qcom)
{
	struct msm_bus_scale_pdata *pdata;
	int ret;

	pdata = msm_bus_cl_get_pdata(pdev);
	if (!pdata || pdata->num_usecases <= 0) {
		dev_err(&pdev->dev,
			"A52 P397 USB_BUS missing downstream bus table\n");
		return -ENODATA;
	}

	qcom->a52_p397_bus_client = msm_bus_scale_register_client(pdata);
	if (!qcom->a52_p397_bus_client) {
		dev_err(&pdev->dev,
			"A52 P397 USB_BUS client registration failed\n");
		return -EPROBE_DEFER;
	}

	qcom->a52_p397_bus_max_vote = pdata->num_usecases - 1;
	ret = msm_bus_scale_client_update_request(qcom->a52_p397_bus_client,
						 qcom->a52_p397_bus_max_vote);
	if (ret) {
		dev_err(&pdev->dev,
			"A52 P397 USB_BUS MAX vote=%u failed ret=%d\n",
			qcom->a52_p397_bus_max_vote, ret);
		msm_bus_scale_unregister_client(qcom->a52_p397_bus_client);
		qcom->a52_p397_bus_client = 0;
		return ret;
	}

	qcom->a52_p397_legacy_bus = true;
	dev_info(&pdev->dev,
		 "A52 P397 USB_BUS active client=%u max_vote=%u cases=%d\n",
		 qcom->a52_p397_bus_client, qcom->a52_p397_bus_max_vote,
		 pdata->num_usecases);
	return 0;
}

static void a52_p397_usb_bus_unregister(struct dwc3_qcom *qcom)
{
	if (!qcom->a52_p397_legacy_bus)
		return;

	msm_bus_scale_unregister_client(qcom->a52_p397_bus_client);
	dev_info(qcom->dev, "A52 P397 USB_BUS unregister client=%u\n",
		 qcom->a52_p397_bus_client);
	qcom->a52_p397_bus_client = 0;
	qcom->a52_p397_bus_max_vote = 0;
	qcom->a52_p397_legacy_bus = false;
}
'''
    text = one(text, helper_anchor, helper_anchor + helper_block,
               "USB helper insertion")

    icc_enable_anchor = "static int dwc3_qcom_interconnect_enable(struct dwc3_qcom *qcom)\n{\n\tint ret;\n"
    icc_enable_new = icc_enable_anchor + "\n\tif (qcom->a52_p397_legacy_bus)\n\t\treturn 0;\n"
    text = one(text, icc_enable_anchor, icc_enable_new, "USB ICC enable bypass")

    icc_disable_anchor = "static int dwc3_qcom_interconnect_disable(struct dwc3_qcom *qcom)\n{\n\tint ret;\n"
    icc_disable_new = icc_disable_anchor + "\n\tif (qcom->a52_p397_legacy_bus)\n\t\treturn 0;\n"
    text = one(text, icc_disable_anchor, icc_disable_new, "USB ICC disable bypass")

    icc_init_anchor = (
        "static int dwc3_qcom_interconnect_init(struct dwc3_qcom *qcom)\n"
        "{\n"
        "\tstruct device *dev = qcom->dev;\n"
        "\tint ret;\n\n"
        "\tif (has_acpi_companion(dev))\n"
        "\t\treturn 0;\n"
    )
    icc_init_new = icc_init_anchor + (
        "\n\tif (dev->of_node &&\n"
        "\t    of_device_is_compatible(dev->of_node, \"qcom,dwc-usb3-msm\"))\n"
        "\t\treturn a52_p397_usb_bus_register(to_platform_device(dev), qcom);\n"
    )
    text = one(text, icc_init_anchor, icc_init_new, "USB legacy init route")

    icc_exit_anchor = (
        "static void dwc3_qcom_interconnect_exit(struct dwc3_qcom *qcom)\n"
        "{\n"
        "\ticc_put(qcom->icc_path_ddr);\n"
        "\ticc_put(qcom->icc_path_apps);\n"
        "}\n"
    )
    icc_exit_new = (
        "static void dwc3_qcom_interconnect_exit(struct dwc3_qcom *qcom)\n"
        "{\n"
        "\tif (qcom->a52_p397_legacy_bus) {\n"
        "\t\ta52_p397_usb_bus_unregister(qcom);\n"
        "\t\treturn;\n"
        "\t}\n"
        "\ticc_put(qcom->icc_path_ddr);\n"
        "\ticc_put(qcom->icc_path_apps);\n"
        "}\n"
    )
    text = one(text, icc_exit_anchor, icc_exit_new, "USB exit route")
    return text


def validate(root: Path) -> None:
    ufs = (root / UFS).read_text(errors="replace")
    usb = (root / USB).read_text(errors="replace")

    for token in (
        "A52_PHASE397_UFS_BUS_VOTE_V1",
        "#include <linux/msm-bus.h>",
        "a52_p397_ufs_bus_register(pdev)",
        "msm_bus_scale_register_client(pdata)",
        "pdata->num_usecases - 1",
        "A52 P397 UFS_BUS active",
    ):
        if token not in ufs:
            raise SystemExit("Phase397 UFS token missing: " + token)

    for token in (
        "A52_PHASE397_USB_BUS_VOTE_V1",
        "#include <linux/msm-bus.h>",
        "of_device_is_compatible(dev->of_node, \"qcom,dwc-usb3-msm\")",
        "a52_p397_usb_bus_register(to_platform_device(dev), qcom)",
        "pdata->num_usecases - 1",
        "A52 P397 USB_BUS active",
    ):
        if token not in usb:
            raise SystemExit("Phase397 USB token missing: " + token)

    if "A52_PHASE396_NOC_IRQ_EVIDENCE_V1" not in (root / Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")).read_text(errors="replace"):
        raise SystemExit("Phase397 requires inherited Phase396 observer")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root

    if ns.check_only:
        validate(root)
        print("Phase397 downstream UFS/USB bus-vote audit: PASS")
        return 0

    paths = ((UFS, patch_ufs), (USB, patch_usb))
    for rel, fn in paths:
        p = root / rel
        if not p.is_file():
            raise SystemExit("Phase397 missing source: " + str(rel))
        p.write_text(fn(p.read_text(errors="replace")))

    validate(root)
    print("Phase397 downstream UFS/USB bus-vote restore applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())