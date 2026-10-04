#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE440_EARLY_F0_CLEANUP_FORENSICS_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
DISP = Path("drivers/a52_display/msm/dsi/dsi_display.c")
EXIT = Path("kernel/exit.c")
SYSCALL = Path("arch/arm64/kernel/syscall.c")
CORE = Path("drivers/base/core.c")
RUNTIME = Path("drivers/base/power/runtime.c")
DOMAIN = Path("drivers/base/power/domain.c")
REG = Path("drivers/regulator/core.c")


def die(msg: str) -> None:
    raise SystemExit("Phase440: " + msg)


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


def add_include_and_extern(text: str, header: str = "") -> str:
    if MARK in text:
        return text
    first = text.find("#include ")
    if first < 0:
        die("include anchor missing")
    eol = text.find("\n", first)
    add = ""
    if header and header not in text:
        add += header + "\n"
    add += "extern void a52_p440_event(const char *kind, const char *name, long a, long b);\n"
    return text[:eol + 1] + add + text[eol + 1:]


REC_HELPER = r'''
/* A52_PHASE440_EARLY_F0_CLEANUP_FORENSICS_V1
 *
 * Small bounded passive event stream for the early-vs-late F0 experiment.
 * The broad 1..80 s window intentionally includes bootanimation death and
 * late cleanup. Runtime-PM/genpd events are name-filtered to avoid flooding
 * the persistent recorder.
 */
static atomic_t a52_p440_event_count = ATOMIC_INIT(0);

static bool a52_p440_power_name(const char *name)
{
	static const char * const keys[] = {
		"dsi", "mdss", "sde", "disp", "mdp", "gpu", "kgsl",
		"smmu", "tbu", "mnoc", "noc", "icc", "interconnect",
		"gdsc", "cx", "mx"
	};
	unsigned int i;

	if (!name || !*name)
		return false;
	for (i = 0; i < ARRAY_SIZE(keys); i++)
		if (strstr(name, keys[i]))
			return true;
	return false;
}

void a52_p440_event(const char *kind, const char *name, long a, long b)
{
	u64 ns = ktime_get_boottime_ns();
	u64 ms = div_u64(ns, NSEC_PER_MSEC);
	unsigned int n;

	if (ms < 1000ULL || ms > 80000ULL)
		return;

	/* Runtime power can be extremely chatty. Keep only display-adjacent names. */
	if ((!strncmp(kind, "RPM_", 4) || !strncmp(kind, "GENPD_", 6)) &&
	    !a52_p440_power_name(name))
		return;

	n = (unsigned int)atomic_inc_return(&a52_p440_event_count);
	if (n > 512U)
		return;

	a52_ackfr_record("P440 EV n=%u ms=%llu k=%.15s d=%.31s a=%ld b=%ld",
			 n, (unsigned long long)ms,
			 kind ? kind : "-", name ? name : "-", a, b);
}
EXPORT_SYMBOL_GPL(a52_p440_event);

static const char a52_p440_marker[] __used =
	"A52_PHASE440_EARLY_F0_CLEANUP_FORENSICS_V1";
'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text

    text = one(
        text,
        'return !strncmp(message, "P439 ", 5) ||',
        'return !strncmp(message, "P440 ", 5) ||\n'
        '       !strncmp(message, "P439 ", 5) ||',
        "recorder critical admission",
    )
    anchor = 'strncmp(fmt, "P439", 4) &&'
    n = text.count(anchor)
    if n < 1:
        die("recorder format admission: P439 gate missing")
    text = text.replace(
        anchor,
        'strncmp(fmt, "P440", 4) &&\n\t    ' + anchor,
    )
    return text + "\n" + REC_HELPER


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE439_DSI_AUTOPSY_V3" not in text:
        die("P439 HWC prerequisite missing")

    old = '''void a52_p439_allow_next(void)
{
\t/*
\t * Variant B calls this only after the early F0 unlock and matching
\t * relock have both returned.  Keep the first epoch's samples, but re-arm
\t * the hot-path state so the later Android F0 becomes epoch 2.
\t */
\tatomic_set(&a52_p439_post_pending, 0);
\tatomic_set(&a52_p439_active, 0);
}
'''
    new = '''void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl);

