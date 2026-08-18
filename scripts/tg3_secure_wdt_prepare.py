#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

APPLY = Path("scripts/tg1_apply_critical_flight_recorder.py")
SCM_MARKER = "A52_TOUCHGRASS_V3_SECURE_WDOG_SCM_OFF"
WDT_SECURE_MARKER = "A52_TOUCHGRASS_V3_SECURE_AND_LOCAL_WDOG_OFF"
WDT_PROOF_MARKER = "A52_TOUCHGRASS_V3_WDOG_LATE_PROOF_15S"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)


def main() -> int:
    apply = APPLY.read_text(encoding="utf-8")
    if "A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V3_WDT_OFF_TYPED_SEAL_210S" not in apply:
        raise SystemExit("secure-WDT extension requires the v3 generated-tool patch first")
    if SCM_MARKER in apply and WDT_SECURE_MARKER in apply and WDT_PROOF_MARKER in apply:
        print("TouchGrass v3 secure-WDT generated-tool patch: already applied")
        return 0

    apply = replace_once(
        apply,
        'WDT_REL = Path("drivers/watchdog/qcom-wdt.c")',
        'WDT_REL = Path("drivers/watchdog/qcom-wdt.c")\n'
        'SCM_REL = Path("drivers/firmware/qcom_scm.c")',
        "SCM source path",
    )

    secure_patchers = f'''\n\ndef patch_secure_scm(text: str) -> str:
    marker = "{SCM_MARKER}"
    if marker in text:
        return text
    anchor = "int qcom_scm_set_remote_state(u32 state, u32 id)\\n{{"
    helper = """/* {SCM_MARKER}: diagnostic boot only */
int a52_qcom_scm_disable_secure_wdog(void)
{{
\tstruct qcom_scm_desc desc = {{
\t\t.svc = QCOM_SCM_SVC_BOOT,
\t\t.cmd = 0x07, /* Qualcomm secure watchdog disable */
\t\t.arginfo = QCOM_SCM_ARGS(1),
\t\t.args[0] = 1,
\t\t.owner = ARM_SMCCC_OWNER_SIP,
\t}};
\tstruct qcom_scm_res res;
\tint ret;

\tif (!__scm)
\t\treturn -ENODEV;

\tret = qcom_scm_call(__scm->dev, &desc, &res);
\treturn ret ? : res.result[0];
}}
EXPORT_SYMBOL(a52_qcom_scm_disable_secure_wdog);

"""
    return replace_once(text, anchor, helper + anchor, "secure watchdog SCM helper")


def patch_secure_watchdog(text: str) -> str:
    text = patch_watchdog(text)
    marker = "{WDT_SECURE_MARKER}"
    proof_marker = "{WDT_PROOF_MARKER}"
    if marker in text and proof_marker in text:
        return text

    text = replace_once(
        text,
        "#include <linux/a52_ack_secure_flight_recorder.h>\\n",
        "#include <linux/a52_ack_secure_flight_recorder.h>\\n"
        "#include <linux/jiffies.h>\\n"
        "#include <linux/workqueue.h>\\n\\n"
        "extern int a52_qcom_scm_disable_secure_wdog(void);\\n",
        "secure watchdog SCM declaration",
    )
    text = replace_once(
        text,
        "\\tconst u32\\t\\t*layout;\\n}};\\n",
        "\\tconst u32\\t\\t*layout;\\n"
        "\\tstruct delayed_work\\ta52_diag_work;\\n"
        "\\tint\\t\\t\\ta52_secure_wdt_rc;\\n"
        "\\tunsigned int\\t\\ta52_diag_samples;\\n"
        "}};\\n",
        "watchdog diagnostic state",
    )

    worker_anchor = "static int qcom_wdt_ping(struct watchdog_device *wdd)\\n{{"
    worker = """/* {WDT_PROOF_MARKER}: retain watchdog state near the late failure window */
static void a52_qcom_wdt_diag_workfn(struct work_struct *work)
{{
\tstruct qcom_wdt *wdt = container_of(to_delayed_work(work),
\t\t\t\t\t    struct qcom_wdt, a52_diag_work);
\tu32 en;
\tu32 sts;

\tif (wdt->a52_secure_wdt_rc)
\t\twdt->a52_secure_wdt_rc = a52_qcom_scm_disable_secure_wdog();

\ten = readl(wdt_addr(wdt, WDT_EN));
\tsts = readl(wdt_addr(wdt, WDT_STS));
\twdt->a52_diag_samples++;
\ta52_ackfr_record("WDT late sec=%d en=%u sts=%u sample=%u",
\t\t\t  wdt->a52_secure_wdt_rc,
\t\t\t  !!(en & QCOM_WDT_ENABLE), !!(sts & 1),
\t\t\t  wdt->a52_diag_samples);

\tif (wdt->a52_diag_samples < 14)
\t\tschedule_delayed_work(&wdt->a52_diag_work,
\t\t\t\t      msecs_to_jiffies(15000));
}}

"""
    text = replace_once(text, worker_anchor, worker + worker_anchor,
                        "late watchdog proof worker")

    text = replace_once(
        text,
        "\\t\\t/* A52_TOUCHGRASS_V3_DIAGNOSTIC_QCOM_WDT_OFF: diagnostic boot only */\\n",
        "\\t\\t/* A52_TOUCHGRASS_V3_DIAGNOSTIC_QCOM_WDT_OFF: diagnostic boot only */\\n"
        f"\\t\\t/* {{marker}}: secure SCM + local WDT_EN disarm */\\n",
        "secure/local watchdog marker",
    )
    text = replace_once(
        text,
        "\\t\\t\\tu32 a52_wdt_before;\\n"
        "\\t\\t\\tu32 a52_wdt_after;\\n\\n"
        "\\t\\t\\ta52_wdt_before = readl(wdt_addr(wdt, WDT_STS));\\n",
        "\\t\\t\\tu32 a52_wdt_before;\\n"
        "\\t\\t\\tu32 a52_wdt_after;\\n\\n"
        "\\t\\t\\twdt->a52_secure_wdt_rc = a52_qcom_scm_disable_secure_wdog();\\n"
        "\\t\\t\\ta52_wdt_before = readl(wdt_addr(wdt, WDT_STS));\\n",
        "secure watchdog call",
    )
    text = replace_once(
        text,
        "\\t\\t\\ta52_ackfr_record(\\\"WDT disarm before=%u after=%u\\\",\\n"
        "\\t\\t\\t\\t\\t  !!(a52_wdt_before & 1),\\n"
        "\\t\\t\\t\\t\\t  !!(a52_wdt_after & 1));\\n",
        "\\t\\t\\ta52_ackfr_record(\\\"WDT secure rc=%d local before=%u after=%u\\\",\\n"
        "\\t\\t\\t\\t\\t  wdt->a52_secure_wdt_rc,\\n"
        "\\t\\t\\t\\t\\t  !!(a52_wdt_before & 1),\\n"
        "\\t\\t\\t\\t\\t  !!(a52_wdt_after & 1));\\n"
        "\\t\\t\\tINIT_DELAYED_WORK(&wdt->a52_diag_work, a52_qcom_wdt_diag_workfn);\\n"
        "\\t\\t\\twdt->a52_diag_samples = 0;\\n"
        "\\t\\t\\tschedule_delayed_work(&wdt->a52_diag_work, msecs_to_jiffies(15000));\\n"
        "\\t\\t\\tplatform_set_drvdata(pdev, wdt);\\n",
        "watchdog recorder result and late proof",
    )
    text = replace_once(
        text,
        "A52 TouchGrass v3: QCOM watchdog disabled for late recorder\\\\n",
        "A52 TouchGrass v3: secure watchdog attempted; local watchdog disabled\\\\n",
        "watchdog diagnostic log",
    )
    return text
'''

    apply = replace_once(
        apply,
        "\n\ndef apply(root: Path) -> None:\n",
        secure_patchers + "\n\ndef apply(root: Path) -> None:\n",
        "secure watchdog patch functions",
    )
    apply = replace_once(
        apply,
        "paths = [REC_REL, HDR_REL, DSI_REL, SMMU_REL, WDT_REL]",
        "paths = [REC_REL, HDR_REL, DSI_REL, SMMU_REL, WDT_REL, SCM_REL]",
        "apply source list",
    )
    apply = replace_once(
        apply,
        "        WDT_REL: patch_watchdog,\n    }",
        "        WDT_REL: patch_secure_watchdog,\n        SCM_REL: patch_secure_scm,\n    }",
        "apply function map",
    )
    apply = replace_once(
        apply,
        "if changed not in (0, 5):",
        "if changed not in (0, 6):",
        "apply changed count",
    )
    apply = replace_once(
        apply,
        "partial application: changed {changed}/5 files",
        "partial application: changed {changed}/6 files",
        "apply count message",
    )

    old_loop = "for rel in [REC_REL, HDR_REL, DSI_REL, SMMU_REL, WDT_REL]:"
    if apply.count(old_loop) != 3:
        raise SystemExit(f"secure-WDT self-test source lists: expected 3 anchors, found {apply.count(old_loop)}")
    apply = apply.replace(
        old_loop,
        "for rel in [REC_REL, HDR_REL, DSI_REL, SMMU_REL, WDT_REL, SCM_REL]:",
    )
    apply = replace_once(
        apply,
        '            expected_marker = "A52_TOUCHGRASS_V3_DIAGNOSTIC_QCOM_WDT_OFF" if rel == WDT_REL else MARKER\n',
        f'            if rel == WDT_REL:\n'
        f'                expected_marker = "{WDT_SECURE_MARKER}"\n'
        f'                if text.count("{WDT_PROOF_MARKER}") != 1:\n'
        f'                    raise RuntimeError("self-test late watchdog proof marker count")\n'
        f'            elif rel == SCM_REL:\n'
        f'                expected_marker = "{SCM_MARKER}"\n'
        f'            else:\n'
        f'                expected_marker = MARKER\n',
        "self-test secure marker selection",
    )

    APPLY.write_text(apply, encoding="utf-8")
    print("TouchGrass v3 secure-WDT generated-tool patch: PASS")
    print("TouchGrass v3 secure watchdog SCM command 0x07: staged")
    print("TouchGrass v3 15-second watchdog proof recorder: staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
