#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

HEADER = r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_TG_GPU_REFERENCE_H
#define _LINUX_TG_GPU_REFERENCE_H

#include <linux/types.h>

void tg_gpu_ref_record(const char *tag, const char *name, int rc,
		u64 a, u64 b, u64 c, u64 d);

#define TG_GPU_REF(_tag, _name, _rc, _a, _b, _c, _d) \
	tg_gpu_ref_record((_tag), (_name), (_rc), (u64)(_a), (u64)(_b), \
		(u64)(_c), (u64)(_d))

#define TG_GPU_REF0(_tag) \
	TG_GPU_REF((_tag), NULL, 0, 0, 0, 0, 0)

#endif
'''

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/* TouchGrass GPU reference recorder: observation-only, first-events-retained. */
#include <linux/atomic.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/smp.h>
#include <linux/string.h>
#include <linux/tg_gpu_reference.h>

#define TG_GPU_REF_MAX_RECORDS 8192
#define TG_GPU_REF_TAG_LEN 24
#define TG_GPU_REF_NAME_LEN 48

struct tg_gpu_ref_entry {
	u64 seq;
	u64 ns;
	u64 a;
	u64 b;
	u64 c;
	u64 d;
	s32 rc;
	u16 cpu;
	char tag[TG_GPU_REF_TAG_LEN];
	char name[TG_GPU_REF_NAME_LEN];
};

static struct tg_gpu_ref_entry tg_gpu_ref_entries[TG_GPU_REF_MAX_RECORDS];
static atomic_t tg_gpu_ref_next = ATOMIC_INIT(0);

void tg_gpu_ref_record(const char *tag, const char *name, int rc,
		u64 a, u64 b, u64 c, u64 d)
{
	struct tg_gpu_ref_entry *e;
	int idx = atomic_inc_return(&tg_gpu_ref_next) - 1;

	if (idx < 0 || idx >= TG_GPU_REF_MAX_RECORDS)
		return;

	e = &tg_gpu_ref_entries[idx];
	e->ns = ktime_get_ns();
	e->a = a;
	e->b = b;
	e->c = c;
	e->d = d;
	e->rc = rc;
	e->cpu = raw_smp_processor_id();
	if (tag)
		strlcpy(e->tag, tag, sizeof(e->tag));
	if (name)
		strlcpy(e->name, name, sizeof(e->name));

	/* Publish seq last so readers never treat a partial record as complete. */
	smp_wmb();
	WRITE_ONCE(e->seq, (u64)idx + 1);
}

static int tg_gpu_ref_show(struct seq_file *m, void *unused)
{
	int i;
	int count = atomic_read(&tg_gpu_ref_next);

	if (count > TG_GPU_REF_MAX_RECORDS)
		count = TG_GPU_REF_MAX_RECORDS;

	seq_puts(m, "# touchgrass_gpu_reference_v1\n");
	seq_puts(m, "# seq ns cpu tag name rc a b c d\n");
	for (i = 0; i < count; i++) {
		struct tg_gpu_ref_entry *e = &tg_gpu_ref_entries[i];
		u64 seq = READ_ONCE(e->seq);

		if (!seq)
			continue;
		smp_rmb();
		seq_printf(m, "%llu %llu %u %s %s %d %llu %llu %llu %llu\n",
			(unsigned long long)seq,
			(unsigned long long)e->ns,
			(unsigned int)e->cpu,
			e->tag[0] ? e->tag : "-",
			e->name[0] ? e->name : "-",
			(int)e->rc,
			(unsigned long long)e->a,
			(unsigned long long)e->b,
			(unsigned long long)e->c,
			(unsigned long long)e->d);
	}
	return 0;
}

static int tg_gpu_ref_open(struct inode *inode, struct file *file)
{
	return single_open(file, tg_gpu_ref_show, NULL);
}

static const struct file_operations tg_gpu_ref_fops = {
	.owner = THIS_MODULE,
	.open = tg_gpu_ref_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init tg_gpu_ref_proc_init(void)
{
	if (!proc_create("tg_gpu_reference", 0444, NULL, &tg_gpu_ref_fops))
		return -ENOMEM;
	return 0;
}
late_initcall(tg_gpu_ref_proc_init);
'''

