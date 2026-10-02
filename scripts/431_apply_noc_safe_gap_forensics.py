#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK='A52_PHASE431_NOC_SAFE_GAP_FORENSICS_V1'
SYSCALL=Path('arch/arm64/kernel/syscall.c')
UFS=Path('drivers/scsi/ufs/ufshcd.c')
RSC=Path('drivers/soc/qcom/rpmh-rsc.c')
REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')


def one(text:str, old:str, new:str, label:str)->str:
    n=text.count(old)
    if n!=1:
        raise SystemExit(f'Phase431 {label}: expected 1 anchor, found {n}')
    return text.replace(old,new,1)


def patch_syscall(text:str)->str:
    if MARK in text:
        return text
    if 'A52_PHASE430_SF_DMA_FORENSICS_V1' not in text:
        raise SystemExit('Phase431 requires Phase430 syscall lineage')
    if '#include <linux/hrtimer.h>\n' not in text:
        text=one(text,'#include <linux/delay.h>\n','#include <linux/delay.h>\n#include <linux/hrtimer.h>\n','hrtimer include')

    text=one(text,'#define A52_P430_COPY_BYTES         0x2800U\n','#define A52_P430_COPY_BYTES         0x2A00U /* Phase431: 42 task slots/copy */\n','task copy size')
    text=one(text,'#define A52_P430_SNAPSHOTS          6U\n','#define A52_P430_SNAPSHOTS          7U\n','snapshot count')
    text=one(text,'#define A52_P430_SLOTS              36U\n','#define A52_P430_SLOTS              42U\n','slot count')
    text=one(text,'#define A52_P430_MAGIC              0x303334534e454353ULL\n','#define A52_P430_MAGIC              0x313334534e454353ULL\n','task magic')
    text=one(text,'#define A52_P430_COMMIT             0x430c0de5U\n','#define A52_P430_COMMIT             0x431c0de5U\n','task commit')
    text=one(text,'#define A52_P430_VERSION            1U\n','#define A52_P430_VERSION            2U\n','task version')
    text=text.replace('extern void a52_p430_ufs_compact(unsigned int snapshot_id);\n','')

    anchor='static u64 a52_p430_sf_start_ns;\n'
    block=r'''static u64 a52_p430_sf_start_ns;

/* A52_PHASE431_NOC_SAFE_GAP_FORENSICS_V1
 * Three-copy, 48-byte progress witnesses live in the final 0x200 bytes of the
 * 32 KiB task sideband. They are independent of task-record persistence:
 *   stage:    SF found / snapshot begin / discovery done / tasks persisted
 *   hrtimer:  250 ms interrupt-driven liveness for the first 20 s after SF
 *   thread:   one-second sampler-thread liveness for the first 20 s after SF
 * Both liveness witnesses include CNTVCT so a future capture can distinguish
 * timer/scheduler disappearance from ordinary userspace waiting.
 */
#define A52_P431_WIT_BYTES          48U
#define A52_P431_WIT_COPIES         3U
#define A52_P431_STAGE_OFF          0x7E00U
#define A52_P431_TIMER_OFF          0x7E90U
#define A52_P431_THREAD_OFF         0x7F20U
#define A52_P431_WIT_MAGIC          0x3133345449575047ULL
#define A52_P431_WIT_COMMIT         0x431b17e5U
#define A52_P431_STAGE_SF_FOUND     1U
#define A52_P431_STAGE_SNAP_BEGIN   2U
#define A52_P431_STAGE_DISC_DONE    3U
#define A52_P431_STAGE_TASKS_DONE   4U
#define A52_P431_STAGE_ATOMIC       5U
#define A52_P431_SRC_STAGE          1U
#define A52_P431_SRC_TIMER          2U
#define A52_P431_SRC_THREAD         3U

struct a52_p431_witness {
	u64 magic;
	u64 ns;
	u64 cntvct;
	u32 seq;
	u32 code;
	u32 snapshot;
	u32 cpu;
	u32 crc32c;
	u32 commit;
};

static atomic_t a52_p431_wit_seq = ATOMIC_INIT(0);
static struct hrtimer a52_p431_live_timer;
static bool a52_p431_timer_started;
static u32 a52_p431_thread_last_sec = ~0U;

static __always_inline u64 a52_p431_cntvct(void)
{
	u64 v;
	asm volatile("mrs %0, cntvct_el0" : "=r" (v));
	return v;
}

static void a52_p431_witness_write(unsigned int base, u32 code, u32 snapshot)
{
	struct a52_p431_witness r;
	void *d0, *d1, *d2;

	if (!a52_p430_task_sideband)
		return;
	BUILD_BUG_ON(sizeof(r) != A52_P431_WIT_BYTES);
	memset(&r, 0, sizeof(r));
	r.magic = A52_P431_WIT_MAGIC;
	r.ns = ktime_get_boottime_ns();
	r.cntvct = a52_p431_cntvct();
	r.seq = (u32)atomic_inc_return(&a52_p431_wit_seq);
	r.code = code;
	r.snapshot = snapshot;
	r.cpu = (u32)raw_smp_processor_id();
	r.crc32c = a52_r382_crc32c(&r, offsetof(struct a52_p431_witness, crc32c));
	r.commit = A52_P431_WIT_COMMIT;
	d0 = (u8 *)a52_p430_task_sideband + base;
	d1 = d0 + A52_P431_WIT_BYTES;
	d2 = d1 + A52_P431_WIT_BYTES;
	memcpy(d0, &r, sizeof(r));
	memcpy(d1, &r, sizeof(r));
	memcpy(d2, &r, sizeof(r));
	wmb();
	__flush_dcache_area(d0, sizeof(r));
	__flush_dcache_area(d1, sizeof(r));
	__flush_dcache_area(d2, sizeof(r));
	dsb(sy);
}

bool a52_p431_trace_window_active(void)
{
	u64 start = READ_ONCE(a52_p430_sf_start_ns);
	if (!start)
		return false;
	return ktime_get_boottime_ns() - start <= 70ULL * NSEC_PER_SEC;
}
EXPORT_SYMBOL_GPL(a52_p431_trace_window_active);

static enum hrtimer_restart a52_p431_live_timer_fn(struct hrtimer *timer)
{
	u64 start = READ_ONCE(a52_p430_sf_start_ns);
	u64 now = ktime_get_boottime_ns();
	(void)timer;
	if (!start || now - start > 20ULL * NSEC_PER_SEC)
		return HRTIMER_NORESTART;
	a52_p431_witness_write(A52_P431_TIMER_OFF, A52_P431_SRC_TIMER, ~0U);
	hrtimer_forward_now(&a52_p431_live_timer, ms_to_ktime(250));
	return HRTIMER_RESTART;
}

'''
    text=one(text,anchor,block,'witness insertion')

    old='''\t\ta52_ackfr_record("P430 AT0 t=%d ns=%llu", tgid,\n\t\t\t(unsigned long long)ktime_get_boottime_ns());\n\t\tif (READ_ONCE(a52_p430_sampler))\n'''
    new='''\t\ta52_ackfr_record("P431 AT0 t=%d ns=%llu", tgid,\n\t\t\t(unsigned long long)ktime_get_boottime_ns());\n\t\ta52_p431_witness_write(A52_P431_STAGE_OFF, A52_P431_STAGE_ATOMIC, 6U);\n\t\tif (READ_ONCE(a52_p430_sampler))\n'''
    text=one(text,old,new,'atomic witness')

    old='''\tmemset(tasks, 0, sizeof(tasks));\n\tmemset(roles, 0, sizeof(roles));\n\tnr = a52_p430_display_tasks(tasks, roles, A52_P430_PER_SNAPSHOT,\n\t\t&sf, &cp, &ss);\n\ta52_p430_ufs_compact(snapshot_id);\n\ta52_ackfr_record("P430 S id=%u nr=%u sf=%d cp=%d ss=%d rel=%llu",\n\t\tsnapshot_id, nr, sf, cp, ss,\n\t\t(unsigned long long)(a52_p430_sf_start_ns ?\n\t\t(ktime_get_boottime_ns() - a52_p430_sf_start_ns) : 0));\n\tfor (i = 0; i < nr; i++) {\n\t\ta52_p430_fill(&r, tasks[i], snapshot_id, roles[i], i);\n\t\ta52_p430_write(base + i, &r);\n\t\tput_task_struct(tasks[i]);\n\t}\n'''
    new='''\tmemset(tasks, 0, sizeof(tasks));\n\tmemset(roles, 0, sizeof(roles));\n\ta52_p431_witness_write(A52_P431_STAGE_OFF, A52_P431_STAGE_SNAP_BEGIN, snapshot_id);\n\ta52_ackfr_record("P431 SB id=%u rel=%llu", snapshot_id,\n\t\t(unsigned long long)(a52_p430_sf_start_ns ?\n\t\t(ktime_get_boottime_ns() - a52_p430_sf_start_ns) : 0));\n\tnr = a52_p430_display_tasks(tasks, roles, A52_P430_PER_SNAPSHOT,\n\t\t&sf, &cp, &ss);\n\ta52_p431_witness_write(A52_P431_STAGE_OFF, A52_P431_STAGE_DISC_DONE, snapshot_id);\n\ta52_ackfr_record("P431 SD id=%u nr=%u sf=%d cp=%d ss=%d",\n\t\tsnapshot_id, nr, sf, cp, ss);\n\tfor (i = 0; i < nr; i++) {\n\t\ta52_p430_fill(&r, tasks[i], snapshot_id, roles[i], i);\n\t\ta52_p430_write(base + i, &r);\n\t\tput_task_struct(tasks[i]);\n\t}\n\ta52_p431_witness_write(A52_P431_STAGE_OFF, A52_P431_STAGE_TASKS_DONE, snapshot_id);\n\ta52_ackfr_record("P431 SE id=%u nr=%u", snapshot_id, nr);\n'''
    text=one(text,old,new,'safe snapshot ordering')

    text=one(text,'static const u32 rel_s[5] = { 5U, 20U, 40U, 60U, 70U };\n',
             'static const u32 rel_s[6] = { 0U, 1U, 3U, 5U, 20U, 60U };\n','relative schedule')
    text=one(text,'\t\t\t\ta52_ackfr_record("P430 SF0 p=%d ns=%llu", sf->pid,\n',
             '\t\t\t\ta52_ackfr_record("P431 SF0 p=%d ns=%llu", sf->pid,\n','SF0 prefix')
    old='''\t\t\t\tput_task_struct(sf);\n\t\t\t}\n'''
    new='''\t\t\t\ta52_p431_witness_write(A52_P431_STAGE_OFF, A52_P431_STAGE_SF_FOUND, ~0U);\n\t\t\t\ta52_p431_witness_write(A52_P431_THREAD_OFF, A52_P431_SRC_THREAD, ~0U);\n\t\t\t\tif (!a52_p431_timer_started) {\n\t\t\t\t\ta52_p431_timer_started = true;\n\t\t\t\t\thrtimer_start(&a52_p431_live_timer, ms_to_ktime(250), HRTIMER_MODE_REL_PINNED);\n\t\t\t\t}\n\t\t\t\tput_task_struct(sf);\n\t\t\t}\n'''
    text=one(text,old,new,'SF witness start')

    anchor='''\t\tif (a52_p430_sf_start_ns) {\n\t\t\telapsed = div_u64(ktime_get_boottime_ns() - a52_p430_sf_start_ns,\n\t\t\t\tNSEC_PER_SEC);\n'''
    repl=anchor+'''\t\t\tif (elapsed <= 20U && a52_p431_thread_last_sec != (u32)elapsed) {\n\t\t\t\ta52_p431_thread_last_sec = (u32)elapsed;\n\t\t\t\ta52_p431_witness_write(A52_P431_THREAD_OFF, A52_P431_SRC_THREAD, ~0U);\n\t\t\t}\n'''
    text=one(text,anchor,repl,'thread liveness')
    text=one(text,'\t\t\t\ta52_p430_snapshot(5U);\n','\t\t\t\ta52_p430_snapshot(6U);\n','endpoint snapshot id')

    old='''\tmemset(a52_p430_task_sideband, 0, A52_P430_TASK_BYTES);\n\twmb();\n\t__flush_dcache_area(a52_p430_task_sideband, A52_P430_TASK_BYTES);\n\ta52_p430_sampler = kthread_run(a52_p430_sampler_fn, NULL, "a52_p430_sf");\n'''
    new='''\tmemset(a52_p430_task_sideband, 0, A52_P430_TASK_BYTES);\n\twmb();\n\t__flush_dcache_area(a52_p430_task_sideband, A52_P430_TASK_BYTES);\n\tdsb(sy);\n\thrtimer_init(&a52_p431_live_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL_PINNED);\n\ta52_p431_live_timer.function = a52_p431_live_timer_fn;\n\ta52_p430_sampler = kthread_run(a52_p430_sampler_fn, NULL, "a52_p431_sf");\n'''
    text=one(text,old,new,'timer init')

    old='''\tBUILD_BUG_ON(3U * A52_P430_COPY_BYTES > A52_P430_TASK_BYTES);\n'''
    new='''\tBUILD_BUG_ON(3U * A52_P430_COPY_BYTES != A52_P431_STAGE_OFF);\n\tBUILD_BUG_ON(A52_P431_THREAD_OFF + 3U * A52_P431_WIT_BYTES > A52_P430_TASK_BYTES);\n'''
    text=one(text,old,new,'layout guards')

    return text + '\n/* '+MARK+': NoC-safe SF gap snapshots + dual CNTVCT liveness witnesses; no UFS sampling. */\n'


