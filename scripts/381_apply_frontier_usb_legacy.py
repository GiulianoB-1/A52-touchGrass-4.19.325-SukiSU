#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE381_FRONTIER_USB_LEGACY_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
SYSCALL = Path("arch/arm64/kernel/syscall.c")
UFS = Path("drivers/scsi/ufs/ufshcd.c")
BLK = Path("block/blk-mq.c")
LOOP = Path("drivers/block/loop.c")
DWCQ = Path("drivers/usb/dwc3/dwc3-qcom.c")
DWCCORE = Path("drivers/usb/dwc3/core.c")
GSERIAL = Path("drivers/usb/gadget/legacy/serial.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase381 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_recorder(text: str) -> str:
    if "A52_PHASE381_RECORDER_ADMISSION_V1" in text:
        return text
    if "A52_PHASE380_LEGACY_PROBES_RETIRED_V1" not in text:
        raise SystemExit("Phase381 requires Phase380 recorder lineage")

    old = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
"""
    new = """\t/* A52_PHASE381_RECORDER_ADMISSION_V1
\t * Phase380 added B/L/U/USB records only to the critical classifier, but
\t * this earlier admission gate dropped them first. Admit Phase380/381
\t * diagnostics through both the normal and retained paths.
\t */
\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    strncmp(fmt, "U380", 4) && strncmp(fmt, "B380", 4) &&
\t    strncmp(fmt, "L380", 4) && strncmp(fmt, "USB380", 6) &&
\t    strncmp(fmt, "U381", 4) && strncmp(fmt, "B381", 4) &&
\t    strncmp(fmt, "L381", 4) && strncmp(fmt, "USB381", 6) &&
\t    !(fmt[0] == 'P' && fmt[1] == '2' && fmt[2] == '7' &&
"""
    text = one(text, old, new, "retained admission")

    text = one(
        text,
        'if (strncmp(fmt, "P276", 4) &&\n',
        """if (strncmp(fmt, "U380", 4) &&
    strncmp(fmt, "B380", 4) &&
    strncmp(fmt, "L380", 4) &&
    strncmp(fmt, "USB380", 6) &&
    strncmp(fmt, "U381", 4) &&
    strncmp(fmt, "B381", 4) &&
    strncmp(fmt, "L381", 4) &&
    strncmp(fmt, "USB381", 6) &&
    strncmp(fmt, "P276", 4) &&
""",
        "normal admission",
    )

    text = one(
        text,
        '       !strncmp(message, "V380 ", 5);\n',
        """       !strncmp(message, "V380 ", 5) ||
       !strncmp(message, "U381 ", 5) ||
       !strncmp(message, "B381 ", 5) ||
       !strncmp(message, "L381 ", 5) ||
       !strncmp(message, "USB381 ", 7);
""",
        "critical Phase381 prefixes",
    )
    return text


def patch_census(text: str) -> str:
    if "A52_PHASE381_DENSE_APEX_CENSUS_V1" in text:
        return text
    if "A52_PHASE377_APEXD_LIVE_THREAD_CENSUS_V1" not in text:
        raise SystemExit("Phase381 requires Phase377 census lineage")
    old = """static int a52_r377_sampler_fn(void *unused)
{
\tstatic const u32 seconds[A52_R377_SNAPSHOT_COUNT] = { 19U, 20U, 25U, 40U };
\tunsigned int next = 0;
\tu64 sec;

\twhile (!kthread_should_stop() && next < A52_R377_SNAPSHOT_COUNT) {
\t\tsec = div_u64(ktime_get_boottime_ns(), NSEC_PER_SEC);
\t\tif (sec >= seconds[next]) {
\t\t\ta52_r377_take_snapshot(next);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
\t\tif (msleep_interruptible(100) && kthread_should_stop())
\t\t\tbreak;
\t}
\treturn 0;
}
"""
    new = """/* A52_PHASE381_DENSE_APEX_CENSUS_V1 */
static int a52_r377_sampler_fn(void *unused)
{
\tstatic const u32 target_ms[A52_R377_SNAPSHOT_COUNT] = {
\t\t17950U, 18050U, 18150U, 18300U,
\t};
\tunsigned int next = 0;
\tu64 now_ms;

\twhile (!kthread_should_stop() && next < A52_R377_SNAPSHOT_COUNT) {
\t\tnow_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
\t\tif (now_ms >= target_ms[next]) {
\t\t\ta52_r377_take_snapshot(next);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
\t\tif (msleep_interruptible(10) && kthread_should_stop())
\t\t\tbreak;
\t}
\treturn 0;
}
"""
    text = one(text, old, new, "dense census window")

    # Phase380 replaced the Phase377 census with a VDC-only sampler.  The new
    # evidence shows apexd worker teardown ending around 17.94 s, so Phase381
    # needs the full apexd thread census active again in that exact window.
    text = one(
        text,
        "static int __init a52_r380_vdc_init(void)\n",
        "static int __init __used a52_r380_vdc_init(void)\n",
        "retire VDC init helper",
    )
    text = one(
        text,
        "late_initcall(a52_r380_vdc_init);\n",
        "/* Phase381 retires the Phase380 VDC-only runtime sampler. */\n",
        "disable VDC late init",
    )
    text = one(
        text,
        "/* Phase380 replaces the Phase377 apexd census at runtime. */\n",
        "late_initcall(a52_r377_init); /* Phase381 dense apexd census */\n",
        "reactivate Phase377 census",
    )
    return text


def patch_ufs(text: str) -> str:
    if "A52_PHASE381_DENSE_UFS_WINDOW_V1" in text:
        return text
    if "A52_PHASE380_USB_BLOCK_LIVE_DEBUG_V1" not in text:
        raise SystemExit("Phase381 requires Phase380 UFS lineage")
    old = """static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
\t15000U, 16000U, 16500U, 17000U, 18000U, 19000U,
};
"""
    new = """/* A52_PHASE381_DENSE_UFS_WINDOW_V1 */
static const u32 a52_r380_target_ms[A52_R380_SNAPSHOT_COUNT] = {
\t17500U, 17800U, 17950U, 18050U, 18200U, 18500U,
};
"""
    return one(text, old, new, "dense UFS window")


def patch_blk(text: str) -> str:
    if "A52_PHASE381_BLK_FREEZE_WAIT_V1" in text:
        return text
    if "A52_PHASE380_BLK_FREEZE_TRACE_V1" not in text:
        raise SystemExit("Phase381 requires Phase380 blk lineage")

    anchor = """void blk_mq_freeze_queue_wait(struct request_queue *q)
{
\twait_event(q->mq_freeze_wq, percpu_ref_is_zero(&q->q_usage_counter));
}
"""
    repl = """/* A52_PHASE381_BLK_FREEZE_WAIT_V1 */
static atomic_t a52_r381_freeze_wait_id = ATOMIC_INIT(0);

void blk_mq_freeze_queue_wait(struct request_queue *q)
{
\tint a52_id = atomic_inc_return(&a52_r381_freeze_wait_id);
\tbool a52_trace = a52_id <= 48;

\tif (a52_trace)
\t\ta52_ackfr_record("B381 W+ id=%d q=%px d=%d z=%u",
\t\t\t a52_id, q, q->mq_freeze_depth,
\t\t\t percpu_ref_is_zero(&q->q_usage_counter));
\twait_event(q->mq_freeze_wq, percpu_ref_is_zero(&q->q_usage_counter));
\tif (a52_trace)
\t\ta52_ackfr_record("B381 W- id=%d q=%px d=%d z=%u",
\t\t\t a52_id, q, q->mq_freeze_depth,
\t\t\t percpu_ref_is_zero(&q->q_usage_counter));
}
"""
    return one(text, anchor, repl, "freeze wait trace")


def patch_loop(text: str) -> str:
    if "A52_PHASE381_LOOP_WORKER_FRONTIER_V1" in text:
        return text
    if "A52_PHASE380_LOOP_TRACE_V1" not in text:
        raise SystemExit("Phase381 requires Phase380 loop lineage")

    text = one(
        text,
        """static atomic_t a52_r380_loop_issue_id = ATOMIC_INIT(0);
static atomic_t a52_r380_loop_done_id = ATOMIC_INIT(0);
""",
        """static atomic_t a52_r380_loop_issue_id = ATOMIC_INIT(0);
static atomic_t a52_r380_loop_done_id = ATOMIC_INIT(0);
/* A52_PHASE381_LOOP_WORKER_FRONTIER_V1 */
static atomic_t a52_r381_loop_trace_id = ATOMIC_INIT(0);
""",
        "loop trace counter",
    )

    old = """\tkthread_queue_work(&lo->worker, &cmd->work);

\treturn BLK_STS_OK;
}
"""
    new = """\t{
\t\tint a52_id = atomic_inc_return(&a52_r381_loop_trace_id);
\t\tif (a52_id <= 80)
\t\t\ta52_ackfr_record("L381 Q n=%d rq=%px op=%u s=%llu b=%u",
\t\t\t\t lo->lo_number, rq, req_op(rq),
\t\t\t\t (unsigned long long)blk_rq_pos(rq), blk_rq_bytes(rq));
\t}
\tkthread_queue_work(&lo->worker, &cmd->work);

\treturn BLK_STS_OK;
}
"""
    text = one(text, old, new, "loop queue detail")

    old = """\tret = do_req_filebacked(lo, rq);
 failed:
"""
    new = """\tif (atomic_read(&a52_r381_loop_trace_id) <= 80)
\t\ta52_ackfr_record("L381 F+ n=%d rq=%px op=%u aio=%u",
\t\t\t lo->lo_number, rq, req_op(rq), cmd->use_aio);
\tret = do_req_filebacked(lo, rq);
\tif (atomic_read(&a52_r381_loop_trace_id) <= 80)
\t\ta52_ackfr_record("L381 F- n=%d rq=%px ret=%d aio=%u",
\t\t\t lo->lo_number, rq, ret, cmd->use_aio);
 failed:
"""
    text = one(text, old, new, "filebacked boundary")

    old = """static void loop_queue_work(struct kthread_work *work)
{
\tstruct loop_cmd *cmd =
\t\tcontainer_of(work, struct loop_cmd, work);

\tloop_handle_cmd(cmd);
}
"""
    new = """static void loop_queue_work(struct kthread_work *work)
{
\tstruct loop_cmd *cmd =
\t\tcontainer_of(work, struct loop_cmd, work);
\tstruct request *rq = blk_mq_rq_from_pdu(cmd);
\tstruct loop_device *lo = rq->q->queuedata;

\tif (atomic_read(&a52_r381_loop_trace_id) <= 80)
\t\ta52_ackfr_record("L381 W+ n=%d rq=%px op=%u aio=%u",
\t\t\t lo ? lo->lo_number : -1, rq, req_op(rq), cmd->use_aio);
\tloop_handle_cmd(cmd);
\tif (atomic_read(&a52_r381_loop_trace_id) <= 80)
\t\ta52_ackfr_record("L381 W- n=%d rq=%px ret=%d aio=%u",
\t\t\t lo ? lo->lo_number : -1, rq, cmd->ret, cmd->use_aio);
}
"""
    text = one(text, old, new, "worker boundary")
    return text


def patch_dwcq(text: str) -> str:
    if "A52_PHASE381_LEGACY_DWC3_MSM_BRIDGE_V1" in text:
        return text

    if '#include <linux/a52_ack_secure_flight_recorder.h>\n' not in text:
        text = one(text, '#include <linux/iopoll.h>\n',
                   '#include <linux/iopoll.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
                   "dwcq recorder include")

    text = one(text,
        """\tstruct icc_path\t\t*icc_path_apps;
};
""",
        """\tstruct icc_path\t\t*icc_path_apps;
\t/* A52_PHASE381_LEGACY_DWC3_MSM_BRIDGE_V1 */
\tbool\t\t\ta52_legacy_msm_dt;
};
""",
        "legacy flag")

    text = one(text,
        """\tplatform_set_drvdata(pdev, qcom);
