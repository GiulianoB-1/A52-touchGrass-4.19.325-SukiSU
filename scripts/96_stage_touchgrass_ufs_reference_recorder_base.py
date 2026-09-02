#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


HEADER = r'''#ifndef _LINUX_TGREF_RECORDER_H
#define _LINUX_TGREF_RECORDER_H

#include <linux/compiler.h>

void tgref_record(const char *fmt, ...) __printf(1, 2);

#endif
'''

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * A52 TouchGrass UFS reference recorder.
 *
 * Diagnostic-only helper for the known-good Linux 4.19.206 boot. UFS bring-up
 * breadcrumbs are kept in a small in-kernel text buffer and copied once to
 * /data/local/tmp/tgref_ufs.log after Android has mounted /data.
 */
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/fs.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/namei.h>
#include <linux/printk.h>
#include <linux/sizes.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/timekeeping.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>

#include <linux/tgref_recorder.h>

#define TGREF_BUFFER_SIZE SZ_128K
#define TGREF_LINE_SIZE 384
#define TGREF_DUMP_PATH "/data/local/tmp/tgref_ufs.log"
#define TGREF_DATA_DIR "/data/local/tmp"
#define TGREF_POLL_MS 5000
#define TGREF_SETTLE_MS 30000
#define TGREF_MAX_POLLS 120

static char tgref_buffer[TGREF_BUFFER_SIZE];
static size_t tgref_length;
static unsigned int tgref_dropped;
static DEFINE_SPINLOCK(tgref_lock);

void tgref_record(const char *fmt, ...)
{
	char line[TGREF_LINE_SIZE];
	unsigned long flags;
	u64 ns;
	va_list args;
	int prefix;
	int body;
	int total;

	ns = ktime_get_ns();
	prefix = scnprintf(line, sizeof(line), "TGREF %llu.%06llu ",
			   (unsigned long long)(ns / NSEC_PER_SEC),
			   (unsigned long long)((ns % NSEC_PER_SEC) / NSEC_PER_USEC));

	va_start(args, fmt);
	body = vscnprintf(line + prefix, sizeof(line) - prefix, fmt, args);
	va_end(args);

	total = prefix + body;
	if (total <= 0)
		return;
	if (line[total - 1] != '\n' && total < sizeof(line) - 1)
		line[total++] = '\n';
	line[total] = '\0';

	spin_lock_irqsave(&tgref_lock, flags);
	if (tgref_length + total <= sizeof(tgref_buffer)) {
		memcpy(tgref_buffer + tgref_length, line, total);
		tgref_length += total;
	} else {
		tgref_dropped++;
	}
	spin_unlock_irqrestore(&tgref_lock, flags);

	printk(KERN_INFO "%s", line);
}
EXPORT_SYMBOL_GPL(tgref_record);

static int tgref_snapshot(char **out, size_t *out_len)
{
	unsigned long flags;
	char *snapshot;
	size_t len;
	unsigned int dropped;
	int header;

	snapshot = vmalloc(TGREF_BUFFER_SIZE + 128);
	if (!snapshot)
		return -ENOMEM;

	spin_lock_irqsave(&tgref_lock, flags);
	len = tgref_length;
	dropped = tgref_dropped;
	memcpy(snapshot + 128, tgref_buffer, len);
	spin_unlock_irqrestore(&tgref_lock, flags);

	header = scnprintf(snapshot, 128,
			   "TGREF reference=touchGrass-4.19.206 bytes=%zu dropped=%u\n",
			   len, dropped);
	memmove(snapshot + header, snapshot + 128, len);
	*out = snapshot;
	*out_len = header + len;
	return 0;
}

static int tgref_write_snapshot(void)
{
	struct file *file;
	char *snapshot;
	size_t length;
	size_t written = 0;
	loff_t pos = 0;
	int ret;

	ret = tgref_snapshot(&snapshot, &length);
	if (ret)
		return ret;

	file = filp_open(TGREF_DUMP_PATH,
			 O_WRONLY | O_CREAT | O_TRUNC | O_LARGEFILE, 0644);
	if (IS_ERR(file)) {
		ret = PTR_ERR(file);
		vfree(snapshot);
		return ret;
	}

	while (written < length) {
		ssize_t step = kernel_write(file, snapshot + written,
					    length - written, &pos);
		if (step < 0) {
			ret = step;
			goto out;
		}
		if (!step) {
			ret = -EIO;
			goto out;
		}
		written += step;
	}

	ret = vfs_fsync(file, 0);
out:
	filp_close(file, NULL);
	vfree(snapshot);
	return ret;
}