INCLUDE = '#include <linux/tg_gpu_reference.h>\n'


def require(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"missing source file: {path}")
    return path.read_text(encoding="utf-8", errors="strict")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def add_include(text: str, label: str) -> str:
    if INCLUDE in text:
        return text
    matches = list(re.finditer(r'(?m)^#include\s+[<\"].+[>\"]\s*$', text))
    if not matches:
        raise RuntimeError(f"{label}: no include anchor")
    pos = matches[-1].end()
    return text[:pos] + '\n' + INCLUDE.rstrip('\n') + text[pos:]


def function_span(text: str, name: str) -> tuple[int, int, int] | None:
    pat = re.compile(r'\b' + re.escape(name) + r'\s*\([^;{}]*\)\s*\{', re.S)
    for m in pat.finditer(text):
        brace = text.find('{', m.start(), m.end())
        if brace < 0:
            continue
        depth = 0
        state = 'code'
        i = brace
        while i < len(text):
            c = text[i]
            n = text[i + 1] if i + 1 < len(text) else ''
            if state == 'code':
                if c == '/' and n == '*':
                    state = 'block'; i += 2; continue
                if c == '/' and n == '/':
                    state = 'line'; i += 2; continue
                if c == '"':
                    state = 'string'; i += 1; continue
                if c == "'":
                    state = 'char'; i += 1; continue
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        return m.start(), brace, i + 1
            elif state == 'block':
                if c == '*' and n == '/':
                    state = 'code'; i += 2; continue
            elif state == 'line':
                if c == '\n':
                    state = 'code'
            else:
                quote = '"' if state == 'string' else "'"
                if c == '\\':
                    i += 2; continue
                if c == quote:
                    state = 'code'
            i += 1
    return None


def inject_entry(text: str, name: str, tag: str, required: bool = True) -> tuple[str, bool]:
    marker = f'TG_GPU_REF0("{tag}")'
    if marker in text:
        return text, True
    span = function_span(text, name)
    if not span:
        if required:
            raise RuntimeError(f"required function missing: {name}")
        return text, False
    _, brace, _ = span
    text = text[:brace + 1] + f'\n\tTG_GPU_REF0("{tag}");' + text[brace + 1:]
    return text, True


def replace_once(text: str, old: str, new: str, label: str, required: bool = True) -> str:
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        if required:
            raise RuntimeError(f"{label}: expected one anchor, found {n}")
        return text
    return text.replace(old, new, 1)


