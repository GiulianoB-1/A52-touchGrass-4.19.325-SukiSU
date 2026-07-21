#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/final-link-closure-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before final link repair"

python3 - "$KERNEL_DIR" "$TOUCHGRASS_COMMIT" "$REPORT" <<'PY'
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1])
touchgrass = sys.argv[2]
report = Path(sys.argv[3])
repairs = []


def git_blob(commit: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        text=True,
    )


def write_if_changed(path: Path, text: str, label: str) -> None:
    if path.read_text() != text:
        path.write_text(text)
        repairs.append(label)


def ensure_after(path: Path, anchor: str, addition: str, label: str) -> None:
    text = path.read_text()
    if addition in text:
        if text.count(addition) != 1:
            raise SystemExit(f"{label}: duplicate addition count={text.count(addition)}")
        return
    if text.count(anchor) != 1:
        raise SystemExit(f"{label}: anchor count={text.count(anchor)}")
    path.write_text(text.replace(anchor, anchor + addition, 1))
    repairs.append(label)


def ensure_before(path: Path, anchor: str, addition: str, label: str) -> None:
    text = path.read_text()
    if addition in text:
        if text.count(addition) != 1:
            raise SystemExit(f"{label}: duplicate addition count={text.count(addition)}")
        return
    if text.count(anchor) != 1:
        raise SystemExit(f"{label}: anchor count={text.count(anchor)}")
    path.write_text(text.replace(anchor, addition + anchor, 1))
    repairs.append(label)


def region(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start) + len(end_marker)
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[start:end]


def extract_function(text: str, marker: str) -> str:
    start = text.index(marker)
    brace = text.index("{", start)
    depth = 0
    i = brace
    state = "normal"
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "normal":
            if c == "/" and n == "*":
                state = "block_comment"
                i += 2
                continue
            if c == "/" and n == "/":
                state = "line_comment"
                i += 2
                continue
            if c == '"':
                state = "string"
            elif c == "'":
                state = "char"
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        elif state == "block_comment":
            if c == "*" and n == "/":
                state = "normal"
                i += 2
                continue
        elif state == "line_comment":
            if c == "\n":
                state = "normal"
        elif state in ("string", "char"):
            if c == "\\":
                i += 2
                continue
            if (state == "string" and c == '"') or (state == "char" and c == "'"):
                state = "normal"
        i += 1
    else:
        raise SystemExit(f"unterminated function for marker {marker!r}")

    # Preserve immediately following kernel export declarations.
    tail = text[end:]
    match = re.match(r"(?:\n|\r\n)+(?:EXPORT_SYMBOL(?:_GPL)?\([^\n]+\);(?:\n|\r\n)+)*", tail)
    if match:
        export_tail = match.group(0)
        if "EXPORT_SYMBOL" in export_tail:
            end += len(export_tail)
        elif end < len(text) and text[end] == "\n":
            end += 1
    return text[start:end]


# ---------------------------------------------------------------------------
# Build wiring lost during the three-way merge.
# ---------------------------------------------------------------------------
init_make = root / "init/Makefile"
ensure_after(
    init_make,
    "mounts-$(CONFIG_BLK_DEV_MD)\t+= do_mounts_md.o\n",
    "mounts-$(CONFIG_BLK_DEV_DM)\t+= do_mounts_dm.o\n",
    "init=restored-dm-init-object",
)
if not (root / "init/do_mounts_dm.c").is_file():
    raise SystemExit("init/do_mounts_dm.c is missing")

proc_make = root / "fs/proc/Makefile"
ensure_after(
    proc_make,
    "proc-y\t+= thread_self.o\n",
    "proc-$(CONFIG_PROC_FSLOG)\t+= fslog.o\n",
    "procfs=restored-fslog-object",
)
ensure_after(
    proc_make,
    "proc-$(CONFIG_PROC_PAGE_MONITOR)\t+= page.o\n",
    "proc-$(CONFIG_PROC_AVC)\t+= proc_avc.o\n",
    "procfs=restored-sec-avc-object",
)
for rel in ("fs/proc/fslog.c", "fs/proc/proc_avc.c"):
    if not (root / rel).is_file():
        raise SystemExit(f"required procfs source is missing: {rel}")