def patch_ufs(text:str)->str:
    if MARK in text:
        return text
    if 'A52_PHASE430_SF_DMA_FORENSICS_V1' not in text:
        raise SystemExit('Phase431 requires Phase430 UFS lineage')
    text=one(text,
        '\thba->irq = irq;\n\t/* Phase430: retain HBA identity even though Phase378 sampler stays retired. */\n\tWRITE_ONCE(a52_r378_hba, hba);\n',
        '\thba->irq = irq;\n\t/* Phase431: do NOT publish HBA to retired asynchronous diagnostics. */\n',
        'remove Phase430 HBA publication')
    start=text.find('void a52_p430_ufs_compact(unsigned int snapshot_id)\n{')
    if start<0: raise SystemExit('Phase431 compact UFS helper start missing')
    end=text.find('EXPORT_SYMBOL_GPL(a52_p430_ufs_compact);\n',start)
    if end<0: raise SystemExit('Phase431 compact UFS helper end missing')
    end += len('EXPORT_SYMBOL_GPL(a52_p430_ufs_compact);\n')
    stub='''void a52_p430_ufs_compact(unsigned int snapshot_id)\n{\n\t/* Phase431: intentionally inert. Never touch UFS MMIO from display diagnostics. */\n\t(void)snapshot_id;\n}\nEXPORT_SYMBOL_GPL(a52_p430_ufs_compact);\n'''
    text=text[:start]+stub+text[end:]
    return text+'\n/* '+MARK+': UFS sampler/MMIO access disabled. */\n'