def patch_arm_smmu(text: str) -> str:
    if '#include <linux/clk.h>\n' not in text:
        text = text.replace('#include <linux/atomic.h>\n', '#include <linux/atomic.h>\n#include <linux/clk.h>\n', 1) if '#include <linux/atomic.h>\n' in text else '#include <linux/clk.h>\n' + text
    text = add_include(text, 'arm-smmu')
    required_entries = {
        'arm_smmu_prepare_clocks': 'SMMU:CLK_PREP',
        'arm_smmu_enable_clocks': 'SMMU:CLK_EN',
        'arm_smmu_enable_regulators': 'SMMU:REG_EN',
        'arm_smmu_power_on_slow': 'SMMU:PWR_SLOW',
        'arm_smmu_power_on_atomic': 'SMMU:PWR_ATOMIC',
        'arm_smmu_power_on': 'SMMU:PWR_ON',
        'arm_smmu_attach_dev': 'SMMU:ATTACH',
        'arm_smmu_init_domain_context': 'SMMU:DOMAIN_INIT',
        'arm_smmu_add_device': 'SMMU:ADD_DEV',
    }
    optional_entries = {
        'arm_smmu_device_probe': 'SMMU:PROBE',
        'arm_smmu_device_cfg_probe': 'SMMU:CFG_PROBE',
        'arm_smmu_init_power_resources': 'SMMU:PWR_RES',
        'arm_smmu_domain_power_on': 'SMMU:DOM_PWR',
        'arm_smmu_domain_add_master': 'SMMU:ADD_MASTER',
    }
    for fn, tag in required_entries.items():
        text, _ = inject_entry(text, fn, tag, True)
    for fn, tag in optional_entries.items():
        text, _ = inject_entry(text, fn, tag, False)

    text = replace_once(
        text,
        '\t\tret = regulator_enable(consumers[i].consumer);\n',
        '\t\tTG_GPU_REF("SMMU:REG_PRE", consumers[i].supply, 0, i, num_consumers, 0, 0);\n'
        '\t\tret = regulator_enable(consumers[i].consumer);\n'
        '\t\tTG_GPU_REF("SMMU:REG_POST", consumers[i].supply, ret, i, num_consumers, 0, 0);\n',
        'arm-smmu regulator enable')

    text = replace_once(
        text,
        '\t\tret = clk_prepare(pwr->clocks[i]);\n',
        '\t\tTG_GPU_REF("SMMU:CPREP_PRE", __clk_get_name(pwr->clocks[i]), 0, i, pwr->num_clocks, 0, 0);\n'
        '\t\tret = clk_prepare(pwr->clocks[i]);\n'
        '\t\tTG_GPU_REF("SMMU:CPREP_POST", __clk_get_name(pwr->clocks[i]), ret, i, pwr->num_clocks, 0, 0);\n',
        'arm-smmu clock prepare')

    text = replace_once(
        text,
        '\t\tret = clk_enable(pwr->clocks[i]);\n',
        '\t\tTG_GPU_REF("SMMU:CEN_PRE", __clk_get_name(pwr->clocks[i]), 0, i, pwr->num_clocks, 0, 0);\n'
        '\t\tret = clk_enable(pwr->clocks[i]);\n'
        '\t\tTG_GPU_REF("SMMU:CEN_POST", __clk_get_name(pwr->clocks[i]), ret, i, pwr->num_clocks, 0, 0);\n',
        'arm-smmu clock enable')

    for old, label, tag in (
        ('\tret = arm_smmu_request_bus(pwr);\n', 'bus request', 'SMMU:BUS'),
        ('\tret = arm_smmu_enable_regulators(pwr);\n', 'regulator aggregate', 'SMMU:REG_ALL'),
        ('\tret = arm_smmu_prepare_clocks(pwr);\n', 'clock prepare aggregate', 'SMMU:CPREP_ALL'),
        ('\tret = arm_smmu_enable_clocks(pwr);\n', 'clock enable aggregate', 'SMMU:CEN_ALL'),
    ):
        new = old + f'\tTG_GPU_REF("{tag}", pwr->dev ? dev_name(pwr->dev) : NULL, ret, pwr->num_gdscs, pwr->num_clocks, pwr->power_count, pwr->clock_refs_count);\n'
        text = replace_once(text, old, new, 'arm-smmu ' + label, required=False)
    return text


def patch_kgsl_iommu(text: str) -> str:
    text = add_include(text, 'kgsl_iommu')
    for fn, tag, req in (
        ('_kgsl_iommu_probe', 'KGSLI:DT_PROBE', True),
        ('kgsl_iommu_probe', 'KGSLI:PROBE', True),
        ('kgsl_iommu_init', 'KGSLI:INIT', True),
        ('kgsl_iommu_init_pt', 'KGSLI:INIT_PT', True),
        ('_attach_pt', 'KGSLI:ATTACH_PT', True),
        ('kgsl_iommu_start', 'KGSLI:START', True),
        ('kgsl_iommu_set_pt', 'KGSLI:SET_PT', True),
        ('kgsl_iommu_enable_clk', 'KGSLI:CLK_ON', False),
        ('kgsl_iommu_disable_clk', 'KGSLI:CLK_OFF', False),
    ):
        text, _ = inject_entry(text, fn, tag, req)
    text = replace_once(
        text,
        '\tret = iommu_attach_device(iommu_pt->domain, ctx->dev);\n',
        '\tTG_GPU_REF("KGSLI:ATT_PRE", ctx->dev ? dev_name(ctx->dev) : NULL, 0, iommu_pt->attached, 0, 0, 0);\n'
        '\tret = iommu_attach_device(iommu_pt->domain, ctx->dev);\n'
        '\tTG_GPU_REF("KGSLI:ATT_POST", ctx->dev ? dev_name(ctx->dev) : NULL, ret, iommu_pt->attached, 0, 0, 0);\n',
        'kgsl iommu attach')
    return text