ext4_make = root / "fs/ext4/Makefile"
verity_line = "ext4-$(CONFIG_FS_VERITY)\t\t\t+= verity.o\n"
if verity_line not in ext4_make.read_text():
    text = ext4_make.read_text()
    if not text.endswith("\n"):
        text += "\n"
    ext4_make.write_text(text + verity_line)
    repairs.append("ext4=restored-fsverity-object")
if not (root / "fs/ext4/verity.c").is_file():
    raise SystemExit("fs/ext4/verity.c is missing")

lib_make = root / "lib/Makefile"
chacha_line = "lib-$(CONFIG_CRYPTO_CHACHA20) += chacha.o\n"
if chacha_line not in lib_make.read_text():
    text = lib_make.read_text()
    if not text.endswith("\n"):
        text += "\n"
    lib_make.write_text(text + chacha_line)
    repairs.append("lib=restored-generic-chacha-object")
if not (root / "lib/chacha.c").is_file():
    raise SystemExit("lib/chacha.c is missing")


# ---------------------------------------------------------------------------
# Restore Samsung's RTC boot parameter definition used by rtc-pm8xxx.
# ---------------------------------------------------------------------------
main_c = root / "init/main.c"
main_text = main_c.read_text()
sapa_block = '''#ifdef CONFIG_RTC_AUTO_PWRON_PARAM
unsigned int sapa_param_time;
EXPORT_SYMBOL(sapa_param_time);

static int __init read_sapa_param(char *str)
{
\tint temp = 0;

\tif (get_option(&str, &temp)) {
\t\tsapa_param_time = (unsigned int)temp;
\t\tpr_info("sapa: %s param_time:%u\\n",
\t\t\t__func__, sapa_param_time);
\t\treturn 0;
\t}

\treturn -EINVAL;
}

early_param("sapa", read_sapa_param);
#endif

'''
if "unsigned int sapa_param_time;" not in main_text:
    anchor = 'early_param("loglevel", loglevel);\n\n'
    if main_text.count(anchor) != 1:
        raise SystemExit("sapa parameter insertion anchor mismatch")
    main_text = main_text.replace(anchor, anchor + sapa_block, 1)
    main_c.write_text(main_text)
    repairs.append("init=restored-sapa-boot-parameter")
elif main_text.count("unsigned int sapa_param_time;") != 1:
    raise SystemExit("unexpected sapa_param_time definition count")


# ---------------------------------------------------------------------------
# Timer compatibility. Keep stable's per-CPU deferrable timer model, so remove
# the stale global-deferrable check from tick-sched. Restore Samsung's hotplug
# migration block because scheduler CPU isolation still invokes timer_quiesce.
# ---------------------------------------------------------------------------
tick_sched = root / "kernel/time/tick-sched.c"
tick_text = tick_sched.read_text()
stale_deferrable = '''#ifdef CONFIG_SMP
\tif (check_pending_deferrable_timers(cpu))
\t\traise_softirq_irqoff(TIMER_SOFTIRQ);
#endif

'''
if stale_deferrable in tick_text:
    if tick_text.count(stale_deferrable) != 1:
        raise SystemExit("unexpected stale deferrable timer block count")
    tick_sched.write_text(tick_text.replace(stale_deferrable, "", 1))
    repairs.append("timer=removed-stale-global-deferrable-check")

vendor_timer = git_blob(touchgrass, "kernel/time/timer.c")
timer_c = root / "kernel/time/timer.c"
timer_text = timer_c.read_text()
hotplug_start = "#ifdef CONFIG_HOTPLUG_CPU\nstatic void migrate_timer_list"
hotplug_end = "#endif /* CONFIG_HOTPLUG_CPU */"
vendor_hotplug = region(vendor_timer, hotplug_start, hotplug_end)
current_hotplug = region(timer_text, hotplug_start, hotplug_end)
if current_hotplug != vendor_hotplug:
    timer_text = timer_text.replace(current_hotplug, vendor_hotplug, 1)
    timer_c.write_text(timer_text)
    repairs.append("timer=restored-samsung-isolation-migration")