def patch_rsc(text:str)->str:
    if MARK in text:
        return text
    if 'A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1' not in text:
        raise SystemExit('Phase431 requires inherited Phase301 RPMh trace lineage')
    anchor='''static bool a52_p301_disp_rsc(const struct rsc_drv *drv)\n{\n\treturn drv && drv->name && !strcmp(drv->name, "disp_rsc");\n}\n'''
    text=one(text,anchor,anchor+'\nextern bool a52_p431_trace_window_active(void);\n','trace-window extern')
    old='''\tirq_status = readl_relaxed(drv->tcs_base + RSC_DRV_IRQ_STATUS);\n\n\tfor_each_set_bit(i, &irq_status, BITS_PER_LONG) {\n'''
    new='''\tirq_status = readl_relaxed(drv->tcs_base + RSC_DRV_IRQ_STATUS);\n\tif (a52_p301_disp_rsc(drv) && a52_p431_trace_window_active())\n\t\ta52_ackfr_record("P431 RI e irq=%d st=%lx use=%u", irq, irq_status,\n\t\t\t(unsigned int)bitmap_weight(drv->tcs_in_use, MAX_TCS_NR));\n\n\tfor_each_set_bit(i, &irq_status, BITS_PER_LONG) {\n'''
    text=one(text,old,new,'RSC IRQ entry')
    old='''\treturn IRQ_HANDLED;\n}\n\n/**\n * __tcs_buffer_write()'''
    new='''\tif (a52_p301_disp_rsc(drv) && a52_p431_trace_window_active())\n\t\ta52_ackfr_record("P431 RI x irq=%d use=%u", irq,\n\t\t\t(unsigned int)bitmap_weight(drv->tcs_in_use, MAX_TCS_NR));\n\treturn IRQ_HANDLED;\n}\n\n/**\n * __tcs_buffer_write()'''
    text=one(text,old,new,'RSC IRQ exit')
    return text+'\n/* '+MARK+': TouchGrass-style low-noise Display-RSC IRQ chronology. */\n'