void a52_p439_allow_next(void)
{
\t/*
\t * Phase440 persists the successful early epoch before re-arming. This is
\t * what makes PASS->PASS observable even if no timeout ever flushes P439.
\t */
\tif (atomic_read(&a52_p439_active) == 1)
\t\ta52_p439_flush_samples(NULL);
\ta52_ackfr_record("P440 EARLY rearm count=%d epoch=%d",
\t\t\t atomic_read(&a52_p439_count),
\t\t\t atomic_read(&a52_p439_epoch));
\tatomic_set(&a52_p439_post_pending, 0);
\tatomic_set(&a52_p439_active, 0);
}
'''
    text = one(text, old, new, "P439 rearm/early flush")
    return text + "\n/* " + MARK + ": successful early P439 epoch persisted before rearm. */\n"


DISP_HELPER = r'''
/* A52_PHASE440_EARLY_F0_CLEANUP_FORENSICS_V1 */
extern void a52_p439_allow_next(void);
extern void a52_p439_flush_samples(struct dsi_ctrl_hw *ctrl);
extern void a52_ackfr_record(const char *fmt, ...);

#define A52_P440_EARLY_NS (12ULL * NSEC_PER_SEC)
#define A52_P440_FLUSH_NS (75ULL * NSEC_PER_SEC)

static struct dsi_display *a52_p440_primary;
static struct delayed_work a52_p440_early_work;
static struct delayed_work a52_p440_flush_work;
static atomic_t a52_p440_scheduled = ATOMIC_INIT(0);

static unsigned long a52_p440_delay_to(u64 target)
{
	u64 now = ktime_get_boottime_ns();

	if (now >= target)
		return 0;
	return nsecs_to_jiffies(target - now);
}

static void a52_p440_flush_workfn(struct work_struct *work)
{
	a52_ackfr_record("P440 FLUSH ms=%llu",
			 (unsigned long long)div_u64(ktime_get_boottime_ns(),
						    NSEC_PER_MSEC));
	/* Flush any successful late epoch. Timeout boots flush earlier themselves. */
	a52_p439_flush_samples(NULL);
}

static void a52_p440_early_workfn(struct work_struct *work)
{
	static const u8 unlock[] = { 0xF0, 0x5A, 0x5A };
	static const u8 relock[] = { 0xF0, 0xA5, 0xA5 };
	struct dsi_display *display = READ_ONCE(a52_p440_primary);
	struct mipi_dsi_msg msg = { 0 };
	u64 ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
	int rc;

	if (!display)
		return;

	a52_ackfr_record("P440 EARLY start ms=%llu", (unsigned long long)ms);

	msg.channel = 0;
	msg.type = MIPI_DSI_GENERIC_LONG_WRITE;
	msg.flags = MIPI_DSI_MSG_LASTCOMMAND;
	msg.tx_len = ARRAY_SIZE(unlock);
	msg.tx_buf = unlock;

	rc = (int)dsi_host_transfer(&display->host, &msg);
	a52_ackfr_record("P440 EARLY unlock_rc=%d ms=%llu", rc,
			 (unsigned long long)div_u64(ktime_get_boottime_ns(),
						    NSEC_PER_MSEC));
	if (rc) {
		/* A synchronous rejection is evidence too. Keep booting for late F0. */
		a52_p439_allow_next();
		return;
	}

	msg.tx_len = ARRAY_SIZE(relock);
	msg.tx_buf = relock;
	rc = (int)dsi_host_transfer(&display->host, &msg);
	a52_ackfr_record("P440 EARLY relock_rc=%d ms=%llu", rc,
			 (unsigned long long)div_u64(ktime_get_boottime_ns(),
						    NSEC_PER_MSEC));

	/* Persist early P439 samples and arm the natural Android F0 as epoch 2. */
	a52_p439_allow_next();
}

static void a52_p440_schedule(struct dsi_display *display)
{
	if (!display || strcmp(display->display_type, "primary"))
		return;
	if (atomic_cmpxchg(&a52_p440_scheduled, 0, 1) != 0)
		return;

	WRITE_ONCE(a52_p440_primary, display);
	INIT_DELAYED_WORK(&a52_p440_early_work, a52_p440_early_workfn);
	INIT_DELAYED_WORK(&a52_p440_flush_work, a52_p440_flush_workfn);

	a52_ackfr_record("P440 ARM ms=%llu early=12000 flush=75000",
			 (unsigned long long)div_u64(ktime_get_boottime_ns(),
						    NSEC_PER_MSEC));
	schedule_delayed_work(&a52_p440_early_work,
			      a52_p440_delay_to(A52_P440_EARLY_NS));
	schedule_delayed_work(&a52_p440_flush_work,
			      a52_p440_delay_to(A52_P440_FLUSH_NS));
}
'''


def patch_disp(text: str) -> str:
    if MARK in text:
        return text

    first = text.find("#include ")
    if first < 0:
        die("display include anchor missing")
    eol = text.find("\n", first)
    includes = (
        "#include <linux/workqueue.h>\n"
        "#include <linux/ktime.h>\n"
        "#include <linux/jiffies.h>\n"
    )
    text = text[:eol + 1] + includes + text[eol + 1:]

    anchor = '''#if defined(CONFIG_DISPLAY_SAMSUNG)