# ---------------------------------------------------------------------------
# MMC compatibility. Restore only the vendor implementation blocks consumed by
# the retained Samsung SD/CQE/isolation paths; preserve stable's other core code
# and the merged cache_enabled callback.
# ---------------------------------------------------------------------------
core_h = root / "drivers/mmc/core/core.h"
core_h_text = core_h.read_text()
if "struct mmc_queue;\n" not in core_h_text:
    anchor = "struct mmc_request;\n"
    if core_h_text.count(anchor) != 1:
        raise SystemExit("MMC queue forward-declaration anchor mismatch")
    core_h_text = core_h_text.replace(anchor, anchor + "struct mmc_queue;\n", 1)
    repairs.append("mmc=restored-queue-forward-declaration")

stlog_block = '''#ifdef CONFIG_MMC_SUPPORT_STLOG
#include <linux/fslog.h>
#else
#define ST_LOG(fmt, ...)
#endif

'''
if "#ifdef CONFIG_MMC_SUPPORT_STLOG\n" not in core_h_text:
    anchor = "#define MMC_CMD_RETRIES        3\n\n"
    if core_h_text.count(anchor) != 1:
        raise SystemExit("MMC ST_LOG insertion anchor mismatch")
    core_h_text = core_h_text.replace(anchor, anchor + stlog_block, 1)
    repairs.append("mmc=restored-storage-log-interface")

clock_decls = '''extern bool mmc_can_scale_clk(struct mmc_host *host);
extern int mmc_init_clk_scaling(struct mmc_host *host);
extern int mmc_suspend_clk_scaling(struct mmc_host *host);
extern int mmc_resume_clk_scaling(struct mmc_host *host);
extern int mmc_exit_clk_scaling(struct mmc_host *host);
extern void mmc_deferred_scaling(struct mmc_host *host);
extern void mmc_cqe_clk_scaling_start_busy(struct mmc_queue *mq,
\tstruct mmc_host *host, bool lock_needed);
extern void mmc_cqe_clk_scaling_stop_busy(struct mmc_host *host,
\t\t\tbool lock_needed, bool is_cqe_dcmd);

extern unsigned long mmc_get_max_frequency(struct mmc_host *host);

'''
if "extern int mmc_init_clk_scaling(struct mmc_host *host);" not in core_h_text:
    anchor = "void mmc_remove_card_debugfs(struct mmc_card *card);\n\n"
    if core_h_text.count(anchor) != 1:
        raise SystemExit("MMC clock declaration anchor mismatch")
    core_h_text = core_h_text.replace(anchor, anchor + clock_decls, 1)
    repairs.append("mmc=restored-clock-scaling-declarations")

gate_decls = '''#ifndef CONFIG_MMC_CLKGATE
void mmc_gate_clock(struct mmc_host *host);
void mmc_ungate_clock(struct mmc_host *host);
#endif

'''
if "void mmc_gate_clock(struct mmc_host *host);" not in core_h_text:
    anchor = "int mmc_hs400_to_hs200(struct mmc_card *card);\n\n"
    if core_h_text.count(anchor) != 1:
        raise SystemExit("MMC clock-gate declaration anchor mismatch")
    core_h_text = core_h_text.replace(anchor, anchor + gate_decls, 1)
    repairs.append("mmc=restored-clock-gate-declarations")

try_decl = '''int __mmc_try_claim_host(struct mmc_host *host, struct mmc_ctx *ctx,
\t\t         unsigned int delay);
'''
if "int __mmc_try_claim_host(" not in core_h_text:
    anchor = "int __mmc_claim_host(struct mmc_host *host, struct mmc_ctx *ctx,\n\t\t     atomic_t *abort);\n"
    if core_h_text.count(anchor) != 1:
        raise SystemExit("MMC try-claim declaration anchor mismatch")
    core_h_text = core_h_text.replace(anchor, anchor + try_decl, 1)
    repairs.append("mmc=restored-try-claim-declaration")

