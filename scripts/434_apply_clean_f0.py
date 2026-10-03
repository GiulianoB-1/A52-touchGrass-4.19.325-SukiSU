#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARK = "A52_PHASE434_CLEAN_F0_V1"

SYSCALL = Path("arch/arm64/kernel/syscall.c")
MSM = Path("drivers/a52_display/msm/msm_drv.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
DSI_HW = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
EXEC = Path("fs/exec.c")
EXIT = Path("kernel/exit.c")
SIGNAL = Path("kernel/signal.c")
COREDUMP = Path("fs/coredump.c")
REGCORE = Path("drivers/regulator/core.c")
NAMEI = Path("fs/namei.c")
VERITY_ENABLE = Path("fs/verity/enable.c")
VERITY_MEASURE = Path("fs/verity/measure.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
A52_MAKE = Path("drivers/a52_secure/Makefile")
HELPER = Path("drivers/a52_secure/a52_phase434_clean.c")


def die(msg: str) -> None:
    raise SystemExit("Phase434: " + msg)


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


def function_span(text: str, name: str) -> tuple[int, int]:
    rx = re.compile(r"(?m)^[^\n;]*\b" + re.escape(name) + r"\s*\(")
    for m in rx.finditer(text):
        line_end = text.find("\n", m.start())
        if line_end < 0:
            line_end = len(text)
        stripped = text[m.start():line_end].lstrip()
        if stripped.startswith(("*", "/*", "//", "#")):
            continue
        start = m.start()
        # Some kernel functions split the return type from the function name.
        # Include that preceding declaration line in the removable span.
        prev_end = start - 1
        if prev_end >= 0:
            prev_start = text.rfind("\n", 0, prev_end) + 1
            prev = text[prev_start:start].strip()
            if (prev.startswith("static ") and
                    ";" not in prev and "(" not in prev and ")" not in prev):
                start = prev_start
        paren = text.find("(", m.start())
        if paren < 0:
            continue
        semi = text.find(";", paren)
        brace = text.find("{", paren)
        if brace < 0 or (semi >= 0 and semi < brace):
            continue
        depth = 0
        for i in range(brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    if end < len(text) and text[end] == "\n":
                        end += 1
                    return start, end
    die("function not found: " + name)


def remove_function(text: str, name: str, required: bool = True) -> str:
    try:
        a, b = function_span(text, name)
    except SystemExit:
        if required:
            raise
        return text
    return text[:a] + text[b:]


def replace_function(text: str, name: str, replacement: str) -> str:
    a, b = function_span(text, name)
    return text[:a] + replacement.rstrip() + "\n\n" + text[b:]


def insert_before_last_return(text: str, name: str, snippet: str) -> str:
    a, b = function_span(text, name)
    fn = text[a:b]
    matches = list(re.finditer(r"(?m)^(\s*)return\s+ret\s*;", fn))
    if not matches:
        die(f"{name}: no return ret anchor")
    m = matches[-1]
    indent = m.group(1)
    repl = "".join(indent + line + "\n" for line in snippet.strip().splitlines())
    fn = fn[:m.start()] + repl + fn[m.start():]
    return text[:a] + fn + text[b:]


HELPER_C = r'''// SPDX-License-Identifier: GPL-2.0-only
/* A52_PHASE434_CLEAN_F0_V1
 *
 * Tiny event-driven shared state for the clean SurfaceFlinger/F0 experiment.
 * No polling threads and no remote task inspection.
 */
#include <linux/a52_ack_secure_flight_recorder.h>
#include <linux/atomic.h>
#include <linux/export.h>
#include <linux/init.h>
#include <linux/ktime.h>
#include <linux/sched.h>
#include <linux/spinlock.h>
#include <linux/string.h>

#define A52_P434_SF_WINDOW_NS (10ULL * NSEC_PER_SEC)

static atomic64_t a52_p434_sf_ns = ATOMIC64_INIT(0);

struct a52_p434_rail_state {
	const char *name;
	int enabled;
	int uv;
	int users;
	u64 ns;
};

static struct a52_p434_rail_state a52_p434_rails[] = {
	{ "pm6350_s5_level", -1, -1, -1, 0 },
	{ "pm6350_l22",      -1, -1, -1, 0 },
	{ "refgen",          -1, -1, -1, 0 },
	{ "panel_vci",       -1, -1, -1, 0 },
	{ "panel_vddi",      -1, -1, -1, 0 },
	{ "mdss_core_gdsc",  -1, -1, -1, 0 },
};
static DEFINE_SPINLOCK(a52_p434_rail_lock);

void a52_p434_note_exec_comm(const char *name, bool exec)
{
	u64 ns;

	if (!exec || !name)
		return;
	ns = ktime_get_boottime_ns();
	if (!strcmp(name, "surfaceflinger")) {
		atomic64_cmpxchg(&a52_p434_sf_ns, 0, (s64)ns);
		a52_ackfr_record("P434 SF EXEC p=%d t=%d ns=%llu",
			current->pid, current->tgid, (unsigned long long)ns);
	} else if (!strcmp(name, "bootanimation")) {
		a52_ackfr_record("P434 BA EXEC p=%d t=%d ns=%llu",
			current->pid, current->tgid, (unsigned long long)ns);
	}
}
EXPORT_SYMBOL_GPL(a52_p434_note_exec_comm);

bool a52_p434_sf_window_active(void)
{
	u64 start = (u64)atomic64_read(&a52_p434_sf_ns);
	u64 now;

	if (!start)
		return false;
	now = ktime_get_boottime_ns();
	return now >= start && now - start <= A52_P434_SF_WINDOW_NS;
}
EXPORT_SYMBOL_GPL(a52_p434_sf_window_active);

void a52_p434_rail_event(const char *name, int enabled, int uv, int users)
{
	unsigned long flags;
	unsigned int i;
	u64 ns;

	if (!name)
		return;
	for (i = 0; i < ARRAY_SIZE(a52_p434_rails); i++)
		if (!strcmp(name, a52_p434_rails[i].name))
			break;
	if (i == ARRAY_SIZE(a52_p434_rails))
		return;

	ns = ktime_get_boottime_ns();
	spin_lock_irqsave(&a52_p434_rail_lock, flags);
	if (enabled >= 0)
		a52_p434_rails[i].enabled = enabled;
	if (uv >= 0)
		a52_p434_rails[i].uv = uv;
	if (users >= 0)
		a52_p434_rails[i].users = users;
	a52_p434_rails[i].ns = ns;
	spin_unlock_irqrestore(&a52_p434_rail_lock, flags);

	a52_ackfr_record("P434 RAIL i=%u en=%d uv=%d u=%d ns=%llu",
		i, enabled, uv, users, (unsigned long long)ns);
}
EXPORT_SYMBOL_GPL(a52_p434_rail_event);

void a52_p434_dump_rails(const char *tag)
{
	struct a52_p434_rail_state s;
	unsigned long flags;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(a52_p434_rails); i++) {
		spin_lock_irqsave(&a52_p434_rail_lock, flags);
		s = a52_p434_rails[i];
		spin_unlock_irqrestore(&a52_p434_rail_lock, flags);
		a52_ackfr_record("P434 RS %.6s i=%u en=%d uv=%d u=%d age=%llu",
			tag ? tag : "-", i, s.enabled, s.uv, s.users,
			s.ns ? (unsigned long long)(ktime_get_boottime_ns() - s.ns) : 0ULL);
	}
}
EXPORT_SYMBOL_GPL(a52_p434_dump_rails);

static int __init a52_p434_init(void)
{
	a52_ackfr_record("P434 BOOT ns=%llu",
		(unsigned long long)ktime_get_boottime_ns());
	return 0;
}
core_initcall(a52_p434_init);
'''


P434_MSM_BLOCK = r'''
/* A52_PHASE434_CLEAN_F0_V1
 * Compact Composer UAPI chronology.  Property discovery is cached silently;
 * only the SF exec -> SF+10s window is emitted.
 */
extern bool a52_p434_sf_window_active(void);
static atomic_t a52_p434_ioctl_seq = ATOMIC_INIT(0);
static atomic_t a52_p434_prop_active = ATOMIC_INIT(0);
static atomic_t a52_p434_prop_dpms = ATOMIC_INIT(0);
static atomic_t a52_p434_prop_crtc = ATOMIC_INIT(0);
static atomic_t a52_p434_prop_fb = ATOMIC_INIT(0);

static void a52_p434_learn_property(unsigned int cmd, unsigned long arg, long rc)
{
	struct drm_mode_get_property v;

	if (rc || _IOC_NR(cmd) != 0xAA)
		return;
	if (copy_from_user(&v, (void __user *)arg, sizeof(v)))
		return;
	if (!strcmp(v.name, "ACTIVE"))
		atomic_set(&a52_p434_prop_active, (int)v.prop_id);
	else if (!strcmp(v.name, "DPMS"))
		atomic_set(&a52_p434_prop_dpms, (int)v.prop_id);
	else if (!strcmp(v.name, "CRTC_ID"))
		atomic_set(&a52_p434_prop_crtc, (int)v.prop_id);
	else if (!strcmp(v.name, "FB_ID"))
		atomic_set(&a52_p434_prop_fb, (int)v.prop_id);
}

static bool a52_p434_interesting_prop(u32 id)
{
	return id &&
		(id == (u32)atomic_read(&a52_p434_prop_active) ||
		 id == (u32)atomic_read(&a52_p434_prop_dpms) ||
		 id == (u32)atomic_read(&a52_p434_prop_crtc) ||
		 id == (u32)atomic_read(&a52_p434_prop_fb));
}

static void a52_p434_record_properties(unsigned int n, unsigned int cmd,
				       unsigned long arg, long rc)
{
	unsigned int nr = _IOC_NR(cmd);
	void __user *up = (void __user *)arg;

	if (rc)
		return;
	if (nr == 0xAB) {
		struct drm_mode_connector_set_property v;
		if (!copy_from_user(&v, up, sizeof(v)) &&
		    a52_p434_interesting_prop(v.prop_id))
			a52_ackfr_record("P434 PROP n=%u obj=%u id=%u v=%llx",
				n, v.connector_id, v.prop_id,
				(unsigned long long)v.value);
	} else if (nr == 0xBC) {
		struct drm_mode_atomic a;
		u32 objs[16], counts[16], props[64];
		u64 vals[64];
		u32 i, j, total = 0, pos = 0;

		if (copy_from_user(&a, up, sizeof(a)) || !a.count_objs ||
		    a.count_objs > ARRAY_SIZE(objs))
			return;
		if (copy_from_user(objs, (void __user *)(unsigned long)a.objs_ptr,
				   a.count_objs * sizeof(objs[0])) ||
		    copy_from_user(counts,
				   (void __user *)(unsigned long)a.count_props_ptr,
				   a.count_objs * sizeof(counts[0])))
			return;
		for (i = 0; i < a.count_objs; i++) {
			if (counts[i] > ARRAY_SIZE(props) - total)
				return;
			total += counts[i];
		}
		if (!total)
			return;
		if (copy_from_user(props, (void __user *)(unsigned long)a.props_ptr,
				   total * sizeof(props[0])) ||
		    copy_from_user(vals,
				   (void __user *)(unsigned long)a.prop_values_ptr,
				   total * sizeof(vals[0])))
			return;
		for (i = 0; i < a.count_objs; i++)
			for (j = 0; j < counts[i]; j++, pos++)
				if (a52_p434_interesting_prop(props[pos]))
					a52_ackfr_record(
						"P434 PROP n=%u obj=%u id=%u v=%llx",
						n, objs[i], props[pos],
						(unsigned long long)vals[pos]);
	}
}

static void a52_p434_drm_post(bool composer, unsigned int cmd,
			      unsigned long arg, long rc)
{
	unsigned int n;

	if (!composer)
		return;
	a52_p434_learn_property(cmd, arg, rc);
	if (!a52_p434_sf_window_active())
		return;
	n = (unsigned int)atomic_inc_return(&a52_p434_ioctl_seq);
	a52_ackfr_record("P434 IO n=%u nr=%x rc=%ld",
		n, _IOC_NR(cmd), rc);
	a52_p434_record_properties(n, cmd, arg, rc);
}
'''


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text

    text = text.replace(
        "\t\tif (READ_ONCE(a52_p430_sampler))\n"
        "\t\t\twake_up_process(READ_ONCE(a52_p430_sampler));\n",
        "",
    )

    # Remove all periodic/remote-task machinery.  Keep only the tiny persistent
    # sideband allocation because P432 frontier records still use it.
    for name in (
        # Current SF-gap samplers.
        "a52_p431_exec_fn",
        "a52_p431_witness_write",
        "a52_p430_find_leader",
        "a52_p430_has",
        "a52_p430_add",
        "a52_p430_preferred",
        "a52_p430_add_one_thread",
        "a52_p430_display_tasks",
        "a52_p430_fill",
        "a52_p430_write",
        "a52_p430_snapshot",
        "a52_p430_sampler_fn",

        # Older remote-task samplers are also incompatible with CLEAN-F0's
        # event-driven-only rule.  They are already retired from the modern
        # runtime lineage, but their compiled function bodies still contain
        # get_wchan()/remote stack inspection and therefore fail the binary
        # hygiene audit.  Remove the whole obsolete chains rather than merely
        # weakening that audit.
        "a52_r377_write_slot",
        "a52_r377_get_latest_apexd_leader",
        "a52_r377_collect_thread_refs",
        "a52_r377_fill_task",
        "a52_r377_take_snapshot",
        "a52_r377_sampler_fn",
        "a52_r377_init",
        "a52_r380_get_latest_vdc",
        "a52_r380_vdc_snapshot",
        "a52_r380_vdc_sampler_fn",
        "a52_r380_vdc_init",
        "a52_p430_comm_is",
        "a52_r373_sample_blocked_mounts",
        "a52_r373_sampler_fn",
        "a52_r373_init",
    ):
        text = remove_function(text, name, required=False)

    text = text.replace("static struct task_struct *a52_p430_sampler;\n", "")
    text = text.replace("static struct task_struct *a52_p431_exec_task __maybe_unused;\n", "")
    text = text.replace("static struct task_struct *a52_p431_exec_task;\n", "")
    text = text.replace("static struct task_struct *a52_r377_sampler_task;\n", "")
    text = text.replace("static void *a52_r377_sideband;\n", "")
    text = text.replace("static struct task_struct *a52_r380_vdc_sampler_task;\n", "")
    text = re.sub(
        r"static const u32 a52_r380_vdc_target_ms\[A52_R380_VDC_SNAPSHOT_COUNT\] = \{.*?\};\n",
        "",
        text,
        count=1,
        flags=re.S,
    )
    text = text.replace("static struct task_struct *a52_r373_sampler_task;\n", "")

    invoke = "\tinvoke_syscall(regs, scno, sc_nr, syscall_table);\n"
    if invoke not in text:
        die("syscall invoke anchor missing for ART key trace")
    text = text.replace(
        invoke,
        invoke +
        "\tif ((!strcmp(current->comm, \"odsign\") ||\n"
        "\t     !strcmp(current->comm, \"odrefresh\")) &&\n"
        "\t    (scno == __NR_add_key || scno == __NR_keyctl))\n"
        "\t\ta52_ackfr_record(\"P434 ART KEY sc=%d op=%llx rc=%ld\",\n"
        "\t\t\tscno, scno == __NR_keyctl ?\n"
        "\t\t\t(unsigned long long)regs->orig_x0 : 0ULL,\n"
        "\t\t\t(long)regs->regs[0]);\n",
        1,
    )

    init = r'''static int __init a52_p430_init(void)
{
	a52_p430_task_sideband = memremap(A52_P430_TASK_PHYS,
		A52_P430_TASK_BYTES, MEMREMAP_WB);
	if (!a52_p430_task_sideband)
		return 0;
	memset(a52_p430_task_sideband, 0, A52_P430_TASK_BYTES);
	wmb();
	__flush_dcache_area(a52_p430_task_sideband, A52_P430_TASK_BYTES);
	dsb(sy);
	a52_ackfr_record("P434 CLEAN sampler=0 remote_task=0");
	return 0;
}
late_initcall(a52_p430_init);
'''
    a, b = function_span(text, "a52_p430_init")
    tail = text[b:]
    # Remove the original initcall immediately following the function.
    tail = re.sub(r"^\s*late_initcall\(a52_p430_init\);\s*\n", "", tail, count=1)
    text = text[:a] + init + "\n" + tail

    text += "\n/* " + MARK + ": P430/P431/P432 periodic task sampler removed. */\n"
    return text


def patch_msm(text: str) -> str:
    if MARK in text:
        return text

    stub = r'''static void a52_r269_record_uapi(unsigned int n, unsigned int cmd,
				 unsigned long arg, long rc)
{
	/* Phase434: verbose Phase269 enumeration is deliberately retired. */
	(void)n;
	(void)cmd;
	(void)arg;
	(void)rc;
}
'''
    text = replace_function(text, "a52_r269_record_uapi", stub)
    text = text.replace(
        "static atomic_t a52_r269_event_sequence = ATOMIC_INIT(0);\n",
        "",
    )
    text = re.sub(
        r"#define A52_R269_EVENT_LIMIT 3072U\n",
        "",
        text,
        count=1,
    )
    text = re.sub(
        r"#define A52_R269_REC\(fmt, \.\.\.\) do \{ \\\n"
        r"\tif \(\(unsigned int\)atomic_inc_return\(&a52_r269_event_sequence\) <= A52_R269_EVENT_LIMIT\) \\\n"
        r"\t\ta52_ackfr_record\(\"P269 \" fmt, ##__VA_ARGS__\); \\\n"
        r"\} while \(0\)\n",
        "",
        text,
        count=1,
    )
    anchor = "static long a52_r211_drm_ioctl(struct file *filp, unsigned int cmd,"
    pos = text.find(anchor)
    if pos < 0:
        die("MSM ioctl wrapper anchor missing")
    text = text[:pos] + P434_MSM_BLOCK + "\n" + text[pos:]

    text = one(
        text,
        "\trc = drm_ioctl(filp, cmd, arg);\n"
        "\tif (a52_p418_atomic)\n"
        "\t\ta52_p418_atomic_post(a52_p418_n, a52_p418_flags,\n"
        "\t\t\ta52_p418_log, rc);\n",
        "\trc = drm_ioctl(filp, cmd, arg);\n"
        "\tif (a52_p418_atomic)\n"
        "\t\ta52_p418_atomic_post(a52_p418_n, a52_p418_flags,\n"
        "\t\t\ta52_p418_log, rc);\n"
        "\ta52_p434_drm_post(composer, cmd, arg, rc);\n",
        "native DRM compact post",
    )
    text = one(
        text,
        "\trc = drm_compat_ioctl(filp, cmd, arg);\n"
        "\tif (a52_p418_atomic)\n"
        "\t\ta52_p418_atomic_post(a52_p418_n, a52_p418_flags,\n"
        "\t\t\ta52_p418_log, rc);\n",
        "\trc = drm_compat_ioctl(filp, cmd, arg);\n"
        "\tif (a52_p418_atomic)\n"
        "\t\ta52_p418_atomic_post(a52_p418_n, a52_p418_flags,\n"
        "\t\t\ta52_p418_log, rc);\n"
        "\ta52_p434_drm_post(composer, cmd, arg, rc);\n",
        "compat DRM compact post",
    )
    text += "\n/* " + MARK + ": compact composer UAPI only. */\n"
    return text


def patch_exec(text: str) -> str:
    if MARK in text:
        return text
    if "void __set_task_comm(struct task_struct *tsk, const char *buf, bool exec)" not in text:
        die("exec __set_task_comm anchor missing")
    text = one(
        text,
        "\tperf_event_comm(tsk, exec);\n",
        "\tperf_event_comm(tsk, exec);\n"
        "\tif (exec)\n"
        "\t\ta52_p434_note_exec_comm(buf, true);\n",
        "exec event hook",
    )
    anchor = "char *__get_task_comm("
    pos = text.find(anchor)
    if pos < 0:
        die("exec extern insertion anchor missing")
    text = text[:pos] + (
        "/* A52_PHASE434_CLEAN_F0_V1 */\n"
        "extern void a52_p434_note_exec_comm(const char *name, bool exec);\n\n"
    ) + text[pos:]
    text += "\n/* " + MARK + ": SurfaceFlinger/bootanimation exec events. */\n"
    return text


def patch_exit(text: str) -> str:
    if MARK in text:
        return text
    old = (
        "\tgroup_dead = atomic_dec_and_test(&tsk->signal->live);\n"
        "\t/* A52_PHASE432_CTRL_ODSIGN_KEYMINT_QSEE_V1: only the final thread records the process exit. */\n"
    )
    new = (
        "\tgroup_dead = atomic_dec_and_test(&tsk->signal->live);\n"
        "\tif (group_dead && !strcmp(tsk->comm, \"bootanimation\")) {\n"
        "\t\tlong a52_p434_x = (tsk->signal->flags & SIGNAL_GROUP_EXIT) ?\n"
        "\t\t\ttsk->signal->group_exit_code : code;\n"
        "\t\ta52_ackfr_record(\"P434 BA EXIT p=%d t=%d x=%lx sig=%u pf=%u ns=%llu\",\n"
        "\t\t\ttsk->pid, tsk->tgid, a52_p434_x,\n"
        "\t\t\t(unsigned int)(a52_p434_x & 0x7f),\n"
        "\t\t\t!!(tsk->flags & PF_SIGNALED),\n"
        "\t\t\t(unsigned long long)ktime_get_boottime_ns());\n"
        "\t}\n"
        "\t/* A52_PHASE432_CTRL_ODSIGN_KEYMINT_QSEE_V1: only the final thread records the process exit. */\n"
    )
    text = one(text, old, new, "bootanimation exit")
    text += "\n/* " + MARK + ": bootanimation group exit fate. */\n"
    return text


def patch_signal(text: str) -> str:
    if MARK in text:
        return text

    anchor = "bool get_signal(struct ksignal *ksig)\n"
    if anchor not in text:
        die("get_signal function anchor missing")

    helper = r'''/* A52_PHASE434_CLEAN_F0_V1: receiving-side bootanimation signal fate. */
static void a52_p434_note_ba_signal(int signr, struct ksignal *ksig)
{
	struct task_struct *sender = NULL;
	pid_t spid, stgid;
	char scomm[TASK_COMM_LEN] = "?";

	if (strcmp(current->comm, "bootanimation") || !ksig || signr <= 0)
		return;

	spid = ksig->info.si_pid;
	stgid = spid;
	if (spid > 0) {
		rcu_read_lock();
		sender = find_task_by_vpid(spid);
		if (sender)
			get_task_struct(sender);
		rcu_read_unlock();
		if (sender) {
			stgid = task_tgid_nr(sender);
			get_task_comm(scomm, sender);
			put_task_struct(sender);
		}
	}

	a52_ackfr_record(
		"P434 BA SIG p=%d t=%d s=%d code=%d sp=%d st=%d sc=%.15s",
		current->pid, current->tgid, signr, ksig->info.si_code,
		spid, stgid, scomm);
}

'''
    text = text.replace(anchor, helper + anchor, 1)

    # Fatal default-action signals never reach the normal get_signal return.
    fatal = '''fatal:
		spin_unlock_irq(&sighand->siglock);
		if (unlikely(cgroup_task_frozen(current)))
'''
    fatal_new = '''fatal:
		spin_unlock_irq(&sighand->siglock);
		a52_p434_note_ba_signal(signr, ksig);
		if (unlikely(cgroup_task_frozen(current)))
'''
    text = one(text, fatal, fatal_new, "fatal receive-side signal hook")

    # Handled signals reach the common out path.  By this point siglock is
    # dropped, so sender task lookup cannot perturb signal lock ordering.
    out = '''out:
	ksig->sig = signr;

	if (!(ksig->ka.sa.sa_flags & SA_EXPOSE_TAGBITS))
'''
    out_new = '''out:
	ksig->sig = signr;
	if (ksig->sig > 0)
		a52_p434_note_ba_signal(ksig->sig, ksig);

	if (!(ksig->ka.sa.sa_flags & SA_EXPOSE_TAGBITS))
'''
    text = one(text, out, out_new, "handled receive-side signal hook")

    anchor_inc = "#include <linux/signal.h>"
    if anchor_inc in text:
        text = text.replace(
            anchor_inc,
            anchor_inc + "\n#include <linux/a52_ack_secure_flight_recorder.h>",
            1,
        )
    else:
        text = "extern void a52_ackfr_record(const char *fmt, ...);\n" + text
    text += "\n/* " + MARK + ": bootanimation receive-side signal records. */\n"
    return text


def patch_coredump(text: str) -> str:
    if MARK in text:
        return text
    a, b = function_span(text, "do_coredump")
    fn = text[a:b]
    block = '''
\tif (!strcmp(current->comm, "bootanimation"))
\t\ta52_ackfr_record("P434 BA CORE_REQ p=%d t=%d sig=%d code=%d",
\t\t\tcurrent->pid, current->tgid,
\t\t\tsiginfo ? siginfo->si_signo : 0,
\t\t\tsiginfo ? siginfo->si_code : 0);
'''
    # Keep declarations before statements for the kernel's GNU89 build.
    stmt = "\taudit_core_dumps(siginfo->si_signo);\n"
    if stmt not in fn:
        die("do_coredump first-statement anchor missing")
    fn = fn.replace(stmt, block + "\n" + stmt, 1)
    text = text[:a] + fn + text[b:]
    anchor = "#include <linux/coredump.h>"
    if anchor in text:
        text = text.replace(
            anchor,
            anchor + "\n#include <linux/a52_ack_secure_flight_recorder.h>",
            1,
        )
    else:
        text = "extern void a52_ackfr_record(const char *fmt, ...);\n" + text
    text += "\n/* " + MARK + ": bootanimation core-request event. */\n"
    return text

def patch_regulator_core(text: str) -> str:
    if MARK in text:
        return text

    # Event helper declarations; helper filters to the six named display rails.
    anchor = "static DEFINE_MUTEX(regulator_list_mutex);"
    if anchor not in text:
        # Stable fallback across Android-common regulator-core revisions.
        anchor = "static LIST_HEAD(regulator_list);"
    if anchor not in text:
        die("regulator core global anchor missing")
    text = text.replace(
        anchor,
        "/* A52_PHASE434_CLEAN_F0_V1 */\n"
        "extern void a52_ackfr_record(const char *fmt, ...);\n"
        "extern void a52_p434_rail_event(const char *name, int enabled, int uv, int users);\n\n"
        + anchor,
        1,
    )

    # Record every regulator late-cleanup disable by name and update cached rail
    # state.  No extra regulator operation is issued.
    a, b = function_span(text, "regulator_late_cleanup")
    fn = text[a:b]
    target = "\t\tret = _regulator_do_disable(rdev);\n"
    if target not in fn:
        die("regulator_late_cleanup disable assignment anchor missing")
    fn = fn.replace(
        target,
        '\t\ta52_ackfr_record("P434 RLC off n=%.31s", rdev_get_name(rdev));\n'
        '\t\ta52_p434_rail_event(rdev_get_name(rdev), 0, -1, rdev->use_count);\n'
        + target,
        1,
    )
    text = text[:a] + fn + text[b:]

    # Cache rail state only when the normal regulator core itself changes it.
    # These hooks are after the hardware operation has completed successfully;
    # they do not issue a second enable/disable/set-voltage transaction.
    a, b = function_span(text, "_regulator_do_enable")
    fn = text[a:b]
    tail = "\ttrace_regulator_enable_complete(rdev_get_name(rdev));\n\n\treturn 0;\n"
    tail_new = (
        "\ttrace_regulator_enable_complete(rdev_get_name(rdev));\n"
        "\ta52_p434_rail_event(rdev_get_name(rdev), 1,\n"
        "\t\tregulator_get_voltage_rdev(rdev), rdev->use_count + 1);\n\n"
        "\treturn 0;\n"
    )
    if tail not in fn:
        die("_regulator_do_enable completion anchor missing")
    fn = fn.replace(tail, tail_new, 1)
    text = text[:a] + fn + text[b:]

    a, b = function_span(text, "_regulator_do_disable")
    fn = text[a:b]
    tail = "\ttrace_regulator_disable_complete(rdev_get_name(rdev));\n\n\treturn 0;\n"
    tail_new = (
        "\ttrace_regulator_disable_complete(rdev_get_name(rdev));\n"
        "\ta52_p434_rail_event(rdev_get_name(rdev), 0,\n"
        "\t\tregulator_get_voltage_rdev(rdev),\n"
        "\t\trdev->use_count > 0 ? rdev->use_count - 1 : 0);\n\n"
        "\treturn 0;\n"
    )
    if tail not in fn:
        die("_regulator_do_disable completion anchor missing")
    fn = fn.replace(tail, tail_new, 1)
    text = text[:a] + fn + text[b:]

    a, b = function_span(text, "_regulator_do_set_voltage")
    fn = text[a:b]
    tail = (
        "out:\n"
        "\ttrace_regulator_set_voltage_complete(rdev_get_name(rdev), best_val);\n\n"
        "\treturn ret;\n"
    )
    tail_new = (
        "out:\n"
        "\ttrace_regulator_set_voltage_complete(rdev_get_name(rdev), best_val);\n"
        "\tif (!ret)\n"
        "\t\ta52_p434_rail_event(rdev_get_name(rdev), -1,\n"
        "\t\t\tregulator_get_voltage_rdev(rdev), rdev->use_count);\n\n"
        "\treturn ret;\n"
    )
    if tail not in fn:
        die("_regulator_do_set_voltage completion anchor missing")
    fn = fn.replace(tail, tail_new, 1)
    text = text[:a] + fn + text[b:]

    text += "\n/* " + MARK + ": regulator cleanup + cached display-rail transitions. */\n"
    return text


def patch_namei(text: str) -> str:
    if MARK in text:
        return text

    def wrap_known(src: str, name: str, rettype: str) -> str:
        a, b = function_span(src, name)
        fn = src[a:b]
        sig_end = fn.find("{")
        sig = fn[:sig_end].rstrip()
        if "struct filename *name" not in sig or "int dfd" not in sig:
            die(name + " signature drift")
        inner = name + "_a52_p434_inner"
        inner_fn = fn.replace(name + "(", inner + "(", 1)
        wrapper = (
            sig + "\n{\n"
            "\tchar a52_p434_path[160] = { 0 };\n"
            f"\t{rettype} a52_p434_rc;\n"
            "\tbool a52_p434_art = !strcmp(current->comm, \"odsign\") ||\n"
            "\t\t!strcmp(current->comm, \"odrefresh\");\n"
            "\tif (a52_p434_art && name && name->name)\n"
            "\t\tstrscpy(a52_p434_path, name->name, sizeof(a52_p434_path));\n"
            f"\ta52_p434_rc = {inner}(dfd, name);\n"
            "\tif (a52_p434_art && strstr(a52_p434_path,\n"
            "\t\t\"/data/misc/apexdata/com.android.art\"))\n"
            f"\t\ta52_ackfr_record(\"P434 ART {name} rc=%ld p=%.63s\","
            "\n\t\t\t(long)a52_p434_rc, a52_p434_path);\n"
            "\treturn a52_p434_rc;\n"
            "}\n"
        )
        return src[:a] + inner_fn + "\n" + wrapper + src[b:]

    text = wrap_known(text, "do_unlinkat", "long")
    text = wrap_known(text, "do_rmdir", "int")
    anchor = "#include <linux/namei.h>"
    if anchor in text:
        text = text.replace(
            anchor,
            anchor + "\n#include <linux/a52_ack_secure_flight_recorder.h>",
            1,
        )
    else:
        text = "extern void a52_ackfr_record(const char *fmt, ...);\n" + text
    text += "\n/* " + MARK + ": odsign/odrefresh ART deletion return codes. */\n"
    return text


def patch_verity_one(text: str, name: str) -> str:
    if MARK in text:
        return text

    a, b = function_span(text, name)
    fn = text[a:b]
    sig_end = fn.find("{")
    sig = fn[:sig_end].rstrip()
    if not re.search(r"\bint\s+" + re.escape(name) + r"\s*\(", sig):
        die(name + " signature drift")

    # Derive the original argument identifiers from the stable 5.10 signatures,
    # rather than assuming const/void annotation details.
    params = sig[sig.find("(") + 1:sig.rfind(")")]
    names = []
    for param in params.split(","):
        param = param.strip()
        ident = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", param)
        if not ident:
            die(name + " parameter parse failed: " + param)
        names.append(ident.group(1))

    inner = name + "_a52_p434_inner"
    inner_fn = fn.replace(name + "(", inner + "(", 1)
    wrapper = (
        sig + "\n{\n"
        "\tint a52_p434_rc;\n"
        f"\ta52_p434_rc = {inner}(" + ", ".join(names) + ");\n"
        "\tif (!strcmp(current->comm, \"odsign\") ||\n"
        "\t    !strcmp(current->comm, \"odrefresh\"))\n"
        f"\t\ta52_ackfr_record(\"P434 ART {name} rc=%d\", a52_p434_rc);\n"
        "\treturn a52_p434_rc;\n"
        "}\n"
    )
    text = text[:a] + inner_fn + "\n" + wrapper + text[b:]

    anchor = '#include "fsverity_private.h"'
    if anchor in text:
        text = text.replace(
            anchor,
            anchor + "\n#include <linux/a52_ack_secure_flight_recorder.h>",
            1,
        )
    else:
        text = "extern void a52_ackfr_record(const char *fmt, ...);\n" + text
    text += "\n/* " + MARK + f": {name} return code for ART gate. */\n"
    return text


def patch_dsi_hw(text: str) -> str:
    if MARK in text:
        return text

    # Retire timer-driven register polling.  Keep eleven slots so the exact F0
    # can be sampled at pre/post, +1/+5/+20/+100 us, IRQ entry, DMA_DONE,
    # wait-return, completion and final/timeout without any background timer.
    text = one(text,
               "#define A52_P430_DMA_SAMPLES       7U\n",
               "#define A52_P430_DMA_SAMPLES       11U\n",
               "DMA event sample capacity")
    text = remove_function(text, "a52_p430_dma_timer_fn")
    text = text.replace("static struct hrtimer a52_p430_dma_timer;\n", "")
    text = text.replace("static atomic_t a52_p430_timer_index = ATOMIC_INIT(0);\n", "")
    text = text.replace("\tatomic_set(&a52_p430_timer_index, 0);\n", "")
    text = re.sub(
        r"\n\thrtimer_start\(&a52_p430_dma_timer,.*?HRTIMER_MODE_REL_PINNED\);\n",
        "\n", text, count=1, flags=re.S
    )
    text = text.replace("\thrtimer_cancel(&a52_p430_dma_timer);\n", "")
    text = re.sub(
        r"\n\thrtimer_init\(&a52_p430_dma_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL_PINNED\);\n"
        r"\ta52_p430_dma_timer\.function = a52_p430_dma_timer_fn;\n",
        "\n", text, count=1
    )

    # Additional analog/PHY/INTF maps.  They are read only from the active F0
    # command path, never from a timer or regulator cleanup callback.
    anchor = "#define A52_P424_RSC_WRP_PHYS 0x0af30000ULL\n"
    text = one(
        text, anchor,
        anchor +
        "#define A52_P434_PHYPLL_PHYS  0x0ae94000ULL\n"
        "#define A52_P434_INTF1_PHYS   0x0ae6b000ULL\n",
        "analog map defines",
    )
    anchor = "static void __iomem *a52_p424_rsc_wrp;\n"
    text = one(
        text, anchor,
        anchor +
        "static void __iomem *a52_p434_phypll;\n"
        "static void __iomem *a52_p434_intf1;\n",
        "analog map globals",
    )
    anchor = "\ta52_p424_rsc_wrp = ioremap(A52_P424_RSC_WRP_PHYS, 0x100);\n"
    text = one(
        text, anchor,
        anchor +
        "\ta52_p434_phypll = ioremap(A52_P434_PHYPLL_PHYS, 0x1000);\n"
        "\ta52_p434_intf1 = ioremap(A52_P434_INTF1_PHYS, 0x1000);\n",
        "analog ioremap",
    )

    # Expand each event sample with the two missing DSI core registers and the
    # remaining analog state Claude/TouchGrass comparison could not measure.
    anchor = "\tu32 err_mask0;\n} __packed;\n"
    text = one(
        text, anchor,
        "\tu32 err_mask0;\n"
        "\tu32 ctrl;\n"
        "\tu32 lane_ctrl;\n"
        "\tu32 pll_common_status_one;\n"
        "\tu32 phy_pll_cntrl;\n"
        "\tu32 phy_ctrl0;\n"
        "\tu32 phy_rbuf_ctrl;\n"
        "\tu32 phy_clk_cfg1;\n"
        "\tu32 intf1_frame_count;\n"
        "\tu32 intf1_te_count;\n"
        "} __packed;\n",
        "DMA sample expansion",
    )
    anchor = "\ts->err_mask0 = DSI_R32(ctrl, DSI_ERR_INT_MASK0);\n"
    text = one(
        text, anchor,
        anchor +
        "\ts->ctrl = DSI_R32(ctrl, DSI_CTRL);\n"
        "\ts->lane_ctrl = DSI_R32(ctrl, DSI_LANE_CTRL);\n"
        "\ts->pll_common_status_one = a52_p424_r(a52_p434_phypll, 0xBA0);\n"
        "\ts->phy_pll_cntrl = a52_p424_r(a52_p434_phypll, 0x438);\n"
        "\ts->phy_ctrl0 = a52_p424_r(a52_p434_phypll, 0x424);\n"
        "\ts->phy_rbuf_ctrl = a52_p424_r(a52_p434_phypll, 0x41C);\n"
        "\ts->phy_clk_cfg1 = a52_p424_r(a52_p434_phypll, 0x414);\n"
        "\ts->intf1_frame_count = a52_p424_r(a52_p434_intf1, 0x8AC);\n"
        "\ts->intf1_te_count = a52_p424_r(a52_p434_intf1, 0xA98);\n",
        "DMA analog sample",
    )

    # Rail cache is printed only at the actual F0 pre-trigger arm.
    anchor = "extern bool a52_p430_bad_iova_active(void);\n"
    text = one(
        text, anchor,
        anchor + "extern void a52_p434_dump_rails(const char *tag);\n",
        "rail dump extern",
    )
    anchor = "\ta52_p430_dma_sample(ctrl, 0U);\n"
    text = one(
        text, anchor,
        "\ta52_p434_dump_rails(\"pre\");\n" + anchor,
        "pre-trigger cached rails",
    )

    # Exact-F0 bounded microsecond samples replace the old hrtimer.  Total
    # deliberate delay is 100 us and only occurs on the already-identified
    # failing target command.
    triggered = r'''void a52_p430_dma_triggered(struct dsi_ctrl_hw *ctrl)
{
	if (!atomic_read(&a52_p430_dma_armed) ||
	    ctrl != READ_ONCE(a52_p430_dma_ctrl))
		return;
	a52_p430_dma_sample(ctrl, 1U);
	udelay(1);
	a52_p430_dma_sample(ctrl, 2U);
	udelay(4);
	a52_p430_dma_sample(ctrl, 3U);
	udelay(15);
	a52_p430_dma_sample(ctrl, 4U);
	udelay(80);
	a52_p430_dma_sample(ctrl, 5U);
}
'''
    text = replace_function(text, "a52_p430_dma_triggered", triggered)

    # Event-only sampler callable from IRQ/wait paths.
    trig_a, trig_b = function_span(text, "a52_p430_dma_triggered")
    insert_at = trig_b
    event_fn = r'''
void a52_p430_dma_event(struct dsi_ctrl_hw *ctrl, u32 point)
{
	if (!atomic_read(&a52_p430_dma_armed) ||
	    ctrl != READ_ONCE(a52_p430_dma_ctrl))
		return;
	a52_p430_dma_sample(ctrl, point);
}
EXPORT_SYMBOL_GPL(a52_p430_dma_event);
'''
    text = text[:insert_at] + "\n" + event_fn + text[insert_at:]

    text = one(text,
               "\ta52_p430_dma_sample(ctrl, 6U);\n",
               "\ta52_p430_dma_sample(ctrl, 10U);\n",
               "final sample point")
    text += "\n/* " + MARK + ": timer polling retired; active-path F0 state only. */\n"
    return text


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text

    anchor = (
        "extern void a52_p430_dma_finalize(struct dsi_ctrl_hw *ctrl, u64 iova,\n"
        "\t\tu32 irq_trig, int wait_ret);\n"
    )
    text = one(
        text, anchor,
        anchor + "extern void a52_p430_dma_event(struct dsi_ctrl_hw *ctrl, u32 point);\n",
        "DMA event extern",
    )

    # ISR entry after status/error reads; DMA_DONE just before completion; wait
    # return after completion wait.  All are naturally occurring events.
    anchor = (
        "\tif (a52_p293_gdm_armed(dsi_ctrl))\n"
        "\t\ta52_ackfr_record(\"P276 387I irq=%d st=%x raw=%x err=%llx\",\n"
    )
    text = one(
        text, anchor,
        "\tif (a52_p421_target_active())\n"
        "\t\ta52_p430_dma_event(&dsi_ctrl->hw, 6U);\n" + anchor,
        "IRQ-entry sample",
    )

    anchor = (
        "\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n"
        "\t\tif (a52_p421_target_active())\n"
    )
    text = one(
        text, anchor,
        "\t\tatomic_set(&dsi_ctrl->dma_irq_trig, 1);\n"
        "\t\tif (a52_p421_target_active())\n"
        "\t\t\ta52_p430_dma_event(&dsi_ctrl->hw, 7U);\n"
        "\t\tif (a52_p421_target_active())\n",
        "DMA-done sample",
    )

    anchor = (
        "\tret = wait_for_completion_timeout(\n"
        "\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n"
        "\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n"
    )
    text = one(
        text, anchor,
        anchor +
        "\tif (a52_p421_target_active())\n"
        "\t\ta52_p430_dma_event(&dsi_ctrl->hw, 8U);\n",
        "wait-return sample",
    )

    anchor = "\t\tcomplete_all(&dsi_ctrl->irq_info.cmd_dma_done);\n"
    text = one(
        text, anchor,
        anchor +
        "\t\tif (a52_p421_target_active())\n"
        "\t\t\ta52_p430_dma_event(&dsi_ctrl->hw, 9U);\n",
        "completion sample",
    )
    text += "\n/* " + MARK + ": event-driven F0 IRQ/completion/wait chronology. */\n"
    return text


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text
    crit = 'return !strncmp(message, "P432 ", 5) ||'
    if crit in text and 'return !strncmp(message, "P434 ", 5) ||' not in text:
        text = text.replace(
            crit,
            'return !strncmp(message, "P434 ", 5) ||\n'
            '\t       !strncmp(message, "P432 ", 5) ||',
            1,
        )
    if 'strncmp(fmt, "P434", 4) &&' not in text:
        anchor = 'strncmp(fmt, "P433", 4) &&'
        n = text.count(anchor)
        if n < 3:
            die(f"recorder P433 admission anchors missing: {n}")
        text = text.replace(
            anchor,
            'strncmp(fmt, "P434", 4) &&\n    ' + anchor,
        )
    text += "\n/* " + MARK + ": P434 event records admitted. */\n"
    return text


def apply(root: Path) -> None:
    files = {
        SYSCALL: patch_syscall,
        MSM: patch_msm,
        DSI: patch_dsi,
        DSI_HW: patch_dsi_hw,
        EXEC: patch_exec,
        EXIT: patch_exit,
        SIGNAL: patch_signal,
        COREDUMP: patch_coredump,
        REGCORE: patch_regulator_core,
        NAMEI: patch_namei,
        VERITY_ENABLE: lambda text: patch_verity_one(text, "fsverity_ioctl_enable"),
        VERITY_MEASURE: lambda text: patch_verity_one(text, "fsverity_ioctl_measure"),
        REC: patch_recorder,
    }
    for rel, fn in files.items():
        p = root / rel
        if not p.is_file():
            die("source missing: " + str(rel))
        p.write_text(fn(p.read_text(errors="replace")))

    hp = root / HELPER
    hp.write_text(HELPER_C)
    mp = root / A52_MAKE
    ms = mp.read_text()
    line = "obj-y += a52_phase434_clean.o"
    if line not in ms:
        mp.write_text(ms.rstrip() + "\n# " + MARK + "\n" + line + "\n")


def validate(root: Path) -> None:
    syscall = (root / SYSCALL).read_text(errors="replace")
    msm = (root / MSM).read_text(errors="replace")
    dsi = (root / DSI).read_text(errors="replace")
    hw = (root / DSI_HW).read_text(errors="replace")
    rec = (root / REC).read_text(errors="replace")
    helper = (root / HELPER).read_text(errors="replace")
    signal = (root / SIGNAL).read_text(errors="replace")
    namei = (root / NAMEI).read_text(errors="replace")
    verity_enable = (root / VERITY_ENABLE).read_text(errors="replace")
    verity_measure = (root / VERITY_MEASURE).read_text(errors="replace")

    for bad in (
        "a52_p430_snapshot(",
        "a52_p430_fill(",
        "a52_p430_sampler_fn(",
        "get_wchan(",
        "stack_trace_save_tsk(",
        "try_get_task_stack(",
        "task_pt_regs(",
        'kthread_run(a52_p430_sampler_fn',
        'kthread_run(a52_r377_sampler_fn',
        'kthread_run(a52_r380_vdc_sampler_fn',
        'kthread_run(a52_r373_sampler_fn',
    ):
        if bad in syscall:
            die("intrusive/periodic sampler token remains: " + bad)

    if "a52_p430_ufs_compact(snapshot_id);" in syscall:
        die("active UFS sampler call reappeared")
    if "static unsigned int\n\n" in syscall:
        die("orphan split return type remains after sampler removal")
    if "P434 ART KEY sc=%d op=%llx rc=%ld" not in syscall:
        die("ART add_key/keyctl return trace missing")

    if 'A52_R269_REC("PROP ' in msm or 'A52_R269_REC("PVAL ' in msm:
        # The strings may remain only if the whole old function was not replaced.
        die("verbose P269 property dump remains")
    if "a52_r269_event_sequence" in msm or "#define A52_R269_REC" in msm:
        die("retired P269 event macro remains")
    for token in (
        "P434 IO n=%u nr=%x rc=%ld",
        "P434 PROP n=%u obj=%u id=%u v=%llx",
        "a52_p434_sf_window_active",
    ):
        if token not in msm:
            die("MSM compact trace missing: " + token)

    for token in (
        "P434 BA SIG",
        "find_task_by_vpid",
    ):
        if token not in signal:
            die("bootanimation signal hook missing: " + token)
    for token in (
        "P434 ART do_unlinkat",
        "P434 ART do_rmdir",
    ):
        if token not in namei:
            die("ART deletion hook missing: " + token)
    for token in (
        "P434 ART fsverity_ioctl_enable",
        "P434 ART fsverity_ioctl_measure",
    ):
        if token not in verity_enable + "\n" + verity_measure:
            die("ART fs-verity hook missing: " + token)

    if "hrtimer_start(&a52_p430_dma_timer" in hw or "a52_p430_dma_timer_fn" in hw:
        die("timer-driven DMA sampler remains")
    if hw.count("EXPORT_SYMBOL_GPL(a52_p430_dma_triggered);") != 1:
        die("DMA-triggered export count mismatch")
    for token in (
        "a52_p430_dma_event",
        "pll_common_status_one",
        "intf1_frame_count",
        "a52_p434_dump_rails(\"pre\")",
    ):
        if token not in hw:
            die("F0 clean sampler missing: " + token)
    for token in (
        "a52_p430_dma_event(&dsi_ctrl->hw, 6U)",
        "a52_p430_dma_event(&dsi_ctrl->hw, 7U)",
        "a52_p430_dma_event(&dsi_ctrl->hw, 8U)",
        "a52_p430_dma_event(&dsi_ctrl->hw, 9U)",
    ):
        if token not in dsi:
            die("event-driven DSI hook missing: " + token)

    if 'strncmp(fmt, "P434", 4)' not in rec:
        die("P434 recorder admission missing")
    if '||\n\t       return !strncmp(message, "P432 ", 5)' in rec:
        die("malformed P434 critical admission")
    for token in (
        MARK,
        "P434 SF EXEC",
        "P434 BA EXEC",
        "P434 RAIL",
        "P434 BOOT",
    ):
        if token not in helper:
            die("helper missing: " + token)

    print("Phase434 CLEAN-F0: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root.resolve()
    if not ns.check_only:
        apply(root)
    validate(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
