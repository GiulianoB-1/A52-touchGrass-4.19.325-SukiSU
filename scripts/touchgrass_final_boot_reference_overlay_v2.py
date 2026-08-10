#!/usr/bin/env python3
"""Final TouchGrass boot-reference recorder v2.

Observation only. Fixed tags, bounded first-events buffer, no dynamic strings.
Raw function/device/driver pointers are retained numerically for offline
symbolization with the collector's /proc/kallsyms snapshot.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HEADER = r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_TG_BOOT_REFERENCE_H
#define _LINUX_TG_BOOT_REFERENCE_H
#include <linux/types.h>
void tg_boot_ref_record(const char *tag, int rc, u64 a, u64 b, u64 c, u64 d);
#define TG_BOOT_REF(_tag, _rc, _a, _b, _c, _d) \
	tg_boot_ref_record((_tag), (_rc), (u64)(_a), (u64)(_b), (u64)(_c), (u64)(_d))
#define TG_BOOT_REF0(_tag) TG_BOOT_REF((_tag), 0, 0, 0, 0, 0)
#endif
'''

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
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
	u64 seq, ns, a, b, c, d;
	s32 rc;
	u16 cpu;
	char tag[TG_BOOT_REF_TAG_LEN];
};

static struct tg_boot_ref_entry tg_boot_ref_entries[TG_BOOT_REF_MAX_RECORDS];
static atomic_t tg_boot_ref_next = ATOMIC_INIT(0);

void tg_boot_ref_record(const char *tag, int rc, u64 a, u64 b, u64 c, u64 d)
{
	struct tg_boot_ref_entry *e;
	int idx = atomic_inc_return(&tg_boot_ref_next) - 1;

	if (idx < 0 || idx >= TG_BOOT_REF_MAX_RECORDS)
		return;
	e = &tg_boot_ref_entries[idx];
	e->ns = ktime_get_ns();
	e->a = a; e->b = b; e->c = c; e->d = d;
	e->rc = rc;
	e->cpu = raw_smp_processor_id();
	if (tag)
		strlcpy(e->tag, tag, sizeof(e->tag));
	smp_wmb();
	WRITE_ONCE(e->seq, (u64)idx + 1);
}

static int tg_boot_ref_show(struct seq_file *m, void *unused)
{
	int i, next = atomic_read(&tg_boot_ref_next), count;
	count = next > TG_BOOT_REF_MAX_RECORDS ? TG_BOOT_REF_MAX_RECORDS : next;
	seq_puts(m, "# touchgrass_final_boot_reference_v2\n");
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
			(unsigned long long)seq, (unsigned long long)e->ns,
			(unsigned int)e->cpu, e->tag[0] ? e->tag : "-", (int)e->rc,
			(unsigned long long)e->a, (unsigned long long)e->b,
			(unsigned long long)e->c, (unsigned long long)e->d);
	}
	return 0;
}

static int tg_boot_ref_open(struct inode *inode, struct file *file)
{
	return single_open(file, tg_boot_ref_show, NULL);
}
static const struct file_operations tg_boot_ref_fops = {
	.owner = THIS_MODULE, .open = tg_boot_ref_open, .read = seq_read,
	.llseek = seq_lseek, .release = single_release,
};
static int __init tg_boot_ref_proc_init(void)
{
	return proc_create("tg_boot_reference", 0444, NULL, &tg_boot_ref_fops) ? 0 : -ENOMEM;
}
late_initcall(tg_boot_ref_proc_init);
'''

INC = '#include <linux/tg_boot_reference.h>'


def get(p: Path) -> str:
    if not p.is_file():
        raise RuntimeError(f'missing source: {p}')
    return p.read_text(encoding='utf-8', errors='strict')


def put(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding='utf-8')


def add_include(s: str, label: str) -> str:
    if INC in s:
        return s
    ms = list(re.finditer(r'(?m)^#include\s+[<\"].+[>\"]\s*$', s))
    if not ms:
        raise RuntimeError(f'{label}: include anchor missing')
    pos = ms[-1].end()
    return s[:pos] + '\n' + INC + s[pos:]


def span(s: str, fn: str):
    pat = re.compile(r'\b' + re.escape(fn) + r'\s*\([^;{}]*\)\s*\{', re.S)
    for m in pat.finditer(s):
        b = s.find('{', m.start(), m.end())
        depth = 0; state = 'code'; i = b
        while i < len(s):
            c = s[i]; n = s[i + 1] if i + 1 < len(s) else ''
            if state == 'code':
                if c == '/' and n == '*': state = 'block'; i += 2; continue
                if c == '/' and n == '/': state = 'line'; i += 2; continue
                if c == '"': state = 'str'; i += 1; continue
                if c == "'": state = 'char'; i += 1; continue
                if c == '{': depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0: return m.start(), b, i + 1
            elif state == 'block':
                if c == '*' and n == '/': state = 'code'; i += 2; continue
            elif state == 'line':
                if c == '\n': state = 'code'
            else:
                q = '"' if state == 'str' else "'"
                if c == '\\': i += 2; continue
                if c == q: state = 'code'
            i += 1
    return None


def entry(s: str, fn: str, tag: str, required=False):
    marker = f'TG_BOOT_REF0("{tag}")'
    if marker in s:
        return s, True
    sp = span(s, fn)
    if not sp:
        if required: raise RuntimeError(f'required function absent: {fn}')
        return s, False
    _, b, _ = sp
    return s[:b + 1] + f'\n\t{marker};' + s[b + 1:], True


def edit_function(s: str, fn: str, transform, required=True) -> str:
    sp = span(s, fn)
    if not sp:
        if required: raise RuntimeError(f'required function absent: {fn}')
        return s
    start, _, end = sp
    body = s[start:end]
    new = transform(body)
    if new == body and required:
        raise RuntimeError(f'{fn}: transform made no change')
    return s[:start] + new + s[end:]


def patch_init(s: str) -> str:
    s = add_include(s, 'init/main.c')
    def x(body: str) -> str:
        pre = '\tdo_trace_initcall_start(fn);'
        post = '\tdo_trace_initcall_finish(fn, ret);'
        if 'TG_BOOT_REF("INITCALL:PRE"' not in body:
            if body.count(pre) != 1: raise RuntimeError('do_one_initcall start anchor mismatch')
            body = body.replace(pre,
                '\tTG_BOOT_REF("INITCALL:PRE", 0, (unsigned long)fn, 0, 0, 0);\n' + pre, 1)
        if 'TG_BOOT_REF("INITCALL:POST"' not in body:
            if body.count(post) != 1: raise RuntimeError('do_one_initcall finish anchor mismatch')
            body = body.replace(post,
                post + '\n\tTG_BOOT_REF("INITCALL:POST", ret, (unsigned long)fn, 0, 0, 0);', 1)
        return body
    s = edit_function(s, 'do_one_initcall', x)
    for fn, tag, req in (
        ('kernel_init', 'USER:KERNEL_INIT', True),
        ('kernel_init_freeable', 'USER:INIT_FREE', True),
        ('run_init_process', 'USER:RUN_INIT', True),
        ('try_to_run_init_process', 'USER:TRY_INIT', True),
    ):
        s, _ = entry(s, fn, tag, req)
    return s


def patch_dd(s: str) -> str:
    s = add_include(s, 'drivers/base/dd.c')
    def x(body: str) -> str:
        bus = '\t\tret = dev->bus->probe(dev);'
        drv = '\t\tret = drv->probe(dev);'
        if 'PROBE:BUS_PRE' not in body:
            if body.count(bus) != 1: raise RuntimeError('bus probe call mismatch')
            body = body.replace(bus,
                '\t\tTG_BOOT_REF("PROBE:BUS_PRE", 0, (unsigned long)dev->bus->probe, '\
                '(unsigned long)dev, (unsigned long)drv, 0);\n' + bus +
                '\n\t\tTG_BOOT_REF("PROBE:BUS_POST", ret, (unsigned long)dev->bus->probe, '\
                '(unsigned long)dev, (unsigned long)drv, 0);', 1)
        if 'PROBE:DRV_PRE' not in body:
            if body.count(drv) != 1: raise RuntimeError('driver probe call mismatch')
            body = body.replace(drv,
                '\t\tTG_BOOT_REF("PROBE:DRV_PRE", 0, (unsigned long)drv->probe, '\
                '(unsigned long)dev, (unsigned long)drv, 0);\n' + drv +
                '\n\t\tTG_BOOT_REF("PROBE:DRV_POST", ret, (unsigned long)drv->probe, '\
                '(unsigned long)dev, (unsigned long)drv, 0);', 1)
        return body
    return edit_function(s, 'really_probe', x)


def patch_iommu(s: str) -> str:
    s = add_include(s, 'drivers/iommu/iommu.c')
    for fn, tag, req in (
        ('iommu_group_add_device', 'IOMMU:GROUP_ADD', True),
        ('iommu_attach_device', 'IOMMU:ATTACH', False),
        ('iommu_detach_device', 'IOMMU:DETACH', False),
        ('iommu_domain_alloc', 'IOMMU:DOMAIN_ALLOC', False),
    ):
        s, _ = entry(s, fn, tag, req)
    return s


def optional(root: Path, rel: str, items) -> None:
    p = root / rel
    if not p.is_file():
        print('optional absent', rel); return
    s = add_include(get(p), rel); hits = 0
    for fn, tag in items:
        s, yes = entry(s, fn, tag, False); hits += int(yes)
    if hits:
        put(p, s); print('optional', rel, 'hits', hits)


def apply(root: Path) -> None:
    put(root / 'include/linux/tg_boot_reference.h', HEADER)
    put(root / 'kernel/tg_boot_reference.c', SOURCE)
    mk = root / 'kernel/Makefile'; s = get(mk)
    if 'obj-y += tg_boot_reference.o\n' not in s:
        put(mk, s.rstrip() + '\nobj-y += tg_boot_reference.o\n')

    for rel, fn in (
        ('init/main.c', patch_init),
        ('drivers/base/dd.c', patch_dd),
        ('drivers/iommu/iommu.c', patch_iommu),
    ):
        p = root / rel; put(p, fn(get(p))); print('patched', rel)

    opts = {
        'drivers/android/binder.c': [('binder_init','BINDER:INIT'), ('binder_open','BINDER:OPEN')],
        'drivers/scsi/ufs/ufshcd.c': [
            ('ufshcd_init','UFS:INIT'), ('ufshcd_probe_hba','UFS:PROBE_HBA'),
            ('ufshcd_link_startup','UFS:LINK'), ('ufshcd_make_hba_operational','UFS:OPER'),
            ('ufshcd_async_scan','UFS:SCAN')],
        'drivers/scsi/ufs/ufs-qcom.c': [
            ('ufs_qcom_probe','UFSQ:PROBE'), ('ufs_qcom_init','UFSQ:INIT'),
            ('ufs_qcom_power_up_sequence','UFSQ:POWER')],
        'init/do_mounts.c': [('prepare_namespace','MOUNT:PREP_NS'), ('mount_root','MOUNT:ROOT')],
        'techpack/display/msm/msm_drv.c': [
            ('msm_drm_init','DISP:DRM_INIT'), ('msm_drm_bind','DISP:DRM_BIND'),
            ('msm_drm_probe','DISP:DRM_PROBE')],
        'techpack/display/msm/dsi/dsi_display.c': [
            ('dsi_display_probe','DISP:DSI_PROBE'), ('dsi_display_bind','DISP:DSI_BIND'),
            ('dsi_display_prepare','DISP:DSI_PREP'), ('dsi_display_enable','DISP:DSI_ENABLE')],
        'techpack/display/msm/dsi/dsi_panel.c': [
            ('dsi_panel_get','DISP:PANEL_GET'), ('dsi_panel_prepare','DISP:PANEL_PREP'),
            ('dsi_panel_enable','DISP:PANEL_ENABLE')],
        'techpack/display/msm/sde/sde_kms.c': [('sde_kms_hw_init','DISP:KMS_HW')],
    }
    for rel, items in opts.items(): optional(root, rel, items)

    required = {
        'init/main.c': ['INITCALL:PRE','INITCALL:POST','USER:KERNEL_INIT','USER:RUN_INIT'],
        'drivers/base/dd.c': ['PROBE:BUS_POST','PROBE:DRV_POST'],
        'drivers/iommu/iommu.c': ['IOMMU:GROUP_ADD'],
        'kernel/tg_boot_reference.c': ['touchgrass_final_boot_reference_v2'],
    }
    for rel, tokens in required.items():
        s = get(root / rel)
        for t in tokens:
            if t not in s: raise RuntimeError(f'{rel}: missing {t}')
    print('TouchGrass final boot-reference recorder v2 applied')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: touchgrass_final_boot_reference_overlay_v2.py <kernel-root>')
    apply(Path(sys.argv[1]).resolve())