try_inline = '''/**
 *\tmmc_try_claim_host - try exclusively to claim a host
 *         and keep trying for given time, with a gap of 10ms
 *\t@host: mmc host to claim
 *\t@delay_ms: delay in ms
 *
 *\tReturns %1 if the host is claimed, %0 otherwise.
 */
static inline int mmc_try_claim_host(struct mmc_host *host, unsigned int delay_ms)
{
\treturn __mmc_try_claim_host(host, NULL, delay_ms);
}

'''
if "static inline int mmc_try_claim_host(" not in core_h_text:
    anchor = '''static inline void mmc_claim_host(struct mmc_host *host)
{
\t__mmc_claim_host(host, NULL, NULL);
}

'''
    if core_h_text.count(anchor) != 1:
        raise SystemExit("MMC try-claim inline anchor mismatch")
    core_h_text = core_h_text.replace(anchor, anchor + try_inline, 1)
    repairs.append("mmc=restored-try-claim-inline")
core_h.write_text(core_h_text)

vendor_core = git_blob(touchgrass, "drivers/mmc/core/core.c")
core_c = root / "drivers/mmc/core/core.c"
core_text = core_c.read_text()
if "#include <linux/devfreq.h>\n" not in core_text:
    anchor = "#include <linux/completion.h>\n"
    if core_text.count(anchor) != 1:
        raise SystemExit("MMC devfreq include anchor mismatch")
    core_text = core_text.replace(anchor, anchor + "#include <linux/devfreq.h>\n", 1)
    repairs.append("mmc=restored-devfreq-include")
if '#include "queue.h"\n' not in core_text:
    anchor = '#include "host.h"\n'
    if core_text.count(anchor) != 1:
        raise SystemExit("MMC queue include anchor mismatch")
    core_text = core_text.replace(anchor, anchor + '#include "queue.h"\n', 1)
    repairs.append("mmc=restored-queue-include")

clock_start = "static bool mmc_is_data_request(struct mmc_request *mmc_request)\n"
clock_end = "EXPORT_SYMBOL(mmc_exit_clk_scaling);"
vendor_clock = region(vendor_core, clock_start, clock_end)
if clock_start not in core_text:
    anchor = "static inline void mmc_complete_cmd(struct mmc_request *mrq)\n"
    if core_text.count(anchor) != 1:
        raise SystemExit("MMC clock implementation insertion anchor mismatch")
    core_text = core_text.replace(anchor, vendor_clock + "\n" + anchor, 1)
    repairs.append("mmc=restored-clock-scaling-implementation")

extra_functions = (
    ("int __mmc_try_claim_host(", "__mmc_try_claim_host"),
    ("int mmc_resume_bus(struct mmc_host *host)\n", "mmc_resume_bus"),
    ("void mmc_flush_detect_work(struct mmc_host *host)\n", "mmc_flush_detect_work"),
)
extra_blocks = []
for marker, name in extra_functions:
    if marker not in core_text:
        extra_blocks.append(extract_function(vendor_core, marker))
        repairs.append(f"mmc=restored-{name}")

if "void mmc_gate_clock(struct mmc_host *host)\n" not in core_text:
    gate_block = extract_function(vendor_core, "void mmc_gate_clock(struct mmc_host *host)\n")
    ungate_block = extract_function(vendor_core, "void mmc_ungate_clock(struct mmc_host *host)\n")
    extra_blocks.append("#ifndef CONFIG_MMC_CLKGATE\n" + gate_block + ungate_block + "#endif\n")
    repairs.append("mmc=restored-clock-gate-implementation")

if extra_blocks:
    anchor = "void mmc_rescan(struct work_struct *work)\n"
    if core_text.count(anchor) != 1:
        raise SystemExit("MMC auxiliary implementation insertion anchor mismatch")
    core_text = core_text.replace(anchor, "\n".join(extra_blocks) + "\n" + anchor, 1)
