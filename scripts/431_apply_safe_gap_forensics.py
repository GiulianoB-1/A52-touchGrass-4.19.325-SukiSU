#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE431_SAFE_SF_GAP_FORENSICS_V1"
SYSCALL = Path("arch/arm64/kernel/syscall.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
RSC = Path("drivers/soc/qcom/rpmh-rsc.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase431 {label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)


WITNESS_BLOCK = r'''
/* A52_PHASE431_SAFE_SF_GAP_FORENSICS_V1
 *
 * The final 0x200 bytes of the Phase430 task sideband are unused once the
 * three task-copy strides are widened to 0x2a00.  Use that tail for one
 * three-copy execution witness.  It is driven by raw CNTPCT reads during
 * short CPU7 busy-execution windows, not by timer callbacks.
 */
#define A52_P431_WITNESS_OFF         0x7e00U
#define A52_P431_WITNESS_BYTES       0x0200U
#define A52_P431_WITNESS_STRIDE      0x0080U
#define A52_P431_WITNESS_MAGIC       0x3133345745584543ULL
#define A52_P431_WITNESS_COMMIT      0x431c0de5U
#define A52_P431_WITNESS_VERSION     1U
#define A52_P431_EXEC_WINDOWS        16U
#define A52_P431_EXEC_START_MS       4500ULL
#define A52_P431_EXEC_PERIOD_MS      1000ULL
#define A52_P431_EXEC_BUSY_MS        800ULL

struct a52_p431_witness {
	u64 magic;
	u64 cntpct;
	u64 sf_start_ns;
	u64 loops;
	u64 write_ns;
	u32 sequence;
	u32 sequence_inv;
	u32 cpu;
	u32 stage;
	u32 snapshot_id;
	u32 cntfrq;
	u32 crc32c;
	u32 commit;
	u32 version;
	u32 reserved;
};

static DEFINE_RAW_SPINLOCK(a52_p431_witness_lock);
static atomic_t a52_p431_witness_seq = ATOMIC_INIT(0);
static struct task_struct *a52_p431_exec_task;

bool a52_p431_trace_window_active(void)
{
	u64 start = READ_ONCE(a52_p430_sf_start_ns);
	u64 now;

	if (!start)
		return false;
	now = ktime_get_boottime_ns();
	return now >= start && now - start <= 70ULL * NSEC_PER_SEC;
}
EXPORT_SYMBOL_GPL(a52_p431_trace_window_active);

static __always_inline u64 a52_p431_cntpct(void)
{
	u64 v;
	asm volatile("isb; mrs %0, cntpct_el0" : "=r" (v));
	return v;
}

static __always_inline u32 a52_p431_cntfrq(void)
{
	u64 v;
	asm volatile("mrs %0, cntfrq_el0" : "=r" (v));
	return (u32)v;
}

static void a52_p431_witness_write(u32 stage, u32 snapshot_id, u64 loops)
{
	struct a52_p431_witness w;
	unsigned long flags;
	void *d0, *d1, *d2;
	u32 seq;

	if (!READ_ONCE(a52_p430_task_sideband))
		return;

	memset(&w, 0, sizeof(w));
	seq = (u32)atomic_inc_return(&a52_p431_witness_seq);
	w.magic = A52_P431_WITNESS_MAGIC;
	w.cntpct = a52_p431_cntpct();
	w.sf_start_ns = READ_ONCE(a52_p430_sf_start_ns);
	w.loops = loops;
	w.write_ns = ktime_get_boottime_ns();
	w.sequence = seq;
	w.sequence_inv = ~seq;
	w.cpu = (u32)task_cpu(current);
	w.stage = stage;
	w.snapshot_id = snapshot_id;
	w.cntfrq = a52_p431_cntfrq();
	w.crc32c = a52_r382_crc32c(&w,
		offsetof(struct a52_p431_witness, crc32c));
	w.commit = A52_P431_WITNESS_COMMIT;
	w.version = A52_P431_WITNESS_VERSION;

	BUILD_BUG_ON(sizeof(w) != 80U);
	BUILD_BUG_ON(3U * A52_P431_WITNESS_STRIDE > A52_P431_WITNESS_BYTES);

	raw_spin_lock_irqsave(&a52_p431_witness_lock, flags);
	d0 = (u8 *)a52_p430_task_sideband + A52_P431_WITNESS_OFF;
	d1 = (u8 *)d0 + A52_P431_WITNESS_STRIDE;
	d2 = (u8 *)d1 + A52_P431_WITNESS_STRIDE;
	memcpy(d0, &w, sizeof(w));
	memcpy(d1, &w, sizeof(w));
	memcpy(d2, &w, sizeof(w));
	wmb();
	__flush_dcache_area(d0, sizeof(w));
	__flush_dcache_area(d1, sizeof(w));
	__flush_dcache_area(d2, sizeof(w));
	dsb(sy);
	raw_spin_unlock_irqrestore(&a52_p431_witness_lock, flags);
}

static int a52_p431_exec_fn(void *unused)
{
	u64 sf_ns, target_ns;
	u64 freq, start, end, next, cnt, loops;
	u32 window;
	int rc;

	(void)unused;
	rc = set_cpus_allowed_ptr(current, cpumask_of(7));
	a52_ackfr_record("P431 EXEC affinity cpu=7 rc=%d", rc);
	if (rc)
		return 0;

	while (!kthread_should_stop()) {
		sf_ns = READ_ONCE(a52_p430_sf_start_ns);
		if (sf_ns)
			break;
		msleep(20);
	}
	if (kthread_should_stop())
		return 0;

	freq = (u64)a52_p431_cntfrq();
	if (!freq)
		freq = 19200000ULL;

	for (window = 0; window < A52_P431_EXEC_WINDOWS; window++) {
		if (kthread_should_stop() || atomic_read(&a52_p430_first_atomic))
			break;

		target_ns = sf_ns +
			(A52_P431_EXEC_START_MS +
			 (u64)window * A52_P431_EXEC_PERIOD_MS) * NSEC_PER_MSEC;
		while (!kthread_should_stop() &&
		       ktime_get_boottime_ns() < target_ns)
			msleep(5);
		if (kthread_should_stop() || atomic_read(&a52_p430_first_atomic))
			break;

		start = a52_p431_cntpct();
		end = start + div_u64(freq * A52_P431_EXEC_BUSY_MS, 1000ULL);
		next = start;
		loops = 0;
		for (;;) {
			loops++;
			cnt = a52_p431_cntpct();
			if ((s64)(cnt - next) >= 0) {
				a52_p431_witness_write(4U, window, loops);
				next = cnt + div_u64(freq, 10ULL);
			}
			if ((s64)(cnt - end) >= 0)
				break;
			cpu_relax();
		}
		cond_resched();
	}

	a52_p431_witness_write(5U, window, 0);
	a52_ackfr_record("P431 EXEC done windows=%u", window);
	return 0;
}
'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text

    for tok in (
        "A52_PHASE430_SF_DMA_FORENSICS_V1",
        "A52_PHASE430_HARDENING_V2",
        "static void *a52_p430_task_sideband;",
        "static u64 a52_p430_sf_start_ns;",
    ):
        if tok not in text:
            raise SystemExit("Phase431 syscall prerequisite missing: " + tok)

    text = one(text,
        "#define A52_P430_COPY_BYTES         0x2800U\n",
        "#define A52_P430_COPY_BYTES         0x2A00U\n",
        "task copy stride")
    text = one(text,
        "#define A52_P430_SNAPSHOTS          6U\n",
        "#define A52_P430_SNAPSHOTS          7U\n",
        "snapshot count")
    text = one(text,
        "#define A52_P430_SLOTS              36U\n",
        "#define A52_P430_SLOTS              42U\n",
        "slot count")

    text = one(text,
        "static u64 a52_p430_sf_start_ns;\nextern void a52_p430_ufs_compact(unsigned int snapshot_id);\n",
        "static u64 a52_p430_sf_start_ns;\n\n" + WITNESS_BLOCK + "\n",
        "remove UFS extern and add witness")

    text = one(text,
        'a52_ackfr_record("P430 AT0 t=%d ns=%llu", tgid,\n',
        'a52_ackfr_record("P431 AT0 t=%d ns=%llu", tgid,\n',
        "atomic marker")

    start = text.find("static void a52_p430_snapshot(unsigned int snapshot_id)\n")
    end = text.find("static int a52_p430_sampler_fn(void *unused)\n", start)
    if start < 0 or end < 0:
        raise SystemExit("Phase431 snapshot function boundaries missing")
    snapshot = r'''static void a52_p430_snapshot(unsigned int snapshot_id)
{
	struct task_struct *tasks[A52_P430_PER_SNAPSHOT];
	u32 roles[A52_P430_PER_SNAPSHOT];
	struct a52_p430_task_record r;
	unsigned int nr, i, base = snapshot_id * A52_P430_PER_SNAPSHOT;
	int sf = -1, cp = -1, ss = -1;
	u64 rel = a52_p430_sf_start_ns ?
		ktime_get_boottime_ns() - a52_p430_sf_start_ns : 0;

	/* Persist the boundary before any task walk, stack capture, or other work. */
	a52_ackfr_record("P431 SB id=%u rel=%llu", snapshot_id,
		(unsigned long long)rel);
	a52_p431_witness_write(2U, snapshot_id, 0);

	memset(tasks, 0, sizeof(tasks));
	memset(roles, 0, sizeof(roles));
	nr = a52_p430_display_tasks(tasks, roles, A52_P430_PER_SNAPSHOT,
		&sf, &cp, &ss);
	a52_ackfr_record("P431 SL id=%u nr=%u sf=%d cp=%d ss=%d",
		snapshot_id, nr, sf, cp, ss);

	for (i = 0; i < nr; i++) {
		a52_ackfr_record("P431 TB id=%u i=%u p=%d",
			snapshot_id, i, tasks[i] ? tasks[i]->pid : -1);
		a52_p430_fill(&r, tasks[i], snapshot_id, roles[i], i);
		a52_p430_write(base + i, &r);
		a52_ackfr_record("P431 TD id=%u i=%u p=%d",
			snapshot_id, i, tasks[i] ? tasks[i]->pid : -1);
		put_task_struct(tasks[i]);
	}

	a52_p431_witness_write(3U, snapshot_id, 0);
	a52_ackfr_record("P431 SD id=%u nr=%u rel=%llu",
		snapshot_id, nr,
		(unsigned long long)(a52_p430_sf_start_ns ?
		(ktime_get_boottime_ns() - a52_p430_sf_start_ns) : 0));
}

'''
    text = text[:start] + snapshot + text[end:]

    start = text.find("static int a52_p430_sampler_fn(void *unused)\n")
    end = text.find("static int __init a52_p430_init(void)\n", start)
    if start < 0 or end < 0:
        raise SystemExit("Phase431 sampler function boundaries missing")
    sampler = r'''static int a52_p430_sampler_fn(void *unused)
{
	static const u32 rel_s[5] = { 1U, 3U, 5U, 20U, 60U };
	unsigned int next = 0;
	bool endpoint_done = false;
	bool immediate_done = false;
	struct task_struct *sf;
	u64 elapsed;

	while (!kthread_should_stop()) {
		if (!a52_p430_sf_start_ns) {
			sf = a52_p430_find_leader(A52_P430_ROLE_SF);
			if (sf) {
				a52_p430_sf_start_ns = ktime_get_boottime_ns();
				a52_ackfr_record("P431 SF0 p=%d ns=%llu", sf->pid,
					(unsigned long long)a52_p430_sf_start_ns);
				a52_p431_witness_write(1U, 0U, 0);
				put_task_struct(sf);
			}
		}
		if (a52_p430_sf_start_ns && !immediate_done) {
			a52_p430_snapshot(0U);
			immediate_done = true;
		}
		if (a52_p430_sf_start_ns) {
			elapsed = div_u64(ktime_get_boottime_ns() - a52_p430_sf_start_ns,
				NSEC_PER_SEC);
			while (next < ARRAY_SIZE(rel_s) && elapsed >= rel_s[next]) {
				a52_p430_snapshot(next + 1U);
				next++;
			}
			if (!endpoint_done && atomic_read(&a52_p430_first_atomic)) {
				a52_p430_snapshot(6U);
				endpoint_done = true;
			}
			if (endpoint_done && next >= ARRAY_SIZE(rel_s))
				break;
		}
		if (msleep_interruptible(50) && kthread_should_stop())
			break;
	}
	return 0;
}

'''
    text = text[:start] + sampler + text[end:]

    init_anchor = '''	a52_p430_sampler = kthread_run(a52_p430_sampler_fn, NULL, "a52_p430_sf");
	if (IS_ERR(a52_p430_sampler))
		a52_p430_sampler = NULL;
	return 0;
'''
    init_new = '''	a52_p430_sampler = kthread_run(a52_p430_sampler_fn, NULL, "a52_p430_sf");
	if (IS_ERR(a52_p430_sampler))
		a52_p430_sampler = NULL;
	a52_p431_exec_task = kthread_run(a52_p431_exec_fn, NULL, "a52_p431_exec");
	if (IS_ERR(a52_p431_exec_task)) {
		a52_ackfr_record("P431 EXEC err=%ld", PTR_ERR(a52_p431_exec_task));
		a52_p431_exec_task = NULL;
	}
	return 0;
'''
    text = one(text, init_anchor, init_new, "start execution witness")

    assert_anchor = '''	BUILD_BUG_ON(3U * A52_P430_COPY_BYTES > A52_P430_TASK_BYTES);
'''
    assert_new = '''	BUILD_BUG_ON(3U * A52_P430_COPY_BYTES > A52_P430_TASK_BYTES);
	BUILD_BUG_ON(3U * A52_P430_COPY_BYTES != A52_P431_WITNESS_OFF);
	BUILD_BUG_ON(A52_P431_WITNESS_OFF + A52_P431_WITNESS_BYTES >
		A52_P430_TASK_BYTES);
'''
    text = one(text, assert_anchor, assert_new, "sideband geometry assertions")

    text += "\n/* " + MARK + ": no UFS sampling; immediate/gap snapshots + CNTPCT witness. */\n"
    return text


def patch_ufs(text: str) -> str:
    if MARK in text:
        return text

    active = '''	/* Phase430: retain HBA identity even though Phase378 sampler stays retired. */
	WRITE_ONCE(a52_r378_hba, hba);
'''
    if active in text:
        text = one(text, active,
            "\t/* Phase431: do not reactivate out-of-band UFS sampling. */\n",
            "remove Phase430 HBA registration")
    # One historical assignment is intentionally retained inside the dormant
    # Phase378 starter.  Phase431 only removes the Phase430 probe-time publish.
    if text.count("WRITE_ONCE(a52_r378_hba, hba);") != 1:
        raise SystemExit("Phase431 unexpected a52_r378_hba assignment count")
    if text.count("a52_r378_start_snapshots(") != 1:
        raise SystemExit("Phase431 dormant Phase378 starter has an active call site")

    text += (
        "\n/* " + MARK + "\n"
        " * Phase430's SF snapshot must not touch UFS MMIO.  Historical UFS\n"
        " * helpers may remain compiled, but the HBA is not registered for them\n"
        " * and the Phase431 userspace sampler has no call site.\n"
        " */\n"
    )
    return text



def patch_rsc(text: str) -> str:
    if MARK in text:
        return text

    if "A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1" not in text:
        raise SystemExit("Phase431 requires inherited Phase301 RPMh/RSC trace")

    helper = '''/* A52_PHASE301_RPMH_RSC_CONTRACT_TRACE_V1: observation only. */
static bool a52_p301_disp_rsc(const struct rsc_drv *drv)
{
\treturn drv && drv->name && !strcmp(drv->name, "disp_rsc");
}
'''
    helper_new = helper + '''
/* Phase431: bounded persistent equivalent of the TouchGrass RPMh IRQ events. */
extern bool a52_p431_trace_window_active(void);
static atomic_t a52_p431_ri_seq = ATOMIC_INIT(0);
'''
    text = one(text, helper, helper_new, "Display-RSC trace helper")

    text = one(text,
        '''\tstruct tcs_cmd *cmd;

\tirq_status = readl_relaxed(drv->tcs_base + RSC_DRV_IRQ_STATUS);
''',
        '''\tstruct tcs_cmd *cmd;
\tint a52_p431_ri = 0;

\tirq_status = readl_relaxed(drv->tcs_base + RSC_DRV_IRQ_STATUS);
\tif (a52_p301_disp_rsc(drv) && a52_p431_trace_window_active()) {
\t\ta52_p431_ri = atomic_inc_return(&a52_p431_ri_seq);
\t\tif (a52_p431_ri <= 96)
\t\t\ta52_ackfr_record("P431 RI e n=%d irq=%d st=%lx use=%u",
\t\t\t\ta52_p431_ri, irq, irq_status,
\t\t\t\t(unsigned int)bitmap_weight(drv->tcs_in_use, MAX_TCS_NR));
\t}
''',
        "Display-RSC IRQ entry")

    text = one(text,
        '''\t\ttrace_rpmh_tx_done(drv, i, req, err);

\t\t/* Clear AMC trigger & enable modes and
''',
        '''\t\ttrace_rpmh_tx_done(drv, i, req, err);
\t\tif (a52_p431_ri > 0 && a52_p431_ri <= 96)
\t\t\ta52_ackfr_record("P431 RC n=%d id=%d e=%d st=%u c=%u",
\t\t\t\ta52_p431_ri, i, err, req->state, req->num_cmds);

\t\t/* Clear AMC trigger & enable modes and
''',
        "Display-RSC completion")

    text = one(text,
        '''\treturn IRQ_HANDLED;
}

/**
 * __tcs_buffer_write()''',
        '''\tif (a52_p431_ri > 0 && a52_p431_ri <= 96)
\t\ta52_ackfr_record("P431 RI x n=%d use=%u",
\t\t\ta52_p431_ri,
\t\t\t(unsigned int)bitmap_weight(drv->tcs_in_use, MAX_TCS_NR));
\treturn IRQ_HANDLED;
}

/**
 * __tcs_buffer_write()''',
        "Display-RSC IRQ exit")

    text += "\n/* " + MARK + ": bounded TouchGrass-style Display-RSC IRQ chronology. */\n"
    return text


def patch_rec(text: str) -> str:
    if MARK in text:
        return text

    crit = 'return !strncmp(message, "P430 ", 5) ||'
    text = one(text, crit,
        'return !strncmp(message, "P431 ", 5) ||\n\t       !strncmp(message, "P430 ", 5) ||',
        "critical P431 admission")

    n = text.count('strncmp(fmt, "P430", 4) &&')
    if n < 3:
        raise SystemExit(f"Phase431 expected >=3 P430 recorder gates, found {n}")
    text = text.replace(
        'strncmp(fmt, "P430", 4) &&',
        'strncmp(fmt, "P431", 4) &&\n\t    strncmp(fmt, "P430", 4) &&'
    )

    text += "\n/* " + MARK + ": P431 snapshot/witness markers admitted. */\n"
    return text


def validate(root: Path) -> None:
    s = (root / SYSCALL).read_text(errors="replace")
    u = (root / UFS).read_text(errors="replace")
    r = (root / REC).read_text(errors="replace")
    q = (root / RSC).read_text(errors="replace")

    for tok in (
        MARK,
        "#define A52_P430_COPY_BYTES         0x2A00U",
        "#define A52_P430_SNAPSHOTS          7U",
        "#define A52_P430_SLOTS              42U",
        "A52_P431_WITNESS_OFF         0x7e00U",
        "A52_P431_EXEC_WINDOWS        16U",
        '"P431 SF0 p=%d ns=%llu"',
        '"P431 SB id=%u rel=%llu"',
        '"P431 SL id=%u nr=%u sf=%d cp=%d ss=%d"',
        '"P431 TB id=%u i=%u p=%d"',
        '"P431 TD id=%u i=%u p=%d"',
        '"P431 SD id=%u nr=%u rel=%llu"',
        "static const u32 rel_s[5] = { 1U, 3U, 5U, 20U, 60U };",
        "a52_p430_snapshot(0U);",
        "a52_p430_snapshot(6U);",
        "a52_p431_exec_fn",
        'mrs %0, cntpct_el0',
        "set_cpus_allowed_ptr(current, cpumask_of(7))",
        "a52_p431_trace_window_active",
    ):
        if tok not in s:
            raise SystemExit("Phase431 syscall token missing: " + tok)

    if "a52_p430_ufs_compact(snapshot_id);" in s:
        raise SystemExit("Phase431 UFS snapshot call is still active")
    if "extern void a52_p430_ufs_compact" in s:
        raise SystemExit("Phase431 UFS snapshot extern is still active")
    if u.count("WRITE_ONCE(a52_r378_hba, hba);") != 1:
        raise SystemExit("Phase431 unexpected a52_r378_hba assignment count")
    if u.count("a52_r378_start_snapshots(") != 1:
        raise SystemExit("Phase431 dormant Phase378 starter has an active call site")
    if "Phase430: retain HBA identity even though Phase378 sampler stays retired." in u:
        raise SystemExit("Phase431 Phase430 probe-time HBA registration is still active")

    for tok in (
        MARK,
        '!strncmp(message, "P431 ", 5)',
        'strncmp(fmt, "P431", 4)',
    ):
        if tok not in r:
            raise SystemExit("Phase431 recorder token missing: " + tok)

    for tok in (
        MARK,
        'P431 RI e n=%d irq=%d st=%lx use=%u',
        'P431 RC n=%d id=%d e=%d st=%u c=%u',
        'P431 RI x n=%d use=%u',
        'trace_rpmh_tx_done(drv, i, req, err);',
        'P276 301R e st=%d ty=%d n=%d off=%d use=%u ws=%u irq=%d',
    ):
        if tok not in q:
            raise SystemExit("Phase431 RSC chronology token missing: " + tok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (SYSCALL, UFS, REC, RSC):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase431 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / SYSCALL
        p.write_text(patch_syscall(p.read_text(errors="replace")))
        p = ns.root / UFS
        p.write_text(patch_ufs(p.read_text(errors="replace")))
        p = ns.root / REC
        p.write_text(patch_rec(p.read_text(errors="replace")))
        p = ns.root / RSC
        p.write_text(patch_rsc(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase431 safe SF-gap forensics: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