def patch_rec(text:str)->str:
    if MARK in text:
        return text
    if 'A52_PHASE430_SF_DMA_FORENSICS_V1' not in text:
        raise SystemExit('Phase431 requires Phase430 recorder lineage')
    text=one(text,
        'return !strncmp(message, "P430 ", 5) ||\n',
        'return !strncmp(message, "P431 ", 5) ||\n\t       !strncmp(message, "P430 ", 5) ||\n',
        'critical P431 admission')
    text=one(text,
        '\tif (unlikely(atomic_read(&a52_r280_retained)) &&\n\t    strncmp(fmt, "P430", 4) &&\n',
        '\tif (unlikely(atomic_read(&a52_r280_retained)) &&\n\t    strncmp(fmt, "P431", 4) &&\n\t    strncmp(fmt, "P430", 4) &&\n',
        'retained P431 admission')
    text=one(text,
        'if (strncmp(fmt, "P430", 4) &&\n',
        'if (strncmp(fmt, "P431", 4) &&\n    strncmp(fmt, "P430", 4) &&\n',
        'normal P431 admission')
    text=one(text,
        '\tif (!fmt || (\n\t    strncmp(fmt, "P430", 4) &&\n',
        '\tif (!fmt || (\n\t    strncmp(fmt, "P431", 4) &&\n\t    strncmp(fmt, "P430", 4) &&\n',
        'clean P431 admission')
    return text+'\n/* '+MARK+': P431 evidence admitted to R48 + sequential 3 MiB recorder. */\n'