core_c.write_text(core_text)

vendor_sdhci = git_blob(touchgrass, "drivers/mmc/host/sdhci.c")
sdhci = root / "drivers/mmc/host/sdhci.c"
sdhci_text = sdhci.read_text()
sdhci_marker = "void sdhci_cfg_irq(struct sdhci_host *host, bool enable, bool sync)\n"
if sdhci_marker not in sdhci_text:
    block = extract_function(vendor_sdhci, sdhci_marker)
    anchor = "void sdhci_set_ios(struct mmc_host *mmc, struct mmc_ios *ios)\n"
    if sdhci_text.count(anchor) != 1:
        raise SystemExit("SDHCI IRQ implementation insertion anchor mismatch")
    sdhci.write_text(sdhci_text.replace(anchor, block + "\n" + anchor, 1))
    repairs.append("sdhci=restored-vendor-irq-control")


# ---------------------------------------------------------------------------
# Exact postconditions.
# ---------------------------------------------------------------------------
checks = {
    "init/Makefile": ("mounts-$(CONFIG_BLK_DEV_DM)\t+= do_mounts_dm.o\n",),
    "fs/proc/Makefile": (
        "proc-$(CONFIG_PROC_FSLOG)\t+= fslog.o\n",
        "proc-$(CONFIG_PROC_AVC)\t+= proc_avc.o\n",
    ),
    "fs/ext4/Makefile": (verity_line,),
    "lib/Makefile": (chacha_line,),
    "init/main.c": ("unsigned int sapa_param_time;",),
    "kernel/time/timer.c": ("void timer_quiesce_cpu(void *cpup)\n",),
    "drivers/mmc/core/core.c": (
        "int mmc_init_clk_scaling(struct mmc_host *host)\n",
        "int mmc_exit_clk_scaling(struct mmc_host *host)\n",
        "int __mmc_try_claim_host(",
        "int mmc_resume_bus(struct mmc_host *host)\n",
        "void mmc_flush_detect_work(struct mmc_host *host)\n",
        "void mmc_cqe_clk_scaling_start_busy(",
        "void mmc_cqe_clk_scaling_stop_busy(",
        "void mmc_deferred_scaling(struct mmc_host *host)\n",
        "void mmc_gate_clock(struct mmc_host *host)\n",
        "void mmc_ungate_clock(struct mmc_host *host)\n",
    ),
    "drivers/mmc/host/sdhci.c": (sdhci_marker,),
}
for rel, needles in checks.items():
    text = (root / rel).read_text()
    for needle in needles:
        if text.count(needle) != 1:
            raise SystemExit(f"postcondition failed: {rel}: {needle!r}: count={text.count(needle)}")

if "check_pending_deferrable_timers(cpu)" in tick_sched.read_text():
    raise SystemExit("stale deferrable timer caller remains")
if "static inline int mmc_try_claim_host(" not in core_h.read_text():
    raise SystemExit("MMC try-claim inline helper is missing")
if "#ifdef CONFIG_MMC_SUPPORT_STLOG" not in core_h.read_text():
    raise SystemExit("MMC storage-log mapping is missing")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  init/Makefile init/main.c \
  fs/proc/Makefile fs/ext4/Makefile lib/Makefile \
  kernel/time/timer.c kernel/time/tick-sched.c \
  drivers/mmc/core/core.h drivers/mmc/core/core.c \
  drivers/mmc/host/sdhci.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'build_wiring=dm-proc-avc-fslog-ext4-verity-chacha\n'
  printf 'timer=samsung-isolation-plus-stable-deferrable-model\n'
  printf 'rtc=sapa-boot-parameter-restored\n'
  printf 'mmc=vendor-clock-scaling-isolation-and-sdhci-irq-restored\n'
  printf 'result=linux-4.19.325-final-link-closure-repaired\n'
} | tee -a "$REPORT"

info "Linux $TARGET_VERSION final link closure repaired"