\tqcom->dev = &pdev->dev;
""",
        """\tplatform_set_drvdata(pdev, qcom);
\tqcom->dev = &pdev->dev;
\tqcom->a52_legacy_msm_dt = np &&
\t\tof_device_is_compatible(np, "qcom,dwc-usb3-msm");
\tif (qcom->a52_legacy_msm_dt)
\t\ta52_ackfr_record("USB381 Q enter legacy=1");
""",
        "legacy detection")

    old = """\tqcom->qscratch_base = devm_ioremap_resource(dev, parent_res);
\tif (IS_ERR(qcom->qscratch_base)) {
\t\tdev_err(dev, "failed to map qscratch, err=%d\\n", ret);
\t\tret = PTR_ERR(qcom->qscratch_base);
\t\tgoto free_urs;
\t}
"""
    new = """\tif (qcom->a52_legacy_msm_dt) {
\t\t/* Downstream lagoon DT exposes one 2 MiB core_base covering both
\t\t * DWC3 and QSCRATCH. Map only QSCRATCH without claiming the whole
\t\t * parent resource, otherwise the child DWC3 resource overlaps.
\t\t */
\t\tif (!parent_res) {
\t\t\tret = -ENODEV;
\t\t\tgoto free_urs;
\t\t}
\t\tqcom->qscratch_base = devm_ioremap(dev,
\t\t\tparent_res->start + SDM845_QSCRATCH_BASE_OFFSET,
\t\t\tSDM845_QSCRATCH_SIZE);
\t\tif (!qcom->qscratch_base) {
\t\t\tret = -ENOMEM;
\t\t\tgoto free_urs;
\t\t}
\t\ta52_ackfr_record("USB381 Q qscratch=%pa", &parent_res->start);
\t} else {
\t\tqcom->qscratch_base = devm_ioremap_resource(dev, parent_res);
\t\tif (IS_ERR(qcom->qscratch_base)) {
\t\t\tdev_err(dev, "failed to map qscratch, err=%d\\n", ret);
\t\t\tret = PTR_ERR(qcom->qscratch_base);
\t\t\tgoto free_urs;
\t\t}
\t}
"""
    text = one(text, old, new, "legacy qscratch mapping")

    old = """\tret = dwc3_qcom_interconnect_init(qcom);