def patch_gmu(text: str) -> str:
    text = add_include(text, 'kgsl_gmu')
    for fn, tag, req in (
        ('gmu_iommu_cb_probe', 'GMU:CB_PROBE', True),
        ('gmu_iommu_init', 'GMU:IOMMU_INIT', True),
        ('gmu_memory_probe', 'GMU:MEM_PROBE', True),
        ('gmu_probe', 'GMU:PROBE', True),
        ('gmu_start', 'GMU:START', True),
        ('alloc_and_map', 'GMU:MAP', True),
        ('gmu_regulators_probe', 'GMU:REG_PROBE', False),
        ('gmu_clocks_probe', 'GMU:CLK_PROBE', False),
        ('gmu_init', 'GMU:INIT', False),
        ('gmu_stop', 'GMU:STOP', False),
        ('gmu_suspend', 'GMU:SUSPEND', False),
    ):
        text, _ = inject_entry(text, fn, tag, req)

    text = replace_once(
        text,
        '\tctx->domain = iommu_domain_alloc(&platform_bus_type);\n',
        '\tctx->domain = iommu_domain_alloc(&platform_bus_type);\n'
        '\tTG_GPU_REF("GMU:DOM_ALLOC", ctx->name, ctx->domain ? 0 : -ENODEV, !!ctx->domain, 0, 0, 0);\n',
        'gmu domain alloc')
    text = replace_once(
        text,
        '\tret = iommu_attach_device(ctx->domain, dev);\n',
        '\tTG_GPU_REF("GMU:ATT_PRE", ctx->name, 0, 0, 0, 0, 0);\n'
        '\tret = iommu_attach_device(ctx->domain, dev);\n'
        '\tTG_GPU_REF("GMU:ATT_POST", ctx->name, ret, 0, 0, 0, 0);\n',
        'gmu context attach')
    text = replace_once(
        text,
        '\tret = iommu_map(domain, md->gmuaddr, md->physaddr, md->size, attrs);\n',
        '\tret = iommu_map(domain, md->gmuaddr, md->physaddr, md->size, attrs);\n'
        '\tTG_GPU_REF("GMU:MAP_RET", NULL, ret, md->ctx_idx, md->gmuaddr, md->physaddr, md->size);\n',
        'gmu map')
    return text


def patch_hfi(text: str) -> str:
    text = add_include(text, 'kgsl_hfi')
    for fn, tag, req in (
        ('hfi_start', 'HFI:START', True),
        ('hfi_send_cmd', 'HFI:SEND', True),
        ('hfi_send_gmu_init', 'HFI:GMU_INIT', True),
        ('hfi_queue_write', 'HFI:QWRITE', True),
        ('hfi_get_fw_version', 'HFI:FW_VER', False),
    ):
        text, _ = inject_entry(text, fn, tag, req)
    text = replace_once(
        text,
        '\tuint32_t id = MSG_HDR_GET_ID(*msg);\n',
        '\tuint32_t id = MSG_HDR_GET_ID(*msg);\n'
        '\tTG_GPU_REF("HFI:MSG", NULL, 0, queue_idx, id, size, MSG_HDR_GET_SEQNUM(*msg));\n',
        'hfi queue message')
    text = replace_once(
        text,
        '\tunsigned int seqnum = atomic_inc_return(&hfi->seqnum);\n',
        '\tunsigned int seqnum = atomic_inc_return(&hfi->seqnum);\n'
        '\tTG_GPU_REF("HFI:CMD", NULL, 0, queue_idx, MSG_HDR_GET_ID(*cmd), MSG_HDR_GET_SIZE(*cmd), seqnum);\n',
        'hfi send cmd metadata')
    return text


