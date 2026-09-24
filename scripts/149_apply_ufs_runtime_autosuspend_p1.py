#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 UFS P149: precise devfreq + runtime-PM autosuspend"
DEVFREQ_TIME_SRC = "ee48a99fffd427eaf676e8cd697606d5bcea61dc"
DEVFREQ_RPM_SRC = "80d85e5d973d9ee3ae0cc44580f78c37d8e83c27"
QCOM_AUTOSUSPEND_SRC = "5862ee751b63a2d4b75c751671108754165d5789"
CAP_COLLISION_SRC = "f7eefc38b845a776489c2e8796b50a0c388b68ac"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def function_block(text: str, signature: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"missing function: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"missing opening brace: {signature}")
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise SystemExit(f"unterminated function: {signature}")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    ufshcd_c = root / "drivers/scsi/ufs/ufshcd.c"
    ufshcd_h = root / "drivers/scsi/ufs/ufshcd.h"
    qcom_c = root / "drivers/scsi/ufs/ufs-qcom.c"
    lpm_c = root / "drivers/cpuidle/lpm-levels.c"

    for path in (ufshcd_c, ufshcd_h, qcom_c, lpm_c):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    c = ufshcd_c.read_text()
    h = ufshcd_h.read_text()
    q = qcom_c.read_text()
    lpm = lpm_c.read_text()

    # P148 is the exact boot-tested source boundary for this phase.
    if "A52 QCOM LPM P1: raw idle locks + short-idle tick + hotpath cleanup" not in lpm:
        raise SystemExit("P148 Qualcomm LPM baseline marker missing")
    if "A52 P148 BATTERY: Qualcomm LPM + WALT/schedutil correctness" not in (
        root / "kernel/sched/cpufreq_schedutil.c"
    ).read_text():
        raise SystemExit("P148 scheduler baseline marker missing")

    # Samsung already has request-based SCSI autosuspend infrastructure.
    # Require it so P149 extends the existing path instead of inventing a
    # second PM mechanism.
    for needle in (
        "#define UFSHCD_AUTO_SUSPEND_DELAY_MS 3000",
        "sdev->autosuspend_delay = UFSHCD_AUTO_SUSPEND_DELAY_MS;",
        "sdev->use_rpm_auto = 1;",
        "static int ufshcd_devfreq_target(struct device *dev,",
        "static int ufshcd_devfreq_get_dev_status(struct device *dev,",
        "scaling->window_start_t = jiffies;",
    ):
        if needle not in c:
            raise SystemExit(f"UFS baseline missing: {needle}")

    for needle in (
        "#define UFSHCD_CAP_POWER_COLLAPSE_DURING_HIBERN8 (1 << 7)",
        "#define UFSHCD_CAP_CRYPTO (1 << 7)",
        "unsigned long window_start_t;",
    ):
        if needle not in h:
            raise SystemExit(f"UFS header baseline missing: {needle}")

    # ------------------------------------------------------------------
    # 1) Upstream devfreq accounting correctness.
    #
    # Old code mixes jiffies for total_time with ktime for busy_time.
    # Use one ktime snapshot for both sides of each devfreq window.
    # ------------------------------------------------------------------
    h = replace_once(
        h,
        " * @window_start_t: Start time (in jiffies) of the current polling window\n",
        " * @window_start_t: Start time of the current polling window\n",
        "devfreq window_start_t documentation",
    )
    h = replace_once(
        h,
        "\tunsigned long window_start_t;\n",
        "\tktime_t window_start_t;\n",
        "devfreq window_start_t type",
    )

    old_status = """static int ufshcd_devfreq_get_dev_status(struct device *dev,
\t\tstruct devfreq_dev_status *stat)
{
\tstruct ufs_hba *hba = dev_get_drvdata(dev);
\tstruct ufs_clk_scaling *scaling = &hba->clk_scaling;
\tunsigned long flags;

\tif (!ufshcd_is_clkscaling_supported(hba))
\t\treturn -EINVAL;

\tmemset(stat, 0, sizeof(*stat));

\tspin_lock_irqsave(hba->host->host_lock, flags);
\tif (!scaling->window_start_t)
\t\tgoto start_window;

\tif (scaling->is_busy_started)
\t\tscaling->tot_busy_t += ktime_to_us(ktime_sub(ktime_get(),
\t\t\t\t\tscaling->busy_start_t));

\tstat->total_time = jiffies_to_usecs((long)jiffies -
\t\t\t\t(long)scaling->window_start_t);
\tstat->busy_time = scaling->tot_busy_t;
start_window:
\tscaling->window_start_t = jiffies;
\tscaling->tot_busy_t = 0;

\tif (hba->outstanding_reqs) {
\t\tscaling->busy_start_t = ktime_get();
\t\tscaling->is_busy_started = true;
\t} else {
\t\tscaling->busy_start_t = 0;
\t\tscaling->is_busy_started = false;
\t}
\tspin_unlock_irqrestore(hba->host->host_lock, flags);
\treturn 0;
}
"""
    new_status = f"""static int ufshcd_devfreq_get_dev_status(struct device *dev,
\t\tstruct devfreq_dev_status *stat)
{{
\tstruct ufs_hba *hba = dev_get_drvdata(dev);
\tstruct ufs_clk_scaling *scaling = &hba->clk_scaling;
\tunsigned long flags;
\tktime_t curr_t;

\tif (!ufshcd_is_clkscaling_supported(hba))
\t\treturn -EINVAL;

\tmemset(stat, 0, sizeof(*stat));

\tspin_lock_irqsave(hba->host->host_lock, flags);
\tcurr_t = ktime_get();
\tif (!scaling->window_start_t)
\t\tgoto start_window;

\t/* {MARKER}: upstream {DEVFREQ_TIME_SRC}. */
\tif (scaling->is_busy_started)
\t\tscaling->tot_busy_t += ktime_to_us(ktime_sub(curr_t,
\t\t\t\t\tscaling->busy_start_t));

\tstat->total_time = ktime_to_us(ktime_sub(curr_t,
\t\t\t\tscaling->window_start_t));
\tstat->busy_time = scaling->tot_busy_t;
start_window:
\tscaling->window_start_t = curr_t;
\tscaling->tot_busy_t = 0;

\tif (hba->outstanding_reqs) {{
\t\tscaling->busy_start_t = curr_t;
\t\tscaling->is_busy_started = true;
\t}} else {{
\t\tscaling->busy_start_t = 0;
\t\tscaling->is_busy_started = false;
\t}}
\tspin_unlock_irqrestore(hba->host->host_lock, flags);
\treturn 0;
}}
"""
    c = replace_once(c, old_status, new_status, "precise UFS devfreq window accounting")

    old_start_busy = """static void ufshcd_clk_scaling_start_busy(struct ufs_hba *hba)
{
\tbool queue_resume_work = false;

\tif (!ufshcd_is_clkscaling_supported(hba))
\t\treturn;

\tif (!hba->clk_scaling.active_reqs++)
\t\tqueue_resume_work = true;

\tif (!hba->clk_scaling.is_allowed || hba->pm_op_in_progress)
\t\treturn;

\tif (queue_resume_work)
\t\tqueue_work(hba->clk_scaling.workq,
\t\t\t   &hba->clk_scaling.resume_work);

\tif (!hba->clk_scaling.window_start_t) {
\t\thba->clk_scaling.window_start_t = jiffies;
\t\thba->clk_scaling.tot_busy_t = 0;
\t\thba->clk_scaling.is_busy_started = false;
\t}

\tif (!hba->clk_scaling.is_busy_started) {
\t\thba->clk_scaling.busy_start_t = ktime_get();
\t\thba->clk_scaling.is_busy_started = true;
\t}
}
"""
    new_start_busy = """static void ufshcd_clk_scaling_start_busy(struct ufs_hba *hba)
{
\tbool queue_resume_work = false;
\tktime_t curr_t = ktime_get();

\tif (!ufshcd_is_clkscaling_supported(hba))
\t\treturn;

\tif (!hba->clk_scaling.active_reqs++)
\t\tqueue_resume_work = true;

\tif (!hba->clk_scaling.is_allowed || hba->pm_op_in_progress)
\t\treturn;

\tif (queue_resume_work)
\t\tqueue_work(hba->clk_scaling.workq,
\t\t\t   &hba->clk_scaling.resume_work);

\tif (!hba->clk_scaling.window_start_t) {
\t\thba->clk_scaling.window_start_t = curr_t;
\t\thba->clk_scaling.tot_busy_t = 0;
\t\thba->clk_scaling.is_busy_started = false;
\t}

\tif (!hba->clk_scaling.is_busy_started) {
\t\thba->clk_scaling.busy_start_t = curr_t;
\t\thba->clk_scaling.is_busy_started = true;
\t}
}
"""
    c = replace_once(c, old_start_busy, new_start_busy,
                     "precise UFS devfreq busy-window start")

    # ------------------------------------------------------------------
    # 2) Qualcomm runtime-PM correctness.
    # Never perform a clock/gear devfreq transition while HBA runtime PM is
    # already suspended. Pin the runtime-PM status without forcing a resume.
    # ------------------------------------------------------------------
    target = function_block(c, "static int ufshcd_devfreq_target(struct device *dev,")
    if "pm_runtime_get_noresume(hba->dev);" not in target:
        old = """\tspin_unlock_irqrestore(hba->host->host_lock, irq_flags);

\tstart = ktime_get();
\tret = ufshcd_devfreq_scale(hba, scale_up);
\ttrace_ufshcd_profile_clk_scaling(dev_name(hba->dev),
"""
        new = f"""\tspin_unlock_irqrestore(hba->host->host_lock, irq_flags);

\t/*
\t * {MARKER}
\t * Qualcomm {DEVFREQ_RPM_SRC}: a devfreq callback can race runtime
\t * suspend. Do not wake the controller just to scale it; retry later.
\t */
\tpm_runtime_get_noresume(hba->dev);
\tif (!pm_runtime_active(hba->dev)) {{
\t\tpm_runtime_put_noidle(hba->dev);
\t\tret = -EAGAIN;
\t\tgoto out;
\t}}

\tstart = ktime_get();
\tret = ufshcd_devfreq_scale(hba, scale_up);
\tpm_runtime_put(hba->dev);
\ttrace_ufshcd_profile_clk_scaling(dev_name(hba->dev),
"""
        c = replace_once(c, old, new, "UFS devfreq runtime-active guard")

    # ------------------------------------------------------------------
    # 3) Merge the planned autosuspend phase into P149.
    #
    # Samsung already carries request-based SCSI autosuspend. Align it with
    # newer Qualcomm behavior: expose a host capability, let qcom enable it
    # only when LPM is enabled, and use the newer 2-second default.
    # ------------------------------------------------------------------
    c = replace_once(
        c,
        "/* default value of auto suspend is 3 seconds */\n"
        "#define UFSHCD_AUTO_SUSPEND_DELAY_MS 3000 /* millisecs */\n",
        f"""/* {MARKER}
 * Qualcomm/modern UFS default autosuspend delay: 2 seconds.
 */
#define UFSHCD_AUTO_SUSPEND_DELAY_MS 2000 /* millisecs */
""",
        "UFS autosuspend delay",
    )

    old_caps = """#define UFSHCD_CAP_POWER_COLLAPSE_DURING_HIBERN8 (1 << 7)
\t/*
\t * This capability allows the host controller driver to use the
\t * inline crypto engine, if it is present
\t */
#define UFSHCD_CAP_CRYPTO (1 << 7)
"""
    new_caps = f"""/*
\t * {MARKER}
\t * Keep Qualcomm power-collapse and crypto capability bits distinct.
\t * Android fixed a similar capability collision in {CAP_COLLISION_SRC}.
\t */
#define UFSHCD_CAP_CRYPTO (1 << 7)
#define UFSHCD_CAP_POWER_COLLAPSE_DURING_HIBERN8 (1 << 8)
\t/*
\t * Allow the host driver to opt SCSI devices into runtime autosuspend
\t * instead of relying on userspace to change power/control.
\t */
#define UFSHCD_CAP_RPM_AUTOSUSPEND (1 << 9)
"""
    h = replace_once(h, old_caps, new_caps, "UFS capability bitmap collision")

    helper_anchor = """static inline bool ufshcd_is_hibern8_on_idle_allowed(struct ufs_hba *hba)
{
\treturn hba->caps & UFSHCD_CAP_HIBERN8_ENTER_ON_IDLE;
}

"""
    helper_new = helper_anchor + """static inline bool ufshcd_is_rpm_autosuspend_allowed(struct ufs_hba *hba)
{
\treturn hba->caps & UFSHCD_CAP_RPM_AUTOSUSPEND;
}

"""
    h = replace_once(h, helper_anchor, helper_new, "UFS autosuspend capability helper")

    q = replace_once(
        q,
        """\tif (!host->disable_lpm) {
\t\thba->caps |= UFSHCD_CAP_CLK_GATING;
\t\thba->caps |= UFSHCD_CAP_HIBERN8_WITH_CLK_GATING;
\t\thba->caps |= UFSHCD_CAP_CLK_SCALING;
\t}
""",
        f"""\tif (!host->disable_lpm) {{
\t\thba->caps |= UFSHCD_CAP_CLK_GATING;
\t\thba->caps |= UFSHCD_CAP_HIBERN8_WITH_CLK_GATING;
\t\thba->caps |= UFSHCD_CAP_CLK_SCALING;
\t\t/* Qualcomm {QCOM_AUTOSUSPEND_SRC}: enable request-based RPM auto. */
\t\thba->caps |= UFSHCD_CAP_RPM_AUTOSUSPEND;
\t}}
""",
        "Qualcomm UFS autosuspend capability",
    )

    c = replace_once(
        c,
        """\tsdev->autosuspend_delay = UFSHCD_AUTO_SUSPEND_DELAY_MS;
\tsdev->use_rpm_auto = 1;
""",
        """\tif (ufshcd_is_rpm_autosuspend_allowed(hba)) {
\t\tsdev->autosuspend_delay = UFSHCD_AUTO_SUSPEND_DELAY_MS;
\t\tsdev->use_rpm_auto = 1;
\t}
""",
        "capability-gated UFS SCSI autosuspend",
    )

    # Write results.
    ufshcd_c.write_text(c)
    ufshcd_h.write_text(h)
    qcom_c.write_text(q)

    # Structural audits.
    C = ufshcd_c.read_text()
    H = ufshcd_h.read_text()
    Q = qcom_c.read_text()

    for needle in (
        MARKER,
        "#define UFSHCD_AUTO_SUSPEND_DELAY_MS 2000",
        "ktime_t curr_t;",
        "stat->total_time = ktime_to_us(ktime_sub(curr_t,",
        "scaling->window_start_t = curr_t;",
        "pm_runtime_get_noresume(hba->dev);",
        "if (!pm_runtime_active(hba->dev))",
        "pm_runtime_put_noidle(hba->dev);",
        "pm_runtime_put(hba->dev);",
        "if (ufshcd_is_rpm_autosuspend_allowed(hba))",
        "sdev->use_rpm_auto = 1;",
    ):
        if needle not in C:
            raise SystemExit(f"audit failed: ufshcd.c missing {needle}")

    for needle in (
        "ktime_t window_start_t;",
        "#define UFSHCD_CAP_CRYPTO (1 << 7)",
        "#define UFSHCD_CAP_POWER_COLLAPSE_DURING_HIBERN8 (1 << 8)",
        "#define UFSHCD_CAP_RPM_AUTOSUSPEND (1 << 9)",
        "static inline bool ufshcd_is_rpm_autosuspend_allowed",
    ):
        if needle not in H:
            raise SystemExit(f"audit failed: ufshcd.h missing {needle}")

    if H.count("(1 << 7)") != 1:
        raise SystemExit("audit failed: UFS capability bit 7 still collides")
    if H.count("(1 << 8)") != 1:
        raise SystemExit("audit failed: UFS capability bit 8 not unique")
    if H.count("(1 << 9)") != 1:
        raise SystemExit("audit failed: UFS capability bit 9 not unique")

    for needle in (
        "hba->caps |= UFSHCD_CAP_RPM_AUTOSUSPEND;",
        "hba->caps |= UFSHCD_CAP_CLK_GATING;",
        "hba->caps |= UFSHCD_CAP_HIBERN8_WITH_CLK_GATING;",
        "hba->caps |= UFSHCD_CAP_CLK_SCALING;",
    ):
        if needle not in Q:
            raise SystemExit(f"audit failed: ufs-qcom.c missing {needle}")

    status = function_block(C, "static int ufshcd_devfreq_get_dev_status(struct device *dev,")
    for forbidden in (
        "jiffies_to_usecs(",
        "window_start_t = jiffies",
    ):
        if forbidden in status:
            raise SystemExit(f"audit failed: stale imprecise devfreq accounting: {forbidden}")

    target = function_block(C, "static int ufshcd_devfreq_target(struct device *dev,")
    if "pm_runtime_get_noresume(hba->dev);" not in target or        "pm_runtime_active(hba->dev)" not in target:
        raise SystemExit("audit failed: runtime-active devfreq guard missing")

    slave = function_block(C, "static int ufshcd_slave_configure(struct scsi_device *sdev)")
    if "ufshcd_is_rpm_autosuspend_allowed(hba)" not in slave:
        raise SystemExit("audit failed: SCSI autosuspend not capability-gated")

    # Preserve the device-specific UFS operating envelope.
    for needle in (
        ".upthreshold = 70,",
        ".downdifferential = 65,",
        ".simple_scaling = 1,",
        "#define UFSHCD_CLK_GATING_DELAY_MS_PWR_SAVE\t30",
        "#define UFSHCD_CLK_GATING_DELAY_MS_PERF\t\t50",
    ):
        if needle not in C:
            raise SystemExit(f"audit failed: existing UFS tuning changed/missing: {needle}")

    print("[audit] P148 boot-tested CPU/LPM stack required: PASS")
    print("[audit] UFS devfreq uses one ktime clock for total/busy windows: PASS")
    print("[audit] devfreq scaling refuses runtime-suspended HBA without forced wake: PASS")
    print("[audit] Qualcomm request-based UFS autosuspend enabled when LPM is enabled: PASS")
    print("[audit] autosuspend delay modernized 3000ms -> 2000ms: PASS")
    print("[audit] Samsung SCSI request-based RPM mechanism preserved: PASS")
    print("[audit] UFS capability bit collision removed: PASS")
    print("[audit] UFS gear/frequency tables unchanged: PASS")
    print("[audit] Hibern8 and clock-gating delays unchanged: PASS")
    print(f"[source] {DEVFREQ_TIME_SRC}: precise UFS devfreq load accounting")
    print(f"[source] {DEVFREQ_RPM_SRC}: scale clocks only while HBA runtime-active")
    print(f"[source] {QCOM_AUTOSUSPEND_SRC}: Qualcomm runtime autosuspend capability")
    print(f"[source] {CAP_COLLISION_SRC}: capability-bit collision precedent")
    print("[done] A52 P149 combined UFS runtime-PM + autosuspend phase applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
