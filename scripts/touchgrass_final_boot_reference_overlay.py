#!/usr/bin/env python3
"""Add an observation-only boot-critical recorder to known-good TouchGrass.

The existing GPU recorder remains separate and unchanged. This recorder captures
all initcall outcomes, raw driver-probe outcomes, key kernel->userspace handoff
points, generic IOMMU group/attach entries, and optional fixed-tag checkpoints
for storage, Binder and the Qualcomm display stack.

Safety rules:
- fixed literal tags only; never copy/dereference dynamic device/driver names
- bounded first-events-retained buffer
- no printk, retries, sleeps, resource votes or return-value changes
- raw pointers are stored numerically for later symbolization with /proc/kallsyms
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HEADER = r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_TG_BOOT_REFERENCE_H
#define _LINUX_TG_BOOT_REFERENCE_H

#include <linux/types.h>

void tg_boot_ref_record(const char *tag, int rc,
		u64 a, u64 b, u64 c, u64 d);

#define TG_BOOT_REF(_tag, _rc, _a, _b, _c, _d) \
	tg_boot_ref_record((_tag), (_rc), (u64)(_a), (u64)(_b), \
		(u64)(_c), (u64)(_d))
#define TG_BOOT_REF0(_tag) TG_BOOT_REF((_tag), 0, 0, 0, 0, 0)

#endif
'''

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/* TouchGrass final boot-reference recorder: fixed tags, first events retained. */
#include <linux/atomic.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/smp.h>
#include <linux/string.h>
#include <linux/tg_boot_reference.h>

#define TG_BOOT_REF_MAX_RECORDS 32768
#define TG_BOOT_REF_TAG_LEN 24

struct tg_boot_ref_entry {
	u64 seq;
	u64 ns;
	u64 a;
	u64 b;
	u64 c;
	u64 d;
	s32 rc;
	u16 cpu;
	char tag[TG_BOOT_REF_TAG_LEN];
};

static struct tg_boot_ref_entry tg_boot_ref_entries[TG_BOOT_REF_MAX_RECORDS];
static atomic_t tg_boot_ref_next = ATOMIC_INIT(0);

void tg_boot_ref_record(const char *tag, int rc,
		u64 a, u64 b, u64 c, u64 d)
{
	struct tg_boot_ref_entry *e;
	int idx = atomic_inc_return(&tg_boot_ref_next) - 1;

	if (idx < 0 || idx >= TG_BOOT_REF_MAX_RECORDS)
		return;

	e = &tg_boot_ref_entries[idx];
	e->ns = ktime_get_ns();
	e->a = a;
	e->b = b;
	e->c = c;
	e->d = d;
	e->rc = rc;
	e->cpu = raw_smp_processor_id();
	if (tag)
		strlcpy(e->tag, tag, sizeof(e->tag));

	/* Publish sequence last so readers cannot observe a partial record. */
	smp_wmb();
	WRITE_ONCE(e->seq, (u64)idx + 1);
}

static int tg_boot_ref_show(struct seq_file *m, void *unused)
{
	int i;
	int next = atomic_read(&tg_boot_ref_next);
	int count = next;

	if (count > TG_BOOT_REF_MAX_RECORDS)
		count = TG_BOOT_REF_MAX_RECORDS;

	seq_puts(m, "# touchgrass_final_boot_reference_v1\n");
	seq_printf(m, "# capacity=%d attempted=%d retained=%d dropped=%d\n",
		TG_BOOT_REF_MAX_RECORDS, next, count,
		next > TG_BOOT_REF_MAX_RECORDS ? next - TG_BOOT_REF_MAX_RECORDS : 0);
	seq_puts(m, "# seq ns cpu tag rc a b c d\n");

	for (i = 0; i < count; i++) {
		struct tg_boot_ref_entry *e = &tg_boot_ref_entries[i];
		u64 seq = READ_ONCE(e->seq);

		if (!seq)
			continue;
		smp_rmb();
		seq_printf(m, "%llu %llu %u %s %d %llu %llu %llu %llu\n",
			(unsigned long long)seq,
			(unsigned long long)e->ns,
			(unsigned int)e->cpu,
			e->tag[0] ? e->tag : "-",
			(int)e->rc,
			(unsigned long long)e->a,
			(unsigned long long)e->b,
			(unsigned long long)e->c,
			(unsigned long long)e->d);
	}
	return 0;
}

static int tg_boot_ref_open(struct inode *inode, struct file *file)
{
	return single_open(file, tg_boot_ref_show, NULL);
}

static const struct file_operations tg_boot_ref_fops = {
	.owner = THIS_MODULE,
	.open = tg_boot_ref_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init tg_boot_ref_proc_init(void)
{
	if (!proc_create("tg_boot_reference", 0444, NULL, &tg_boot_ref_fops))
		return -ENOMEM;
	return 0;
}
late_initcall(tg_boot_ref_proc_init);
'''

INCLUDE = '#include <linux/tg_boot_reference.h>\n'


def read(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"required source missing: {path}")
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


def function_span(text: str, name: str):
    pat = re.compile(r'\b' + re.escape(name) + r'\s*\([^;{}]*\)\s*\{', re.S)
    for m in pat.finditer(text):
        brace = text.find('{', m.start(), m.end())
        depth = 0
        state = 'code'
        i = brace
        while i < len(text):
            c = text[i]
            n = text[i + 1] if i + 1 < len(text) else ''
            if state == 'code':
                if c == '/' and n == '*': state = 'block'; i += 2; continue
                if c == '/' and n == '/': state = 'line'; i += 2; continue
                if c == '"': state = 'string'; i += 1; continue
                if c == "'": state = 'char'; i += 1; continue
                if c == '{': depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0: return m.start(), brace, i + 1
            elif state == 'block':
                if c == '*' and n == '/': state = 'code'; i += 2; continue
            elif state == 'line':
                if c == '\n': state = 'code'
            else:
                q = '"' if state == 'string' else "'"
                if c == '\\': i += 2; continue
                if c == q: state = 'code'
            i += 1
    return None


def inject_entry(text: str, name: str, tag: str, required=False):
    marker = f'TG_BOOT_REF0("{tag}")'
    if marker in text:
        return text, True
    span = function_span(text, name)
    if not span:
        if required:
            raise RuntimeError(f"required function missing: {name}")
        return text, False
    _, brace, _ = span
    return text[:brace + 1] + f'\n\tTG_BOOT_REF0("{tag}");' + text[brace + 1:], True


def replace_once(text: str, old: str, new: str, label: str, required=True):
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        if required:
            raise RuntimeError(f"{label}: expected one anchor, found {n}")
        return text
    return text.replace(old, new, 1)


def patch_init_main(text: str) -> str:
    text = add_include(text, 'init/main.c')
    old = '\tdo_trace_initcall_start(fn);\n\tret = fn();\n\tdo_trace_initcall_finish(fn, ret);\n'
    new = (
        '\tTG_BOOT_REF("INITCALL:PRE", 0, (unsigned long)fn, 0, 0, 0);\n'
        '\tdo_trace_initcall_start(fn);\n'
        '\tret = fn();\n'
        '\tdo_trace_initcall_finish(fn, ret);\n'
        '\tTG_BOOT_REF("INITCALL:POST", ret, (unsigned long)fn, 0, 0, 0);\n'
    )
    text = replace_once(text, old, new, 'do_one_initcall outcome')
    for fn, tag in (
        ('kernel_init', 'USER:KERNEL_INIT'),
        ('kernel_init_freeable', 'USER:INIT_FREE'),
        ('run_init_process', 'USER:RUN_INIT'),
        ('try_to_run_init_process', 'USER:TRY_INIT'),
    ):
        text, _ = inject_entry(text, fn, tag, required=(fn == 'kernel_init'))
    return text


def patch_driver_core(text: str) -> str:
    text = add_include(text, 'drivers/base/dd.c')
    old = (
        '\tif (dev->bus->probe) {\n'
        '\t\tret = dev->bus->probe(dev);\n'
        '\t\tif (ret)\n'
        '\t\t\tgoto probe_failed;\n'
        '\t} else if (drv->probe) {\n'
        '\t\tret = drv->probe(dev);\n'
        '\t\tif (ret)\n'
        '\t\t\tgoto probe_failed;\n'
        '\t}\n'
    )
    new = (
        '\tif (dev->bus->probe) {\n'
        '\t\tTG_BOOT_REF("PROBE:BUS_PRE", 0, (unsigned long)dev->bus->probe,\n'
        '\t\t\t(unsigned long)dev, (unsigned long)drv, 0);\n'
        '\t\tret = dev->bus->probe(dev);\n'
        '\t\tTG_BOOT_REF("PROBE:BUS_POST", ret, (unsigned long)dev->bus->probe,\n'
        '\t\t\t(unsigned long)dev, (unsigned long)drv, 0);\n'
        '\t\tif (ret)\n'
        '\t\t\tgoto probe_failed;\n'
        '\t} else if (drv->probe) {\n'
        '\t\tTG_BOOT_REF("PROBE:DRV_PRE", 0, (unsigned long)drv->probe,\n'
        '\t\t\t(unsigned long)dev, (unsigned long)drv, 0);\n'
        '\t\tret = drv->probe(dev);\n'
        '\t\tTG_BOOT_REF("PROBE:DRV_POST", ret, (unsigned long)drv->probe,\n'
        '\t\t\t(unsigned long)dev, (unsigned long)drv, 0);\n'
        '\t\tif (ret)\n'
        '\t\t\tgoto probe_failed;\n'
        '\t}\n'
    )
    return replace_once(text, old, new, 'really_probe raw result')


def patch_iommu(text: str) -> str:
    text = add_include(text, 'drivers/iommu/iommu.c')
    for fn, tag in (
        ('iommu_group_add_device', 'IOMMU:GROUP_ADD'),
        ('iommu_attach_device', 'IOMMU:ATTACH'),
        ('iommu_detach_device', 'IOMMU:DETACH'),
        ('iommu_domain_alloc', 'IOMMU:DOMAIN_ALLOC'),
    ):
        text, _ = inject_entry(text, fn, tag, required=False)
    return text


def patch_optional(root: Path, rel: str, entries):
    path = root / rel
    if not path.is_file():
        print('optional source absent:', rel)
        return
    text = add_include(read(path), rel)
    hit = 0
    for fn, tag in entries:
        text, found = inject_entry(text, fn, tag, required=False)
        hit += int(found)
    if hit:
        write(path, text)
        print(f'patched optional {rel}: {hit} checkpoints')
    else:
        print(f'optional source had no known checkpoint: {rel}')


def apply(root: Path) -> None:
    write(root / 'include/linux/tg_boot_reference.h', HEADER)
    write(root / 'kernel/tg_boot_reference.c', SOURCE)

    mk = root / 'kernel/Makefile'
    m = read(mk)
    line = 'obj-y += tg_boot_reference.o\n'
    if line not in m:
        write(mk, m.rstrip() + '\n' + line)

    required = [
        ('init/main.c', patch_init_main),
        ('drivers/base/dd.c', patch_driver_core),
        ('drivers/iommu/iommu.c', patch_iommu),
    ]
    for rel, fn in required:
        p = root / rel
        out = fn(read(p))
        write(p, out)
        print('patched', rel)

    optional = {
        'drivers/android/binder.c': [
            ('binder_init', 'BINDER:INIT'), ('binder_open', 'BINDER:OPEN'),
        ],
        'drivers/scsi/ufs/ufshcd.c': [
            ('ufshcd_init', 'UFS:INIT'), ('ufshcd_probe_hba', 'UFS:PROBE_HBA'),
            ('ufshcd_link_startup', 'UFS:LINK'),
            ('ufshcd_make_hba_operational', 'UFS:OPER'),
            ('ufshcd_async_scan', 'UFS:SCAN'),
        ],
        'drivers/scsi/ufs/ufs-qcom.c': [
            ('ufs_qcom_probe', 'UFSQ:PROBE'), ('ufs_qcom_init', 'UFSQ:INIT'),
            ('ufs_qcom_power_up_sequence', 'UFSQ:POWER'),
        ],
        'techpack/display/msm/msm_drv.c': [
            ('msm_drm_init', 'DISP:DRM_INIT'), ('msm_drm_bind', 'DISP:DRM_BIND'),
            ('msm_drm_probe', 'DISP:DRM_PROBE'),
        ],
        'techpack/display/msm/dsi/dsi_display.c': [
            ('dsi_display_probe', 'DISP:DSI_PROBE'),
            ('dsi_display_bind', 'DISP:DSI_BIND'),
            ('dsi_display_prepare', 'DISP:DSI_PREP'),
            ('dsi_display_enable', 'DISP:DSI_ENABLE'),
        ],
        'techpack/display/msm/dsi/dsi_panel.c': [
            ('dsi_panel_get', 'DISP:PANEL_GET'),
            ('dsi_panel_prepare', 'DISP:PANEL_PREP'),
            ('dsi_panel_enable', 'DISP:PANEL_ENABLE'),
        ],
        'techpack/display/msm/sde/sde_kms.c': [
            ('sde_kms_hw_init', 'DISP:KMS_HW'),
            ('sde_kms_prepare_commit', 'DISP:KMS_PREP'),
            ('sde_kms_enable_commit', 'DISP:KMS_ENABLE'),
        ],
    }
    for rel, entries in optional.items():
        patch_optional(root, rel, entries)

    # Source-level safety/audit markers.
    checks = {
        'init/main.c': ['INITCALL:PRE', 'INITCALL:POST', 'USER:KERNEL_INIT'],
        'drivers/base/dd.c': ['PROBE:BUS_POST', 'PROBE:DRV_POST'],
        'drivers/iommu/iommu.c': ['IOMMU:GROUP_ADD'],
        'kernel/tg_boot_reference.c': ['touchgrass_final_boot_reference_v1'],
    }
    for rel, tokens in checks.items():
        text = read(root / rel)
        for token in tokens:
            if token not in text:
                raise RuntimeError(f'{rel}: missing final recorder marker {token}')

    print('TouchGrass final boot-reference recorder applied')


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: touchgrass_final_boot_reference_overlay.py <kernel-root>')
    apply(Path(sys.argv[1]).resolve())


if __name__ == '__main__':
    main()
