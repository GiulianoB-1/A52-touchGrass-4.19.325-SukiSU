#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

ENC = Path("drivers/a52_display/msm/sde/sde_encoder.c")
RM = Path("drivers/a52_display/msm/sde/sde_rm.c")
CRTC = Path("drivers/a52_display/msm/sde/sde_crtc.c")
REC_INCLUDE = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
ATOMIC_INCLUDE = "#include <linux/atomic.h>\n"
MARK = "A52_PHASE343_DRM_RM_RESERVATION_STATE_DISCRIMINATOR_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase343 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def ensure_after(text: str, anchor: str, addition: str, label: str) -> str:
    if addition in text:
        return text
    return one(text, anchor, anchor + addition, label)


def behavior_counts(text: str) -> dict[str, int]:
    tokens = (
        "writel(", "writel_relaxed(", "writeq_relaxed(", "readl_relaxed(",
        "regmap_write(", "regmap_update_bits(", "DSI_W32(", "SDE_REG_WRITE(",
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(", "wmb(", "mb(",
        "pm_runtime_get_sync(", "pm_runtime_put_sync(",
        "sde_power_resource_enable(",
    )
    return {token: text.count(token) for token in tokens}


def patch_encoder(text: str) -> str:
    if MARK in text:
        return text

    before = behavior_counts(text)
    text = ensure_after(
        text,
        '#include "sde_trace.h"\n',
        ATOMIC_INCLUDE + REC_INCLUDE,
        "encoder includes",
    )
    text = one(
        text,
        '#include "sde_core_irq.h"\n',
        '#include "sde_core_irq.h"\n\n'
        f'/* {MARK}\n'
        ' * Observe the exact modeset state entering the TouchGrass resource\n'
        ' * reservation path. No DRM state or return value is modified.\n'
        ' */\n'
        'static atomic_t a52_p343_encoder_seq = ATOMIC_INIT(0);\n',
        "encoder marker state",
    )

    old = '''{
\tint ret = 0;
\tstruct drm_display_mode *adj_mode = &crtc_state->adjusted_mode;

\tif (sde_conn && drm_atomic_crtc_needs_modeset(crtc_state)) {
\t\tstruct msm_display_topology *topology = NULL;
'''
    new = '''{
\tint ret = 0;
\tstruct drm_display_mode *adj_mode = &crtc_state->adjusted_mode;
\tunsigned int a52_p343_n;

\ta52_p343_n = (unsigned int)atomic_inc_return(&a52_p343_encoder_seq);

\tif (sde_conn && drm_atomic_crtc_needs_modeset(crtc_state)) {
\t\tstruct msm_display_topology *topology = NULL;

\t\tif (a52_p343_n <= 64U || !(a52_p343_n & 0x3fU))
\t\t\ta52_ackfr_record(
\t\t\t\t"P276 343E n=%u e=%u c=%u ea=%u/%u em=%x cm=%x ch=%u/%u/%u cn=%u be=%u",
\t\t\t\ta52_p343_n, drm_enc->base.id,
\t\t\t\tcrtc_state->crtc ? crtc_state->crtc->base.id : 0,
\t\t\t\t(unsigned int)crtc_state->enable,
\t\t\t\t(unsigned int)crtc_state->active,
\t\t\t\tcrtc_state->encoder_mask, crtc_state->connector_mask,
\t\t\t\t(unsigned int)crtc_state->mode_changed,
\t\t\t\t(unsigned int)crtc_state->active_changed,
\t\t\t\t(unsigned int)crtc_state->connectors_changed,
\t\t\t\tconn_state->crtc ? conn_state->crtc->base.id : 0,
\t\t\t\tconn_state->best_encoder ?
\t\t\t\t\tconn_state->best_encoder->base.id : 0);
'''
    text = one(text, old, new, "encoder state snapshot")

    old = '''\t\tret = sde_rm_reserve(&sde_kms->rm, drm_enc, crtc_state,
\t\t\tconn_state, true);
\t\tif (ret) {
'''
    new = '''\t\tret = sde_rm_reserve(&sde_kms->rm, drm_enc, crtc_state,
\t\t\tconn_state, true);
\t\tif (a52_p343_n <= 64U || ret || !(a52_p343_n & 0x3fU))
\t\t\ta52_ackfr_record("P276 343Q n=%u e=%u rm=%d em=%x cm=%x",
\t\t\t\ta52_p343_n, drm_enc->base.id, ret,
\t\t\t\tcrtc_state->encoder_mask, crtc_state->connector_mask);
\t\tif (ret) {
'''
    text = one(text, old, new, "encoder rm result")

    if behavior_counts(text) != before:
        raise SystemExit("Phase343 encoder patch changed protected behavior")
    return text


def patch_rm(text: str) -> str:
    if MARK in text:
        return text

    before = behavior_counts(text)
    text = ensure_after(
        text,
        '#include "sde_hw_qdss.h"\n',
        ATOMIC_INCLUDE + REC_INCLUDE,
        "rm includes",
    )
    text = one(
        text,
        "#define RESERVED_BY_OTHER(h, r) \\\n",
        f"/* {MARK}\n"
        " * Trace current/next SDE reservations around TEST_ONLY atomic checks.\n"
        " * Read-only instrumentation only.\n"
        " */\n"
        "static atomic_t a52_p343_rm_seq = ATOMIC_INIT(0);\n\n"
        "#define RESERVED_BY_OTHER(h, r) \\\n",
        "rm marker state",
    )

    old = '''\tstruct msm_drm_private *priv;
\tstruct sde_kms *sde_kms;
\tint ret;

\tif (!rm || !enc || !crtc_state || !conn_state) {
'''
    new = '''\tstruct msm_drm_private *priv;
\tstruct sde_kms *sde_kms;
\tint ret;
\tunsigned int a52_p343_n;

\ta52_p343_n = (unsigned int)atomic_inc_return(&a52_p343_rm_seq);

\tif (!rm || !enc || !crtc_state || !conn_state) {
'''
    text = one(text, old, new, "rm sequence")

    old = '''\trsvp_cur = _sde_rm_get_rsvp(rm, enc);
\trsvp_nxt = _sde_rm_get_rsvp_nxt(rm, enc);

\t/*
'''
    new = '''\trsvp_cur = _sde_rm_get_rsvp(rm, enc);
\trsvp_nxt = _sde_rm_get_rsvp_nxt(rm, enc);

\tif (a52_p343_n <= 64U || (test_only && rsvp_cur && rsvp_nxt) ||
\t\t\t!(a52_p343_n & 0x3fU))
\t\ta52_ackfr_record(
\t\t\t"P276 343R n=%u t=%u e=%u cur=%u nxt=%u sp=%u em=%x cm=%x",
\t\t\ta52_p343_n, (unsigned int)test_only, enc->base.id,
\t\t\trsvp_cur ? rsvp_cur->seq : 0,
\t\t\trsvp_nxt ? rsvp_nxt->seq : 0,
\t\t\t(unsigned int)_sde_rm_is_display_in_cont_splash(sde_kms, enc),
\t\t\tcrtc_state->encoder_mask, crtc_state->connector_mask);

\t/*
'''
    text = one(text, old, new, "rm current next snapshot")

    old = '''\t\tif (rsvp_nxt) {
\t\t\tSDE_ERROR("poll timeout cur %d nxt %d enc %d\\n",
'''
    new = '''\t\tif (rsvp_nxt) {
\t\t\ta52_ackfr_record(
\t\t\t\t"P276 343T n=%u e=%u cur=%u nxt=%u em=%x cm=%x",
\t\t\t\ta52_p343_n, enc->base.id, rsvp_cur->seq,
\t\t\t\trsvp_nxt->seq, crtc_state->encoder_mask,
\t\t\t\tcrtc_state->connector_mask);
\t\t\tSDE_ERROR("poll timeout cur %d nxt %d enc %d\\n",
'''
    text = one(text, old, new, "rm poll timeout")

    old = '''\t} else if (test_only && !RM_RQ_LOCK(&reqs)) {
\t\t/*
'''
    new = '''\t} else if (test_only && !RM_RQ_LOCK(&reqs)) {
\t\tif (a52_p343_n <= 64U || !(a52_p343_n & 0x3fU))
\t\t\ta52_ackfr_record("P276 343N n=%u e=%u nxt=%u lock=0",
\t\t\t\ta52_p343_n, enc->base.id, rsvp_nxt->seq);
\t\t/*
'''
    text = one(text, old, new, "rm test-only pending snapshot")

    old = '''end:
\t_sde_rm_print_rsvps(rm, SDE_RM_STAGE_FINAL);
\tmutex_unlock(&rm->rm_lock);

\treturn ret;
'''
    new = '''end:
\t_sde_rm_print_rsvps(rm, SDE_RM_STAGE_FINAL);
\tif (a52_p343_n <= 64U || ret || !(a52_p343_n & 0x3fU))
\t\ta52_ackfr_record("P276 343X n=%u e=%u t=%u ret=%d",
\t\t\ta52_p343_n, enc->base.id, (unsigned int)test_only, ret);
\tmutex_unlock(&rm->rm_lock);

\treturn ret;
'''
    text = one(text, old, new, "rm exit")

    if behavior_counts(text) != before:
        raise SystemExit("Phase343 rm patch changed protected behavior")
    return text


def patch_crtc(text: str) -> str:
    if MARK in text:
        return text

    before = behavior_counts(text)
    text = ensure_after(
        text,
        '#include <linux/ktime.h>\n',
        ATOMIC_INCLUDE + REC_INCLUDE,
        "crtc includes",
    )
    text = one(
        text,
        '#include "sde_connector.h"\n',
        '#include "sde_connector.h"\n\n'
        f'/* {MARK}\n'
        ' * Verify that atomic-state destruction carries the encoder mask needed\n'
        ' * to release TEST_ONLY SDE RM reservations.\n'
        ' */\n'
        'static atomic_t a52_p343_destroy_seq = ATOMIC_INIT(0);\n',
        "crtc marker state",
    )

    old = '''\tstruct drm_encoder *enc;
\tstruct sde_kms *sde_kms;

\tif (!crtc || !state) {
'''
    new = '''\tstruct drm_encoder *enc;
\tstruct sde_kms *sde_kms;
\tunsigned int a52_p343_n;

\ta52_p343_n = (unsigned int)atomic_inc_return(&a52_p343_destroy_seq);

\tif (!crtc || !state) {
'''
    text = one(text, old, new, "crtc destroy sequence")

    old = '''\tSDE_DEBUG("crtc%d\\n", crtc->base.id);

\tdrm_for_each_encoder_mask(enc, crtc->dev, state->encoder_mask)
\t\tsde_rm_release(&sde_kms->rm, enc, true);

\t__drm_atomic_helper_crtc_destroy_state(state);
'''
    new = '''\tSDE_DEBUG("crtc%d\\n", crtc->base.id);

\tif (a52_p343_n <= 96U || state->encoder_mask ||
\t\t\t!(a52_p343_n & 0x3fU))
\t\ta52_ackfr_record(
\t\t\t"P276 343D n=%u c=%u ea=%u/%u em=%x cm=%x",
\t\t\ta52_p343_n, crtc->base.id,
\t\t\t(unsigned int)state->enable, (unsigned int)state->active,
\t\t\tstate->encoder_mask, state->connector_mask);

\tdrm_for_each_encoder_mask(enc, crtc->dev, state->encoder_mask) {
\t\tif (a52_p343_n <= 96U || !(a52_p343_n & 0x3fU))
\t\t\ta52_ackfr_record("P276 343L n=%u c=%u e=%u",
\t\t\t\ta52_p343_n, crtc->base.id, enc->base.id);
\t\tsde_rm_release(&sde_kms->rm, enc, true);
\t}

\t__drm_atomic_helper_crtc_destroy_state(state);
'''
    text = one(text, old, new, "crtc destroy release trace")

    if behavior_counts(text) != before:
        raise SystemExit("Phase343 crtc patch changed protected behavior")
    return text


def verify(root: Path, before: dict[Path, str] | None = None) -> dict:
    files = {
        ENC: (root / ENC).read_text(),
        RM: (root / RM).read_text(),
        CRTC: (root / CRTC).read_text(),
    }
    required = {
        ENC: (
            MARK,
            "P276 343E n=%u e=%u c=%u ea=%u/%u em=%x cm=%x ch=%u/%u/%u cn=%u be=%u",
            "P276 343Q n=%u e=%u rm=%d em=%x cm=%x",
        ),
        RM: (
            MARK,
            "P276 343R n=%u t=%u e=%u cur=%u nxt=%u sp=%u em=%x cm=%x",
            "P276 343T n=%u e=%u cur=%u nxt=%u em=%x cm=%x",
            "P276 343N n=%u e=%u nxt=%u lock=0",
            "P276 343X n=%u e=%u t=%u ret=%d",
        ),
        CRTC: (
            MARK,
            "P276 343D n=%u c=%u ea=%u/%u em=%x cm=%x",
            "P276 343L n=%u c=%u e=%u",
        ),
    }

    for path, tokens in required.items():
        for token in tokens:
            if files[path].count(token) != 1:
                raise SystemExit(
                    f"Phase343 verification failed {path}: {token!r} count="
                    f"{files[path].count(token)}"
                )
        if REC_INCLUDE not in files[path]:
            raise SystemExit(f"Phase343 recorder include missing: {path}")

    if before:
        for path, old in before.items():
            new = files[path]
            if behavior_counts(old) != behavior_counts(new):
                raise SystemExit(f"Phase343 protected behavior changed: {path}")

    return {
        "status": "phase343-drm-rm-reservation-state-discriminator-staged",
        "functional_change": "diagnostic-only",
        "base_phase": "337",
        "targets": [str(p) for p in files],
        "markers": {
            "343E": "encoder modeset state before RM reserve",
            "343Q": "encoder RM reserve return",
            "343R": "RM current/next reservation snapshot",
            "343T": "RM pending-next poll timeout",
            "343N": "successful TEST_ONLY pending reservation",
            "343X": "RM reserve final return",
            "343D": "CRTC state destructor masks",
            "343L": "actual pending-reservation release attempt",
        },
        "register_writes_added": 0,
        "clock_power_reset_delay_changed": False,
        "drm_return_semantics_changed": False,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()

    root = args.root.resolve()
    for rel in (ENC, RM, CRTC):
        if not (root / rel).is_file():
            raise SystemExit(f"Phase343 missing required source: {rel}")

    before = {
        ENC: (root / ENC).read_text(),
        RM: (root / RM).read_text(),
        CRTC: (root / CRTC).read_text(),
    }

    if not args.check_only:
        (root / ENC).write_text(patch_encoder(before[ENC]))
        (root / RM).write_text(patch_rm(before[RM]))
        (root / CRTC).write_text(patch_crtc(before[CRTC]))

    report = verify(root, before if not args.check_only else None)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