\tif (ret)
\t\tgoto depopulate;
"""
    new = """\tif (!qcom->a52_legacy_msm_dt) {
\t\tret = dwc3_qcom_interconnect_init(qcom);
\t\tif (ret)
\t\t\tgoto depopulate;
\t} else {
\t\t/* Old Samsung/Qualcomm DT uses qcom,msm-bus, not interconnects. */
\t\ta52_ackfr_record("USB381 Q core-populated legacy=1");
\t}
"""
    text = one(text, old, new, "legacy interconnect bypass")

    text = text.replace("""\tdwc3_qcom_interconnect_exit(qcom);
""", """\tif (!qcom->a52_legacy_msm_dt)
\t\tdwc3_qcom_interconnect_exit(qcom);
""")
    text = text.replace("""\tret = dwc3_qcom_interconnect_disable(qcom);
\tif (ret)
\t\tdev_warn(qcom->dev, "failed to disable interconnect: %d\\n", ret);
""", """\tif (!qcom->a52_legacy_msm_dt) {
\t\tret = dwc3_qcom_interconnect_disable(qcom);
\t\tif (ret)
\t\t\tdev_warn(qcom->dev, "failed to disable interconnect: %d\\n", ret);
\t}
""")
    text = text.replace("""\tret = dwc3_qcom_interconnect_enable(qcom);