def validate(root:Path)->None:
    files={p:(root/p).read_text(errors='replace') for p in (SYSCALL,UFS,RSC,REC)}
    s,u,rsc,rec=files[SYSCALL],files[UFS],files[RSC],files[REC]
    req_s=[MARK,'A52_P430_COPY_BYTES         0x2A00U','A52_P430_SNAPSHOTS          7U','A52_P430_SLOTS              42U',
           '0U, 1U, 3U, 5U, 20U, 60U','P431 SB id=%u rel=%llu','P431 SD id=%u nr=%u sf=%d cp=%d ss=%d',
           'P431 SE id=%u nr=%u','P431 SF0 p=%d ns=%llu','P431 AT0 t=%d ns=%llu',
           'A52_P431_STAGE_OFF          0x7E00U','A52_P431_TIMER_OFF          0x7E90U','A52_P431_THREAD_OFF         0x7F20U',
           'mrs %0, cntvct_el0','hrtimer_start(&a52_p431_live_timer','a52_p431_trace_window_active','a52_p430_snapshot(6U);']
    for tok in req_s:
        if tok not in s: raise SystemExit('Phase431 syscall missing: '+tok)
    if 'a52_p430_ufs_compact(snapshot_id);' in s or 'extern void a52_p430_ufs_compact' in s:
        raise SystemExit('Phase431 UFS call/reference remains in syscall')
    if 'WRITE_ONCE(a52_r378_hba, hba);' not in u:
        raise SystemExit('Phase431 expected dormant Phase378 assignment missing')
    if u.count('WRITE_ONCE(a52_r378_hba, hba);') != 1:
        raise SystemExit('Phase431 unexpected active HBA publication count')
    if u.count('a52_r378_start_snapshots(') != 1:
        raise SystemExit('Phase431 dormant Phase378 starter unexpectedly called')
    for tok in ('Phase431: intentionally inert','(void)snapshot_id;'):
        if tok not in u: raise SystemExit('Phase431 UFS stub missing: '+tok)
    for tok in ('extern bool a52_p431_trace_window_active(void);','P431 RI e irq=%d st=%lx use=%u','P431 RI x irq=%d use=%u'):
        if tok not in rsc: raise SystemExit('Phase431 RSC trace missing: '+tok)
    for tok in ('strncmp(fmt, "P431", 4)','!strncmp(message, "P431 ", 5)'):
        if tok not in rec: raise SystemExit('Phase431 recorder admission missing: '+tok)
    dsi=(root/Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')).read_text(errors='replace')
    hwc=(root/Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c')).read_text(errors='replace')
    smmu=(root/Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')).read_text(errors='replace')
    for tok in ('P430 PANIC_ARMED','a52_p430_dma_finalize','0xdead0000U'):
        if tok not in dsi: raise SystemExit('Phase431 inherited DSI forensic missing: '+tok)
    for tok in ('A52_P430_DMA_SAMPLES       7U','im->num_tcs = im->child_cfg & 0x3fU','a52_p430_smmu_forensic'):
        if tok not in hwc: raise SystemExit('Phase431 inherited HWC forensic missing: '+tok)
    for tok in ('ARM_SMMU_CB_ATS1PR','ARM_SMMU_CB_PAR','ops->iova_to_phys'):
        if tok not in smmu: raise SystemExit('Phase431 inherited SMMU forensic missing: '+tok)


def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--check-only',action='store_true')
    ns=ap.parse_args(); root=ns.root
    for p in (SYSCALL,UFS,RSC,REC,Path('drivers/a52_display/msm/dsi/dsi_ctrl.c'),Path('drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c'),Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')):
        if not (root/p).is_file(): raise SystemExit('Phase431 source missing: '+str(p))
    if not ns.check_only:
        for p,fn in ((SYSCALL,patch_syscall),(UFS,patch_ufs),(RSC,patch_rsc),(REC,patch_rec)):
            q=root/p; q.write_text(fn(q.read_text(errors='replace')))
    validate(root)
    print('Phase431 NoC-safe gap forensics: PASS')
    return 0

if __name__=='__main__': raise SystemExit(main())
