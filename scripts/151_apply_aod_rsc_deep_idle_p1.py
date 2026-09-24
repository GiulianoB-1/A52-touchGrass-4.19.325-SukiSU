#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 AOD P151: allow deep RSC CMD state only in panel LPM"
REFERENCE = "Samsung rsc_4_frame_idle workaround scoped away from static AOD"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
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
    encoder = root / "techpack/display/msm/sde/sde_encoder.c"
    common_h = root / "techpack/display/msm/samsung/ss_dsi_panel_common.h"
    panel_dts = root / "arch/arm64/boot/dts/samsung/a52/a52xq/a52xq_eur_open_w00_r00.dts"
    ufs = root / "drivers/scsi/ufs/ufshcd.c"
    lpm = root / "drivers/cpuidle/lpm-levels.c"
    sugov = root / "kernel/sched/cpufreq_schedutil.c"

    for path in (encoder, common_h, panel_dts, ufs, lpm, sugov):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    e = encoder.read_text()
    h = common_h.read_text()
    d = panel_dts.read_text()
    u = ufs.read_text()
    lp = lpm.read_text()
    sg = sugov.read_text()

    # Exact boot-tested P149 stack boundary.
    if "A52 UFS P149: precise devfreq + runtime-PM autosuspend" not in u:
        raise SystemExit("P149 UFS baseline marker missing")
    if "A52 QCOM LPM P1: raw idle locks + short-idle tick + hotpath cleanup" not in lp:
        raise SystemExit("P148 Qualcomm LPM baseline marker missing")
    if "A52 P148 BATTERY: Qualcomm LPM + WALT/schedutil correctness" not in sg:
        raise SystemExit("P148 scheduler baseline marker missing")

    # Require the exact Samsung workaround and A52 panel capabilities that
    # motivated this phase.  Do not silently apply to a different panel.
    for needle in (
        "bool rsc_4_frame_idle;",
        "static inline bool ss_is_panel_lpm(",
        "PANEL_PWR_LPM",
    ):
        if needle not in h:
            raise SystemExit(f"Samsung display baseline missing: {needle}")

    for needle in (
        'samsung,disp-model = "AMS646YD01";',
        "samsung,support_lpm;",
        "samsung,rsc_4_frame_idle;",
    ):
        if needle not in d:
            raise SystemExit(f"A52 AMS646YD01 DTS baseline missing: {needle}")

    # This panel does not advertise Samsung's generic HOP/LFD framework.
    # P151 must not accidentally turn that on or pretend this is a 1 Hz panel.
    if "samsung,support_lfd;" in d:
        raise SystemExit("unexpected A52 LFD enablement; P151 assumptions changed")

    fn = function_block(e, "static int _sde_encoder_update_rsc_client(")

    old = """#if defined(CONFIG_DISPLAY_SAMSUNG)
\tif (vdd->rsc_4_frame_idle && rsc_state == SDE_RSC_CMD_STATE)
\t\trsc_state = SDE_RSC_CLK_STATE;

"""
    new = f"""#if defined(CONFIG_DISPLAY_SAMSUNG)
\t/*
\t * {MARKER}
\t *
\t * Samsung's four-frame workaround keeps active command-mode display
\t * traffic in SDE_RSC_CLK_STATE to avoid inter-frame drop/glitch issues.
\t * AOD is different: the panel is already in PANEL_PWR_LPM and mostly
\t * static, so retaining that workaround prevents the Qualcomm RSC from
\t * reaching its deeper SDE_RSC_CMD_STATE between AOD updates.
\t *
\t * Preserve the workaround exactly for normal 60/120 Hz use and bypass
\t * it only while Samsung reports panel LPM.
\t */
\tif (vdd->rsc_4_frame_idle &&
\t\t\t!ss_is_panel_lpm(vdd) &&
\t\t\trsc_state == SDE_RSC_CMD_STATE)
\t\trsc_state = SDE_RSC_CLK_STATE;

"""
    if old not in fn:
        raise SystemExit("Samsung rsc_4_frame_idle workaround shape changed")
    fn_new = fn.replace(old, new, 1)
    e = replace_once(e, fn, fn_new, "AOD-only RSC deep-state policy")

    encoder.write_text(e)

    # Structural audits.
    E = encoder.read_text()
    fn2 = function_block(E, "static int _sde_encoder_update_rsc_client(")

    for needle in (
        MARKER,
        "vdd->rsc_4_frame_idle &&",
        "!ss_is_panel_lpm(vdd) &&",
        "rsc_state == SDE_RSC_CMD_STATE",
        "rsc_state = SDE_RSC_CLK_STATE;",
    ):
        if needle not in fn2:
            raise SystemExit(f"audit failed: missing {needle}")

    # The old unconditional Samsung override must be gone.
    stale = """if (vdd->rsc_4_frame_idle && rsc_state == SDE_RSC_CMD_STATE)
\t\trsc_state = SDE_RSC_CLK_STATE;"""
    if stale in fn2:
        raise SystemExit("audit failed: unconditional four-frame RSC override remains")

    # Verify we did not alter the actual RSC state-selection architecture.
    for needle in (
        "rsc_state = enable ? SDE_RSC_CMD_STATE : SDE_RSC_IDLE_STATE;",
        "rsc_state = enable ? SDE_RSC_CLK_STATE : SDE_RSC_IDLE_STATE;",
        "sde_rsc_client_state_update(sde_enc->rsc_client,",
    ):
        if needle not in fn2:
            raise SystemExit(f"audit failed: Qualcomm RSC core selection changed: {needle}")

    # Explicitly ensure P151 does not fake-enable unsupported A52 features.
    D = panel_dts.read_text()
    if "samsung,support_lfd;" in D:
        raise SystemExit("audit failed: generic LFD was enabled")
    if "qcom,sde-uidle" in D:
        raise SystemExit("audit failed: unsupported UIDLE property added")

    print("[audit] exact boot-tested P149 marker required: PASS")
    print("[audit] A52 AMS646YD01 + Samsung panel LPM required: PASS")
    print("[audit] normal-display rsc_4_frame_idle workaround preserved: PASS")
    print("[audit] panel-LPM/AOD bypasses only the shallow CLK-state override: PASS")
    print("[audit] Qualcomm SDE_RSC_CMD_STATE architecture preserved: PASS")
    print("[audit] no LFD/1Hz support fabricated: PASS")
    print("[audit] no AOD brightness, DSI command, refresh-rate or regulator tuning changed: PASS")
    print(f"[reference] {REFERENCE}")
    print("[done] A52 P151 AOD RSC deep-idle phase applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