extern bool pba_regulator_control_ss;
#endif
'''
    text = one(text, anchor, anchor + DISP_HELPER + "\n", "display helper")

    old = '''\tdsi_config_host_engine_state_for_cont_splash(display);
\tmutex_unlock(&display->display_lock);

\t/* Set the current brightness level */
'''
    new = '''\tdsi_config_host_engine_state_for_cont_splash(display);
\tmutex_unlock(&display->display_lock);

\t/* Phase440: schedule one absolute-boottime early F0, leave natural late F0 untouched. */
\ta52_p440_schedule(display);

\t/* Set the current brightness level */
'''
    text = one(text, old, new, "continuous-splash scheduling hook")
    return text + "\n/* " + MARK + ": absolute ~12 s early F0 + 75 s PASS/PASS flush. */\n"


def patch_exit(text: str) -> str:
    if MARK in text:
        return text
    text = add_include_and_extern(text)

    start = text.find("void __noreturn do_exit(long code)")
    if start < 0:
        start = text.find("void __noreturn do_exit(long code)")
    if start < 0:
        die("do_exit missing")
    pos = text.find("int group_dead;", start)
    if pos < 0:
        die("do_exit local anchor missing")
    pos = text.find("\n", pos) + 1
    ins = '''\tif (!strncmp(current->comm, "bootanimation", 13) ||
\t    (current->group_leader &&
\t     !strncmp(current->group_leader->comm, "bootanimation", 13)))
\t\ta52_p440_event("BA_DO_EXIT", current->comm, code, current->pid);
'''
    text = text[:pos] + ins + text[pos:]

    start = text.find("do_group_exit(int exit_code)")
    if start < 0:
        die("do_group_exit missing")
    pos = text.find("struct signal_struct *sig = current->signal;", start)
    if pos < 0:
        die("do_group_exit local anchor missing")
    pos = text.find("\n", pos) + 1
    ins = '''\tif (!strncmp(current->comm, "bootanimation", 13) ||
\t    (current->group_leader &&
\t     !strncmp(current->group_leader->comm, "bootanimation", 13)))
\t\ta52_p440_event("BA_GROUP_EXIT", current->comm,
\t\t\t exit_code, current->pid);
'''
    text = text[:pos] + ins + text[pos:]
    return text + "\n/* " + MARK + ": bootanimation exit codes captured. */\n"


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    text = add_include_and_extern(text)

    start = text.find("static void invoke_syscall(")
    if start < 0:
        die("invoke_syscall missing")
    end = text.find("\nstatic ", start + 20)
    if end < 0:
        end = len(text)
    body = text[start:end]

    anchors = [
        "\tregs->regs[0] = ret;\n",
        "\tsyscall_set_return_value(current, regs, 0, ret);\n",
    ]
    anchor = next((x for x in anchors if x in body), None)
    if not anchor:
        die("invoke_syscall return anchor missing")
    log = '''\tif (ret < 0 &&
\t    (!strncmp(current->comm, "bootanimation", 13) ||
\t     (current->group_leader &&
\t      !strncmp(current->group_leader->comm, "bootanimation", 13))))
\t\ta52_p440_event("BA_SYSCALL", current->comm, scno, ret);
'''
    body = body.replace(anchor, log + anchor, 1)
    text = text[:start] + body + text[end:]
    return text + "\n/* " + MARK + ": failed bootanimation syscalls captured. */\n"


def patch_core(text: str) -> str:
    if MARK in text:
        return text
    text = add_include_and_extern(text)

    old = '''\t\tif (dev->bus->sync_state)
\t\t\tdev->bus->sync_state(dev);
\t\telse if (dev->driver && dev->driver->sync_state)
\t\t\tdev->driver->sync_state(dev);
'''
    new = '''\t\ta52_p440_event("SYNC_PRE", dev_name(dev), 0, 0);