static int tgref_dump_thread(void *unused)
{
	struct path path;
	int poll;
	int ret = -ENOENT;

	tgref_record("DUMPER armed path=%s", TGREF_DUMP_PATH);

	for (poll = 0; poll < TGREF_MAX_POLLS && !kthread_should_stop(); poll++) {
		ret = kern_path(TGREF_DATA_DIR,
				LOOKUP_FOLLOW | LOOKUP_DIRECTORY, &path);
		if (!ret) {
			path_put(&path);
			tgref_record("DUMPER data_ready poll=%d settle_ms=%d",
				     poll, TGREF_SETTLE_MS);
			msleep(TGREF_SETTLE_MS);
			ret = tgref_write_snapshot();
			if (!ret) {
				printk(KERN_INFO
				       "TGREF DUMPER saved %s\n", TGREF_DUMP_PATH);
				return 0;
			}
			tgref_record("DUMPER write_failed ret=%d retrying", ret);
		}
		msleep(TGREF_POLL_MS);
	}

	tgref_record("DUMPER gave_up ret=%d polls=%d", ret, poll);
	return ret;
}

static int __init tgref_recorder_init(void)
{
	struct task_struct *task;

	tgref_record("RECORDER ready buffer=%u dump=%s",
		     (unsigned int)TGREF_BUFFER_SIZE, TGREF_DUMP_PATH);
	task = kthread_run(tgref_dump_thread, NULL, "tgref_ufs_dump");
	if (IS_ERR(task)) {
		int ret = PTR_ERR(task);

		tgref_record("DUMPER thread_start_failed ret=%d", ret);
		return 0;
	}
	return 0;
}
late_initcall(tgref_recorder_init);

MODULE_DESCRIPTION("A52 TouchGrass UFS reference recorder and automatic dumper");
MODULE_LICENSE("GPL v2");
'''


def fail(label: str, detail: str) -> None:
    raise SystemExit(f"{label}: {detail}")


def function_bounds(text: str, anchor: str, label: str) -> tuple[int, int, int]:
    search = 0
    while True:
        start = text.find(anchor, search)
        if start < 0:
            fail(label, f"function anchor not found: {anchor}")
        brace = text.find("{", start)
        semicolon = text.find(";", start)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            break
        search = start + len(anchor)

    depth = 0
    for pos in range(brace, len(text)):
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, brace, pos + 1
    fail(label, "closing brace not found")


def add_include(text: str, anchor: str, label: str) -> str:
    include = "#include <linux/tgref_recorder.h>\n"
    if include in text:
        fail(label, "recorder include already present")
    count = text.count(anchor)
    if count != 1:
        fail(label, f"expected one include anchor, found {count}")
    return text.replace(anchor, anchor + include, 1)


def insert_after_fragment(
    text: str, function_anchor: str, fragment: str, insertion: str, label: str
) -> str:
    start, _, end = function_bounds(text, function_anchor, label)
    positions: list[int] = []
    cursor = start
    while True:
        pos = text.find(fragment, cursor, end)
        if pos < 0:
            break
        positions.append(pos)
        cursor = pos + len(fragment)
    if len(positions) != 1:
        fail(label, f"expected one fragment in function, found {len(positions)}")
    point = positions[0] + len(fragment)
    return text[:point] + insertion + text[point:]


def insert_before_fragment(
    text: str, function_anchor: str, fragment: str, insertion: str, label: str
) -> str:
    start, _, end = function_bounds(text, function_anchor, label)
    positions: list[int] = []
    cursor = start
    while True:
        pos = text.find(fragment, cursor, end)
        if pos < 0:
            break
        positions.append(pos)
        cursor = pos + len(fragment)
    if len(positions) != 1:
        fail(label, f"expected one fragment in function, found {len(positions)}")
    point = positions[0]
    return text[:point] + insertion + text[point:]


def insert_after_open(
    text: str, function_anchor: str, insertion: str, label: str
) -> str:
    _, brace, _ = function_bounds(text, function_anchor, label)
    return text[: brace + 1] + insertion + text[brace + 1 :]


def insert_before_last_return(
    text: str, function_anchor: str, return_fragment: str, insertion: str, label: str
) -> str:
    start, _, end = function_bounds(text, function_anchor, label)
    pos = text.rfind(return_fragment, start, end)
    if pos < 0:
        fail(label, "return fragment not found")
    return text[:pos] + insertion + text[pos:]


def replace_in_function(
    text: str,
    function_anchor: str,
    old: str,
    new: str,
    label: str,
) -> str:
    start, _, end = function_bounds(text, function_anchor, label)
    count = text.count(old, start, end)
    if count != 1:
        fail(label, f"expected one replacement target in function, found {count}")
    pos = text.find(old, start, end)
    return text[:pos] + new + text[pos + len(old) :]


def instrument_driver_core(text: str) -> str:
    text = add_include(
        text,
        "#include <linux/pinctrl/devinfo.h>\n",
        "driver core include",
    )
    helper = r'''