\tif (ret)
\t\tdev_warn(qcom->dev, "failed to enable interconnect: %d\\n", ret);
""", """\tif (!qcom->a52_legacy_msm_dt) {
\t\tret = dwc3_qcom_interconnect_enable(qcom);
\t\tif (ret)
\t\t\tdev_warn(qcom->dev, "failed to enable interconnect: %d\\n", ret);
\t}
""")

    text = one(text,
        """\t{ .compatible = "qcom,sdm845-dwc3" },
\t{ }
""",
        """\t{ .compatible = "qcom,sdm845-dwc3" },
\t{ .compatible = "qcom,dwc-usb3-msm" }, /* A52 legacy lagoon DT */
\t{ }
""",
        "legacy compatible")

    text = one(text,
        """\tpm_runtime_forbid(dev);

\treturn 0;
""",
        """\tpm_runtime_forbid(dev);
\tif (qcom->a52_legacy_msm_dt)
\t\ta52_ackfr_record("USB381 Q ready mode=%u", qcom->mode);

\treturn 0;
""",
        "legacy ready marker")
    return text


def patch_dwc_core(text: str) -> str:
    if "A52_PHASE381_FORCE_USB_PERIPHERAL_V1" in text:
        return text
    if '#include <linux/a52_ack_secure_flight_recorder.h>\n' not in text:
        text = one(text, '#include "core.h"\n',
                   '#include <linux/a52_ack_secure_flight_recorder.h>\n\n#include "core.h"\n',
                   "dwc core recorder include")

    old = """\tdwc3_get_properties(dwc);

\tdwc->reset = devm_reset_control_array_get_optional_shared(dev);
"""
    new = """\tdwc3_get_properties(dwc);
\t/* A52_PHASE381_FORCE_USB_PERIPHERAL_V1
\t * The downstream lagoon child says dr_mode=drd and relies on the old
\t * msm-dwc3 state machine. For this debug-only g_serial build force the
\t * child into gadget mode so a UDC exists without Android userspace.
\t */
\tif (dev->parent && dev->parent->of_node &&
\t    of_device_is_compatible(dev->parent->of_node,
\t\t\t\t    "qcom,dwc-usb3-msm")) {
\t\tdwc->dr_mode = USB_DR_MODE_PERIPHERAL;
\t\ta52_ackfr_record("USB381 C force-peripheral");
\t}