\t\tif (dev->bus->sync_state)
\t\t\tdev->bus->sync_state(dev);
\t\telse if (dev->driver && dev->driver->sync_state)
\t\t\tdev->driver->sync_state(dev);
\t\ta52_p440_event("SYNC_POST", dev_name(dev), 0, 0);
'''
    text = one(text, old, new, "sync_state callback")
    return text + "\n/* " + MARK + ": sync_state chronology captured. */\n"


def patch_runtime(text: str) -> str:
    if MARK in text:
        return text
    text = add_include_and_extern(text)

    text = one(
        text,
        "\t__update_runtime_status(dev, RPM_SUSPENDED);\n",
        "\t__update_runtime_status(dev, RPM_SUSPENDED);\n"
        '\ta52_p440_event("RPM_SUSP", dev_name(dev), rpmflags, 0);\n',
        "runtime suspended transition",
    )
    text = one(
        text,
        "\t__update_runtime_status(dev, RPM_ACTIVE);\n",
        "\t__update_runtime_status(dev, RPM_ACTIVE);\n"
        '\ta52_p440_event("RPM_RESUME", dev_name(dev), rpmflags, 0);\n',
        "runtime active transition",
    )
    return text + "\n/* " + MARK + ": relevant runtime-PM transitions captured. */\n"


def patch_domain(text: str) -> str:
    if MARK in text:
        return text
    text = add_include_and_extern(text)

    old = '''\tgenpd->status = GENPD_STATE_OFF;
\tgenpd_update_accounting(genpd);
\tgenpd->states[genpd->state_idx].usage++;
'''
    new = '''\tgenpd->status = GENPD_STATE_OFF;
\tgenpd_update_accounting(genpd);
\ta52_p440_event("GENPD_OFF", genpd->name, genpd->state_idx, 0);
\tgenpd->states[genpd->state_idx].usage++;
'''
    text = one(text, old, new, "runtime genpd off")

    old = '''\tgenpd->status = GENPD_STATE_ON;
\tgenpd_update_accounting(genpd);
'''
    new = '''\tgenpd->status = GENPD_STATE_ON;
\tgenpd_update_accounting(genpd);
\ta52_p440_event("GENPD_ON", genpd->name, genpd->state_idx, 0);
'''
    text = one(text, old, new, "genpd on")
    return text + "\n/* " + MARK + ": genpd runtime on/off chronology captured. */\n"


def patch_reg(text: str) -> str:
    if MARK in text:
        return text
    text = add_include_and_extern(text)

    start = text.find("static int regulator_late_cleanup(")
    if start < 0:
        die("regulator_late_cleanup missing")
    end = text.find("\nstatic ", start + 20)
    if end < 0:
        die("regulator_late_cleanup end missing")
    body = text[start:end]
    old = "\t\tret = _regulator_do_disable(rdev);\n"
    if old not in body:
        die("regulator disable anchor missing")
    new = old + '\t\ta52_p440_event("REG_LATE", rdev_get_name(rdev), ret, rdev->use_count);\n'
    body = body.replace(old, new, 1)
    text = text[:start] + body + text[end:]
    return text + "\n/* " + MARK + ": actual late regulator-disable attempts captured. */\n"


def validate(root: Path) -> None:
    checks = {
        REC: ["P440 EV n=%u", "a52_p440_event_count", "EXPORT_SYMBOL_GPL(a52_p440_event)"],
        HWC: ["P440 EARLY rearm", "a52_p439_flush_samples(NULL)"],
        DISP: ["A52_P440_EARLY_NS", "P440 EARLY start", "a52_p440_schedule(display)", "A52_P440_FLUSH_NS"],
        EXIT: ["BA_DO_EXIT", "BA_GROUP_EXIT"],
        SYSCALL: ["BA_SYSCALL"],
        CORE: ["SYNC_PRE", "SYNC_POST"],
        RUNTIME: ["RPM_SUSP", "RPM_RESUME"],
        DOMAIN: ["GENPD_OFF", "GENPD_ON"],
        REG: ["REG_LATE", "rdev_get_name(rdev)"],
    }
    for rel, toks in checks.items():
        p = root / rel
        if not p.is_file():
            die("missing " + str(rel))
        s = p.read_text(errors="replace")
        for tok in toks:
            if tok not in s:
                die(f"{rel}: missing token {tok}")
    print("Phase440 early-F0 + cleanup forensics: PASS")


def apply(root: Path) -> None:
    patches = (
        (REC, patch_rec),
        (HWC, patch_hwc),
        (DISP, patch_disp),
        (EXIT, patch_exit),
        (SYSCALL, patch_syscall),
        (CORE, patch_core),
        (RUNTIME, patch_runtime),
        (DOMAIN, patch_domain),
        (REG, patch_reg),
    )
    for rel, fn in patches:
        p = root / rel
        if not p.is_file():
            die("missing " + str(rel))
        p.write_text(fn(p.read_text(errors="replace")))


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