static bool tgref_relevant_probe(struct device *dev, struct device_driver *drv)
{
	const char *device = dev_name(dev);
	const char *driver = drv ? drv->name : "";

	return strstr(device, "1d84000") || strstr(device, "1d87000") ||
	       strstr(driver, "ufshcd-qcom") ||
	       strstr(driver, "ufs_qcom_phy_qmp_v3");
}

'''
    anchor = "static int really_probe(struct device *dev, struct device_driver *drv)\n"
    if text.count(anchor) != 1:
        fail("driver core helper", "really_probe anchor count is not one")
    text = text.replace(anchor, helper + anchor, 1)

    decl = (
        "\tbool test_remove = IS_ENABLED(CONFIG_DEBUG_TEST_DRIVER_REMOVE) &&\n"
        "\t\t\t   !drv->suppress_bind_attrs;\n"
    )
    text = insert_after_fragment(
        text,
        anchor,
        decl,
        "\tbool tgref_match = tgref_relevant_probe(dev, drv);\n\n"
        "\tif (tgref_match)\n"
        "\t\ttgref_record(\"DEV call device=%s driver=%s\", dev_name(dev), drv->name);\n",
        "driver core call",
    )

    suppliers = (
        "\tret = device_links_check_suppliers(dev);\n"
        "\tif (ret == -EPROBE_DEFER)\n"
        "\t\tdriver_deferred_probe_add_trigger(dev, local_trigger_count);\n"
        "\tif (ret)\n"
        "\t\treturn ret;\n"
    )
    suppliers_new = (
        "\tret = device_links_check_suppliers(dev);\n"
        "\tif (ret == -EPROBE_DEFER)\n"
        "\t\tdriver_deferred_probe_add_trigger(dev, local_trigger_count);\n"
        "\tif (ret) {\n"
        "\t\tif (tgref_match)\n"
        "\t\t\ttgref_record(\"DEV suppliers device=%s driver=%s ret=%d\",\n"
        "\t\t\t\t     dev_name(dev), drv->name, ret);\n"
        "\t\treturn ret;\n"
        "\t}\n"
    )
    text = replace_in_function(
        text, anchor, suppliers, suppliers_new, "driver core suppliers"
    )

    text = insert_before_fragment(
        text,
        anchor,
        "\tif (test_remove) {\n",
        "\tif (tgref_match)\n"
        "\t\ttgref_record(\"DEV probe_return device=%s driver=%s ret=%d\",\n"
        "\t\t\t     dev_name(dev), drv->name, ret);\n",
        "driver core probe return",
    )
    text = insert_after_fragment(
        text,
        anchor,
        "\tdriver_bound(dev);\n",
        "\tif (tgref_match)\n"
        "\t\ttgref_record(\"DEV bound device=%s driver=%s\", dev_name(dev), drv->name);\n",
        "driver core bound",
    )
    text = insert_before_fragment(
        text,
        anchor,
        "\tswitch (ret) {\n",
        "\tif (tgref_match)\n"
        "\t\ttgref_record(\"DEV failed device=%s driver=%s ret=%d\",\n"
        "\t\t\t     dev_name(dev), drv->name, ret);\n",
        "driver core failure",
    )
    return text


def instrument_qmp_v3(text: str) -> str:
    text = add_include(
        text,
        '#include "phy-qcom-ufs-qmp-v3.h"\n',
        "qmp v3 include",
    )
    init_anchor = "static int ufs_qcom_phy_qmp_v3_init(struct phy *generic_phy)\n"
    text = insert_after_fragment(
        text,
        init_anchor,
        "\tint err;\n",
        "\n\ttgref_record(\"PHY init_begin device=%s\", dev_name(phy_common->dev));\n",
        "qmp init begin",
    )
    text = insert_after_fragment(
        text,
        init_anchor,
        "\terr = ufs_qcom_phy_init_clks(phy_common);\n",
        "\ttgref_record(\"PHY init_clks ret=%d\", err);\n",
        "qmp clocks",
    )
    text = insert_after_fragment(
        text,
        init_anchor,
        "\terr = ufs_qcom_phy_init_vregulators(phy_common);\n",
        "\ttgref_record(\"PHY init_vregs ret=%d\", err);\n",
        "qmp vregs",
    )
    text = insert_before_last_return(
        text,
        init_anchor,
        "\treturn err;\n",
        "\ttgref_record(\"PHY init_end ret=%d\", err);\n",
        "qmp init end",
    )

    cal_anchor = "int ufs_qcom_phy_qmp_v3_phy_calibrate("
    text = insert_after_open(
        text,
        cal_anchor,
        "\n\ttgref_record(\"PHY calibrate_begin rate_b=%d gear4=%d\", is_rate_B, is_g4);\n",
        "qmp calibrate begin",
    )
    text = insert_before_last_return(
        text,
        cal_anchor,
        "\treturn 0;\n",
        "\ttgref_record(\"PHY calibrate_end lanes=%u\", ufs_qcom_phy->lanes_per_direction);\n",
        "qmp calibrate end",
    )

    pcs_anchor = "static int ufs_qcom_phy_qmp_v3_is_pcs_ready("
    text = insert_before_last_return(
        text,
        pcs_anchor,
        "\treturn err;\n",
        "\ttgref_record(\"PHY pcs_ready ret=%d val=0x%x\", err, val);\n",
        "qmp pcs",
    )

    probe_anchor = "static int ufs_qcom_phy_qmp_v3_probe(struct platform_device *pdev)\n"
    text = insert_after_fragment(
        text,
        probe_anchor,
        "\tint err = 0;\n",
        "\n\ttgref_record(\"PHY probe_begin device=%s\", dev_name(dev));\n",
        "qmp probe begin",
    )
    text = insert_before_fragment(
        text,
        probe_anchor,
        "\tif (!generic_phy) {\n",
        "\ttgref_record(\"PHY generic_probe pointer=%p\", generic_phy);\n",
        "qmp generic probe",
    )
    text = insert_before_last_return(
        text,
        probe_anchor,
        "\treturn err;\n",
        "\ttgref_record(\"PHY probe_end device=%s ret=%d\", dev_name(dev), err);\n",
        "qmp probe end",
    )
    return text


def instrument_phy_common(text: str) -> str:
    text = add_include(
        text,
        '#include "phy-qcom-ufs-i.h"\n',
        "phy common include",
    )

    generic_anchor = "struct phy *ufs_qcom_phy_generic_probe("
    text = insert_after_fragment(
        text,
        generic_anchor,
        "\tstruct phy_provider *phy_provider;\n",
        "\n\ttgref_record(\"PHY generic_begin device=%s\", dev_name(dev));\n",
        "phy generic begin",
    )
    text = insert_after_fragment(
        text,
        generic_anchor,
        "\terr = ufs_qcom_phy_base_init(pdev, common_cfg);\n",
        "\ttgref_record(\"PHY base_init ret=%d mmio=%p\", err, common_cfg->mmio);\n",
        "phy base",
    )
    text = insert_after_fragment(
        text,
        generic_anchor,
        "\tphy_provider = devm_of_phy_provider_register(dev, of_phy_simple_xlate);\n",
        "\ttgref_record(\"PHY provider pointer=%p\", phy_provider);\n",
        "phy provider",
    )
    text = insert_after_fragment(
        text,
        generic_anchor,
        "\tgeneric_phy = devm_phy_create(dev, NULL, ufs_qcom_phy_gen_ops);\n",
        "\ttgref_record(\"PHY create pointer=%p\", generic_phy);\n",
        "phy create",
    )
    text = insert_before_last_return(
        text,
        generic_anchor,
        "\treturn generic_phy;\n",
        "\ttgref_record(\"PHY generic_end device=%s phy=%p lanes=%u\",\n"
        "\t\t     dev_name(dev), generic_phy, common_cfg->lanes_per_direction);\n",
        "phy generic end",
    )

    clk_anchor = "static int __ufs_qcom_phy_clk_get("
    text = insert_before_last_return(
        text,
        clk_anchor,
        "\treturn err;\n",
        "\ttgref_record(\"PHY clk_get name=%s ret=%d clk=%p\", name, err,\n"
        "\t\t     err ? NULL : *clk_out);\n",
        "phy clk get",
    )

    vreg_anchor = "static int ufs_qcom_phy_init_vreg("
    text = insert_before_last_return(
        text,
        vreg_anchor,
        "\treturn err;\n",
        "\ttgref_record(\"PHY vreg_get name=%s ret=%d reg=%p\", name, err,\n"
        "\t\t     err ? NULL : vreg->reg);\n",
        "phy vreg get",
    )
    return text


def instrument_ufs_qcom(text: str) -> str:
    text = add_include(
        text,
        "#include <linux/clk/qcom.h>\n",
        "ufs qcom include",
    )

    get_anchor = "static int ufs_qcom_host_clk_get("
    text = insert_before_last_return(
        text,
        get_anchor,
        "\treturn err;\n",
        "\ttgref_record(\"UFS host_clk_get name=%s ret=%d clk=%p\", name, err,\n"
        "\t\t     err ? NULL : *clk_out);\n",
        "ufs host clk get",
    )
    enable_anchor = "static int ufs_qcom_host_clk_enable("
    text = insert_before_last_return(
        text,
        enable_anchor,
        "\treturn err;\n",
        "\ttgref_record(\"UFS host_clk_enable name=%s ret=%d rate=%lu\", name,\n"
        "\t\t     err, err ? 0UL : clk_get_rate(clk));\n",
        "ufs host clk enable",
    )

    power_anchor = "static int ufs_qcom_power_up_sequence(struct ufs_hba *hba)\n"
    text = insert_after_fragment(
        text,
        power_anchor,
        "\tint ret = 0;\n",
        "\n\ttgref_record(\"UFS power_up_begin lanes=%u gear4=%d\",\n"
        "\t\t     hba->lanes_per_direction, hba->phy_init_g4);\n",
        "ufs power begin",
    )
    text = insert_after_fragment(
        text,
        power_anchor,
        "\tufs_qcom_assert_reset(hba);\n",
        "\ttgref_record(\"UFS phy_reset_asserted\");\n",
        "ufs reset assert",
    )
    text = insert_after_fragment(
        text,
        power_anchor,
        "\tret = ufs_qcom_phy_calibrate_phy(phy, is_rate_B, hba->phy_init_g4);\n",
        "\ttgref_record(\"UFS phy_calibrate ret=%d rate_b=%d\", ret, is_rate_B);\n",
        "ufs calibrate",
    )
    text = insert_after_fragment(
        text,
        power_anchor,
        "\tufs_qcom_deassert_reset(hba);\n",
        "\ttgref_record(\"UFS phy_reset_deasserted\");\n",
        "ufs reset deassert",
    )
    text = insert_after_fragment(
        text,
        power_anchor,
        "\tret = ufs_qcom_phy_start_serdes(phy);\n",
        "\ttgref_record(\"UFS serdes_start ret=%d\", ret);\n",
        "ufs serdes",
    )
    text = insert_after_fragment(
        text,
        power_anchor,
        "\tret = ufs_qcom_phy_is_pcs_ready(phy);\n",
        "\ttgref_record(\"UFS pcs_ready ret=%d\", ret);\n",
        "ufs pcs",
    )
    text = insert_before_last_return(
        text,
        power_anchor,
        "\treturn ret;\n",
        "\ttgref_record(\"UFS power_up_end ret=%d\", ret);\n",
        "ufs power end",
    )

    probe_anchor = "static int ufs_qcom_probe(struct platform_device *pdev)\n"
    text = insert_after_fragment(
        text,
        probe_anchor,
        "\tstruct device_node *np = dev->of_node;\n",
        "\n\ttgref_record(\"UFS probe_begin device=%s bootdevice=%s\",\n"
        "\t\t     dev_name(dev), android_boot_dev);\n",
        "ufs probe begin",
    )
    text = insert_after_fragment(
        text,
        probe_anchor,
        "\terr = ufshcd_pltfrm_init(pdev, &ufs_hba_qcom_variant);\n",
        "\ttgref_record(\"UFS platform_init ret=%d\", err);\n",
        "ufs platform init",
    )
    text = insert_before_last_return(
        text,
        probe_anchor,
        "\treturn err;\n",
        "\ttgref_record(\"UFS probe_end device=%s ret=%d\", dev_name(dev), err);\n",
        "ufs probe end",
    )
    return text


def instrument_platform(text: str) -> str:
    text = add_include(
        text,
        "#include <linux/of.h>\n",
        "platform include",
    )
    anchor = "int ufshcd_pltfrm_init(struct platform_device *pdev,"
    text = insert_after_fragment(
        text,
        anchor,
        "\tstruct device *dev = &pdev->dev;\n",
        "\n\ttgref_record(\"PLT begin device=%s\", dev_name(dev));\n",
        "platform begin",
    )
    text = insert_after_fragment(
        text,
        anchor,
        "\tmmio_base = devm_ioremap_resource(dev, mem_res);\n",
        "\ttgref_record(\"PLT mmio resource=%pR mapping=%p\", mem_res, mmio_base);\n",
        "platform mmio",
    )
    text = insert_after_fragment(
        text,
        anchor,
        "\tirq = platform_get_irq(pdev, 0);\n",
        "\ttgref_record(\"PLT irq=%d\", irq);\n",
        "platform irq",
    )
    text = insert_after_fragment(
        text,
        anchor,
        "\terr = ufshcd_alloc_host(dev, &hba);\n",
        "\ttgref_record(\"PLT alloc_host ret=%d hba=%p\", err, err ? NULL : hba);\n",
        "platform host",
    )
    for statement, marker, label in (
        ("\terr = ufshcd_parse_clock_info(hba);\n", "parse_clocks", "platform clocks"),
        ("\terr = ufshcd_parse_regulator_info(hba);\n", "parse_regulators", "platform regulators"),
        ("\terr = ufshcd_parse_reset_info(hba);\n", "parse_reset", "platform reset"),
        ("\terr = ufshcd_parse_pinctrl_info(hba);\n", "parse_pinctrl", "platform pinctrl"),
        ("\terr = ufshcd_init(hba, mmio_base, irq);\n", "core_init", "platform core"),
    ):
        text = insert_after_fragment(
            text,
            anchor,
            statement,
            f'\ttgref_record("PLT {marker} ret=%d", err);\n',
            label,
        )
    text = insert_before_last_return(
        text,
        anchor,
        "\treturn 0;\n",
        "\ttgref_record(\"PLT end ret=0 host_no=%d lanes=%u\",\n"
        "\t\t     hba->host->host_no, hba->lanes_per_direction);\n",
        "platform success",
    )
    text = insert_before_last_return(
        text,
        anchor,
        "\treturn err;\n",
        "\ttgref_record(\"PLT fail ret=%d\", err);\n",
        "platform fail",
    )
    return text


def instrument_ufshcd(text: str) -> str:
    text = add_include(
        text,
        '#include "ufshcd.h"\n',
        "ufshcd include",
    )

    init_anchor = "int ufshcd_init(struct ufs_hba *hba,"
    text = insert_after_fragment(
        text,
        init_anchor,
        "\tstruct device *dev = hba->dev;\n",
        "\n\ttgref_record(\"CORE init_begin dev=%s irq=%u mmio=%p host_no=%d\",\n"
        "\t\t     dev_name(dev), irq, mmio_base, hba->host->host_no);\n",
        "core init begin",
    )
    init_points = (
        ("\terr = ufshcd_hba_init(hba);\n", "hba_init ret=%d", "err", "core hba init"),
        ("\terr = ufshcd_hba_capabilities(hba);\n", "capabilities ret=%d cap=0x%x version=0x%x", "err, hba->capabilities, hba->ufs_version", "core capabilities"),
        ("\terr = ufshcd_set_dma_mask(hba);\n", "dma_mask ret=%d", "err", "core dma"),
        ("\terr = ufshcd_memory_alloc(hba);\n", "memory_alloc ret=%d", "err", "core memory"),
        ("\terr = devm_request_irq(dev, irq, ufshcd_intr, IRQF_SHARED, UFSHCD, hba);\n", "request_irq ret=%d irq=%u", "err, irq", "core irq"),
        ("\terr = scsi_add_host(host, hba->dev);\n", "scsi_add_host ret=%d host_no=%d", "err, host->host_no", "core scsi host"),
        ("\terr = ufshcd_hba_enable(hba);\n", "hba_enable ret=%d state=%d hcs=0x%x", "err, hba->ufshcd_state, ufshcd_readl(hba, REG_CONTROLLER_STATUS)", "core hba enable"),
        ("\tasync_schedule(ufshcd_async_scan, hba);\n", "async_scan_scheduled state=%d", "hba->ufshcd_state", "core async schedule"),
    )
    for statement, fmt, args, label in init_points:
        text = insert_after_fragment(
            text,
            init_anchor,
            statement,
            f'\ttgref_record("CORE {fmt}", {args});\n',
            label,
        )

    probe_anchor = "static int ufshcd_probe_hba("
    probe_points = (
        ("\tret = ufshcd_link_startup(hba);\n", "\t", "link_startup ret=%d state=%d link=%d", "ret, hba->ufshcd_state, hba->uic_link_state", "core link"),
        ("\tret = ufshcd_verify_dev_init(hba);\n", "\t", "verify_dev_init ret=%d", "ret", "core verify"),
        ("\tret = ufshcd_complete_dev_init(hba);\n", "\t", "complete_dev_init ret=%d", "ret", "core complete"),
        ("\t\tret = ufshcd_device_params_init(hba);\n", "\t\t", "device_params ret=%d manufacturer=0x%x", "ret, hba->dev_info.wmanufacturerid", "core params"),
        ("\t\tret = ufshcd_config_pwr_mode(hba, &hba->max_pwr_info.info);\n", "\t\t", "power_mode ret=%d rxgear=%d txgear=%d rxlane=%d txlane=%d", "ret, hba->max_pwr_info.info.gear_rx, hba->max_pwr_info.info.gear_tx, hba->max_pwr_info.info.lane_rx, hba->max_pwr_info.info.lane_tx", "core power"),
        ("\t\tscsi_scan_host(hba->host);\n", "\t\t", "scsi_scan host_no=%d", "hba->host->host_no", "core scan"),
    )
    for statement, indent, fmt, args, label in probe_points:
        text = insert_after_fragment(
            text,
            probe_anchor,
            statement,
            f'{indent}tgref_record("CORE {fmt}", {args});\n',
            label,
        )
    text = insert_before_last_return(
        text,
        probe_anchor,
        "\treturn ret;\n",
        "\ttgref_record(\"CORE probe_hba_end ret=%d state=%d link=%d manufacturer=0x%x\",\n"
        "\t\t     ret, hba->ufshcd_state, hba->uic_link_state,\n"
        "\t\t     hba->dev_info.wmanufacturerid);\n",
        "core probe end",
    )
    return text


def instrument_sd(text: str) -> str:
    text = add_include(
        text,
        "#include <linux/blkdev.h>\n",
        "sd include",
    )
    probe_anchor = "static int sd_probe(struct device *dev)\n"
    text = insert_after_fragment(
        text,
        probe_anchor,
        '\terror = sd_format_disk_name("sd", index, gd->disk_name, DISK_NAME_LEN);\n',
        "\tif (!error && sdp->host->by_ufs)\n"
        "\t\ttgref_record(\"SD probe lun=%llu name=%s host_no=%d\",\n"
        "\t\t\t     (unsigned long long)sdp->lun, gd->disk_name,\n"
        "\t\t\t     sdp->host->host_no);\n",
        "sd probe",
    )
    async_anchor = "static void sd_probe_async(void *data, async_cookie_t cookie)\n"
    text = insert_after_fragment(
        text,
        async_anchor,
        "\tdevice_add_disk(dev, gd);\n",
        "\tif (sdp->host->by_ufs)\n"
        "\t\ttgref_record(\"SD add_disk name=%s capacity=%llu sectors host_no=%d\",\n"
        "\t\t\t     gd->disk_name, (unsigned long long)get_capacity(gd),\n"
        "\t\t\t     sdp->host->host_no);\n",
        "sd add disk",
    )
    return text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage targeted TouchGrass 4.19.206 UFS reference recorder"
    )
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    kernel = args.kernel.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    files = {
        "drivers/base/dd.c": instrument_driver_core,
        "drivers/phy/qualcomm/phy-qcom-ufs-qmp-v3.c": instrument_qmp_v3,
        "drivers/phy/qualcomm/phy-qcom-ufs.c": instrument_phy_common,
        "drivers/scsi/ufs/ufs-qcom.c": instrument_ufs_qcom,
        "drivers/scsi/ufs/ufshcd-pltfrm.c": instrument_platform,
        "drivers/scsi/ufs/ufshcd.c": instrument_ufshcd,
        "drivers/scsi/sd.c": instrument_sd,
    }

    for rel, fn in files.items():
        path = kernel / rel
        if not path.is_file():
            fail(rel, "source file missing")
        original = path.read_text(encoding="utf-8")
        patched = fn(original)
        if patched == original:
            fail(rel, "instrumentation produced no change")
        path.write_text(patched, encoding="utf-8")

    header_path = kernel / "include/linux/tgref_recorder.h"
    source_path = kernel / "drivers/scsi/ufs/tgref-recorder.c"
    makefile_path = kernel / "drivers/scsi/ufs/Makefile"
    header_path.write_text(HEADER, encoding="utf-8")
    source_path.write_text(SOURCE, encoding="utf-8")

    makefile = makefile_path.read_text(encoding="utf-8")
    marker = "# A52 TouchGrass UFS reference recorder"
    if marker in makefile or "tgref-recorder.o" in makefile:
        fail("Makefile", "recorder already linked")
    makefile_path.write_text(
        makefile.rstrip() + f"\n\n{marker}\nobj-y += tgref-recorder.o\n",
        encoding="utf-8",
    )

    checks = {
        "recorder_source": source_path.is_file(),
        "recorder_header": header_path.is_file(),
        "automatic_dump_marker": "/data/local/tmp/tgref_ufs.log" in SOURCE,
        "no_kernel_root_integration": "KSU" not in SOURCE and "KernelSU" not in SOURCE,
        "driver_core_trace": "DEV call device=%s driver=%s" in (kernel / "drivers/base/dd.c").read_text(),
        "phy_trace": "PHY probe_begin" in (kernel / "drivers/phy/qualcomm/phy-qcom-ufs-qmp-v3.c").read_text(),
        "qcom_trace": "UFS power_up_begin" in (kernel / "drivers/scsi/ufs/ufs-qcom.c").read_text(),
        "platform_trace": "PLT parse_clocks" in (kernel / "drivers/scsi/ufs/ufshcd-pltfrm.c").read_text(),
        "core_trace": "CORE link_startup" in (kernel / "drivers/scsi/ufs/ufshcd.c").read_text(),
        "disk_trace": "SD add_disk" in (kernel / "drivers/scsi/sd.c").read_text(),
        "makefile_link": "obj-y += tgref-recorder.o" in makefile_path.read_text(),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        fail("staging audit", ", ".join(failed))

    (output / "tgref-recorder.c").write_text(SOURCE, encoding="utf-8")
    (output / "tgref_recorder.h").write_text(HEADER, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "kernel": "touchGrass Linux 4.19.206",
                "scope": [
                    "driver bind/defer",
                    "QMP-v3 UFS PHY",
                    "Qualcomm UFS host",
                    "platform resources",
                    "UFS core link startup",
                    "SCSI disk registration",
                ],
                "automatic_dump": {
                    "path": "/data/local/tmp/tgref_ufs.log",
                    "poll_interval_ms": 5000,
                    "settle_after_data_ms": 30000,
                    "requires_android_root": False,
                },
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