\tdwc->reset = devm_reset_control_array_get_optional_shared(dev);
"""
    text = one(text, old, new, "force peripheral")

    old = """\tret = dwc3_core_init_mode(dwc);
\tif (ret)
\t\tgoto err5;
"""
    new = """\ta52_ackfr_record("USB381 C init-mode enter mode=%u", dwc->dr_mode);
\tret = dwc3_core_init_mode(dwc);
\ta52_ackfr_record("USB381 C init-mode ret=%d mode=%u", ret, dwc->dr_mode);
\tif (ret)
\t\tgoto err5;
"""
    text = one(text, old, new, "core mode trace")
    return text


def patch_gserial(text: str) -> str:
    if "A52_PHASE381_GSERIAL_BIND_TRACE_V1" in text:
        return text
    if '#include <linux/a52_ack_secure_flight_recorder.h>\n' not in text:
        text = one(text, '#include <linux/tty_flip.h>\n',
                   '#include <linux/tty_flip.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
                   "gserial recorder include")

    text = one(text,
        """static int gs_bind(struct usb_composite_dev *cdev)
{
\tint\t\t\tstatus;
""",
        """/* A52_PHASE381_GSERIAL_BIND_TRACE_V1 */
static int gs_bind(struct usb_composite_dev *cdev)
{
\tint\t\t\tstatus;

\ta52_ackfr_record("USB381 GS bind enter");
""",
        "gserial bind entry")

    text = one(text,
        """\tINFO(cdev, "%s\\n", GS_VERSION_NAME);

\treturn 0;
""",
        """\tINFO(cdev, "%s\\n", GS_VERSION_NAME);
\ta52_ackfr_record("USB381 GS bind ready acm=%u ports=%u",
\t\t\t use_acm, n_ports);

\treturn 0;
""",
        "gserial bind ready")

    text = one(text,
        """static int __init init(void)
{
""",
        """static int __init init(void)
{
\ta52_ackfr_record("USB381 GS init acm=%u enable=%u", use_acm, enable);
""",
        "gserial init trace")
    return text


def validate(root: Path) -> None:
    checks = {
        REC: ("A52_PHASE381_RECORDER_ADMISSION_V1", 'strncmp(fmt, "USB381", 6)'),
        SYSCALL: ("A52_PHASE381_DENSE_APEX_CENSUS_V1", "late_initcall(a52_r377_init); /* Phase381 dense apexd census */"),
        UFS: ("A52_PHASE381_DENSE_UFS_WINDOW_V1", "17500U, 17800U, 17950U, 18050U, 18200U, 18500U"),
        BLK: ("A52_PHASE381_BLK_FREEZE_WAIT_V1", 'B381 W+ id=%d q=%px d=%d z=%u'),
        LOOP: ("A52_PHASE381_LOOP_WORKER_FRONTIER_V1", 'L381 W+ n=%d rq=%px op=%u aio=%u'),
        DWCQ: ("A52_PHASE381_LEGACY_DWC3_MSM_BRIDGE_V1", 'qcom,dwc-usb3-msm'),
        DWCCORE: ("A52_PHASE381_FORCE_USB_PERIPHERAL_V1", 'USB381 C force-peripheral'),
        GSERIAL: ("A52_PHASE381_GSERIAL_BIND_TRACE_V1", 'USB381 GS bind ready'),
    }
    for rel, tokens in checks.items():
        txt = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in txt:
                raise SystemExit(f"Phase381 validation missing {token} in {rel}")


def apply(root: Path) -> None:
    mapping = (
        (REC, patch_recorder),
        (SYSCALL, patch_census),
        (UFS, patch_ufs),
        (BLK, patch_blk),
        (LOOP, patch_loop),
        (DWCQ, patch_dwcq),
        (DWCCORE, patch_dwc_core),
        (GSERIAL, patch_gserial),
    )
    for rel, fn in mapping:
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"Phase381 missing source: {path}")
        before = path.read_text(encoding="utf-8")
        after = fn(before)
        path.write_text(after, encoding="utf-8")
    validate(root)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if ns.check_only:
        validate(ns.root)
        print("Phase381 frontier + legacy USB audit: PASS")
        return 0
    apply(ns.root)
    print("Phase381 frontier + legacy USB applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