def patch_entries(text: str, label: str, entries: list[tuple[str, str, bool]]) -> str:
    text = add_include(text, label)
    for fn, tag, req in entries:
        text, _ = inject_entry(text, fn, tag, req)
    return text


def apply(root: Path) -> None:
    header = root / 'include/linux/tg_gpu_reference.h'
    source = root / 'kernel/tg_gpu_reference.c'
    write(header, HEADER)
    write(source, SOURCE)

    mk = root / 'kernel/Makefile'
    mktext = require(mk)
    line = 'obj-y += tg_gpu_reference.o\n'
    if line not in mktext:
        mktext = mktext.rstrip() + '\n' + line
        write(mk, mktext)

    patches = [
        ('drivers/iommu/arm-smmu.c', patch_arm_smmu),
        ('drivers/gpu/msm/kgsl_iommu.c', patch_kgsl_iommu),
        ('drivers/gpu/msm/kgsl_gmu.c', patch_gmu),
        ('drivers/gpu/msm/kgsl_hfi.c', patch_hfi),
    ]
    for rel, fn in patches:
        path = root / rel
        text = require(path)
        out = fn(text)
        write(path, out)
        print('patched', rel)

    entry_files = [
        ('drivers/gpu/msm/adreno.c', [
            ('adreno_probe', 'ADRENO:PROBE', True),
            ('_adreno_start', 'ADRENO:_START', True),
            ('adreno_start', 'ADRENO:START', True),
        ]),
        ('drivers/gpu/msm/adreno_a6xx.c', [
            ('a6xx_init', 'A6XX:INIT', True),
            ('a6xx_start', 'A6XX:START', False),
            ('a6xx_microcode_read', 'A6XX:UCODE', False),
            ('a6xx_gmu_init', 'A6XX:GMU_INIT', False),
            ('a6xx_gmu_start', 'A6XX:GMU_START', False),
        ]),
        ('drivers/gpu/msm/kgsl_device.c', [
            ('kgsl_device_platform_probe', 'KGSL:PLAT_PROBE', True),
        ]),
        ('drivers/gpu/msm/kgsl_pwrctrl.c', [
            ('kgsl_pwrctrl_change_state', 'PWR:STATE', True),
            ('kgsl_pwrctrl_enable', 'PWR:ENABLE', False),
            ('kgsl_pwrctrl_disable', 'PWR:DISABLE', False),
            ('kgsl_pwrctrl_clk', 'PWR:CLK', False),
            ('kgsl_pwrctrl_axi', 'PWR:AXI', False),
        ]),
    ]
    for rel, entries in entry_files:
        path = root / rel
        text = require(path)
        out = patch_entries(text, rel, entries)
        write(path, out)
        print('patched', rel)

    # Optional low-level power-domain visibility. Do not fail if downstream path differs.
    gdsc = root / 'drivers/clk/qcom/gdsc.c'
    if gdsc.is_file():
        text = add_include(require(gdsc), 'gdsc')
        for fn, tag in [('gdsc_enable', 'GDSC:ENABLE'), ('gdsc_disable', 'GDSC:DISABLE')]:
            text, _ = inject_entry(text, fn, tag, False)
        write(gdsc, text)
        print('patched drivers/clk/qcom/gdsc.c')

    # Sanity: every required source must now call the recorder.
    for rel in [p[0] for p in patches] + [p[0] for p in entry_files]:
        data = require(root / rel)
        if 'tg_gpu_reference.h' not in data or 'TG_GPU_REF' not in data:
            raise RuntimeError(f'recorder missing from {rel}')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('kernel_root', type=Path)
    args = ap.parse_args()
    root = args.kernel_root.resolve()
    apply(root)
    print('TouchGrass GPU reference recorder overlay applied:', root)


if __name__ == '__main__':
    main()
