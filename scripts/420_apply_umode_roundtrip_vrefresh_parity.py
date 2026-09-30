#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE420_UMODE_ROUNDTRIP_VREFRESH_PARITY_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MODES = Path("drivers/gpu/drm/drm_modes.c")
DISPLAY = Path("drivers/a52_display/msm/dsi/dsi_display.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase420 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE419_UMODE_VREFRESH_PARITY_V1" not in text:
        raise SystemExit("Phase420 requires Phase419 recorder")

    text = one(
        text,
        'if (strncmp(fmt, "P419", 4) &&\n',
        'if (strncmp(fmt, "P420", 4) &&\n'
        '    strncmp(fmt, "P419", 4) &&\n',
        "first P420 gate",
    )
    text = one(
        text,
        '\tif (!fmt || (\n\t    strncmp(fmt, "P419", 4) &&\n',
        '\tif (!fmt || (\n\t    strncmp(fmt, "P420", 4) &&\n'
        '\t    strncmp(fmt, "P419", 4) &&\n',
        "second P420 gate",
    )
    text = one(
        text,
        'if (unlikely(atomic_read(&a52_r280_retained)) &&\n'
        '\t    strncmp(fmt, "P419", 4) &&\n',
        'if (unlikely(atomic_read(&a52_r280_retained)) &&\n'
        '\t    strncmp(fmt, "P420", 4) &&\n'
        '\t    strncmp(fmt, "P419", 4) &&\n',
        "P420 retention bypass",
    )
    text += f'\n/* {MARK}: P420 round-trip evidence admitted to sequential disk tier. */\n'
    return text


def patch_modes(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE419_UMODE_VREFRESH_PARITY_V1" not in text:
        raise SystemExit("Phase420 requires Phase419 drm_modes")

    outbound_anchor = "void drm_mode_convert_to_umode(struct drm_mode_modeinfo *out,\n"
    outbound_new = (
        "/* A52_PHASE420_UMODE_ROUNDTRIP_VREFRESH_PARITY_V1 */\n"
        "static atomic_t a52_p420_to_umode_once = ATOMIC_INIT(0);\n\n"
        + outbound_anchor
    )
    text = one(text, outbound_anchor, outbound_new, "outbound one-shot state")

    old = '''\tout->vscan = in->vscan;
\tout->vrefresh = drm_mode_vrefresh(in);
\tout->flags = in->flags;
'''
    new = '''\tout->vscan = in->vscan;
\t{
\t\tint a52_p420_calc = drm_mode_vrefresh(in);
\t\tint a52_p420_stored = in->vrefresh;

\t\tout->vrefresh = a52_p420_stored ?
\t\t\ta52_p420_stored : a52_p420_calc;
\t\tif (in->hdisplay == 1080 && in->vdisplay == 2400 &&
\t\t    atomic_cmpxchg(&a52_p420_to_umode_once, 0, 1) == 0)
\t\t\ta52_ackfr_record(
\t\t\t\t"P420 TO st=%d calc=%d out=%u p=%d",
\t\t\t\ta52_p420_stored, a52_p420_calc,
\t\t\t\tout->vrefresh, in->clock);
\t}
\tout->flags = in->flags;
'''
    text = one(text, old, new, "kernel-to-userspace vrefresh parity")
    text += f'\n/* {MARK}: drm_mode_convert_to_umode preserves stored refresh. */\n'
    return text


def patch_display(text: str) -> str:
    if MARK in text:
        return text

    sig = "int dsi_display_find_mode(struct dsi_display *display,"
    pos = text.find(sig)
    if pos < 0:
        raise SystemExit("Phase420 dsi_display_find_mode missing")

    helper = '''/* A52_PHASE420_UMODE_ROUNDTRIP_VREFRESH_PARITY_V1 */
static atomic_t a52_p420_candidates_once = ATOMIC_INIT(0);

'''
    text = text[:pos] + helper + text[pos:]

    # First Composer lookup after the outbound fix: preserve up to 8 actual
    # cached panel tuples so a residual mismatch cannot remain ambiguous.
    arm = '''\t*out_mode = NULL;
'''
    arm_new = '''\t*out_mode = NULL;
'''
    text = one(text, arm, arm_new, "find-mode anchor")

    loop = '''\tfor (i = 0; i < count; i++) {
\t\tstruct dsi_display_mode *m = &display->modes[i];

\t\tif (cmp->timing.v_active == m->timing.v_active &&
'''
    loop_new = '''\t{
\t\tbool a52_p420_log = false;

\t\tif (a52_ackfr_phase269_is_composer_tgid(current->tgid) &&
\t\t    atomic_cmpxchg(&a52_p420_candidates_once, 0, 1) == 0) {
\t\t\ta52_p420_log = true;
\t\t\ta52_ackfr_record(
\t\t\t\t"P420 Q h=%u v=%u r=%u hs=%u ph=%u pm=%u p=%u n=%u",
\t\t\t\tcmp->timing.h_active, cmp->timing.v_active,
\t\t\t\tcmp->timing.refresh_rate, cmp->timing.sot_hs_mode,
\t\t\t\tcmp->timing.phs_mode, cmp->panel_mode,
\t\t\t\tcmp->pixel_clk_khz, count);
\t\t}

\t\tfor (i = 0; i < count; i++) {
\t\t\tstruct dsi_display_mode *m = &display->modes[i];

\t\t\tif (a52_p420_log && i < 8U)
\t\t\t\ta52_ackfr_record(
\t\t\t\t\t"P420 C i=%u h=%u v=%u r=%u hs=%u ph=%u pm=%u p=%u",
\t\t\t\t\ti, m->timing.h_active, m->timing.v_active,
\t\t\t\t\tm->timing.refresh_rate, m->timing.sot_hs_mode,
\t\t\t\t\tm->timing.phs_mode, m->panel_mode,
\t\t\t\t\tm->pixel_clk_khz);

\t\t\tif (cmp->timing.v_active == m->timing.v_active &&
'''
    text = one(text, loop, loop_new, "candidate loop begin")

    tail = '''\t\t\t*out_mode = m;
\t\t\trc = 0;
\t\t\tbreak;
\t\t}
\t}
\tmutex_unlock(&display->display_lock);
'''
    tail_new = '''\t\t\t\t*out_mode = m;
\t\t\t\trc = 0;
\t\t\t\tbreak;
\t\t\t}
\t\t}
\t}
\tmutex_unlock(&display->display_lock);
'''
    text = one(text, tail, tail_new, "candidate loop close")

    text += f'\n/* {MARK}: first Composer panel-cache candidates recorded. */\n'
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    modes = (root / MODES).read_text(errors="replace")
    disp = (root / DISPLAY).read_text(errors="replace")

    if rec.count('strncmp(fmt, "P420", 4)') < 3:
        raise SystemExit("Phase420 recorder admission incomplete")

    for tok in (
        MARK,
        "P420 TO st=%d calc=%d out=%u p=%d",
        "out->vrefresh = a52_p420_stored ?",
        "a52_p420_stored : a52_p420_calc;",
    ):
        if tok not in modes:
            raise SystemExit("Phase420 drm_modes token missing: " + tok)

    for tok in (
        MARK,
        "P420 Q h=%u v=%u r=%u hs=%u ph=%u pm=%u p=%u n=%u",
        "P420 C i=%u h=%u v=%u r=%u hs=%u ph=%u pm=%u p=%u",
    ):
        if tok not in disp:
            raise SystemExit("Phase420 dsi_display token missing: " + tok)

    # Preserve prior correlation layers and keep bool masking unchanged.
    drm = (root / Path("drivers/a52_display/msm/dsi/dsi_drm.c")).read_text(errors="replace")
    enc = (root / Path("drivers/a52_display/msm/sde/sde_encoder.c")).read_text(errors="replace")
    msm = (root / Path("drivers/a52_display/msm/msm_drv.c")).read_text(errors="replace")
    atomic = (root / Path("drivers/gpu/drm/drm_atomic_helper.c")).read_text(errors="replace")
    for tok in (
        "P419 U in=%u pre=%d calc=%d out=%d",
        "P419 F h=%u v=%u r=%u hs=%u ph=%u pm=%u p=%u rc=%d",
    ):
        if tok not in (modes + drm):
            raise SystemExit("Phase420 lost Phase419 token: " + tok)
    if "P419 E3 r=%d vr=%d pv=%u" not in enc:
        raise SystemExit("Phase420 lost Phase419 E3")
    for tok in ("P418 AT in n=%u", "P418 AT out n=%u"):
        if tok not in msm:
            raise SystemExit("Phase420 lost Phase418 atomic counter: " + tok)
    if "P418 MS n=%u r=%d rt=%u" not in atomic:
        raise SystemExit("Phase420 lost Phase418 MS")
    if "P418 E n=%u s=%u r=%d id=%u rt=%u t=%d" not in enc:
        raise SystemExit("Phase420 lost Phase418 E")
    if "if (rc)\n\t\treturn rc;" not in drm:
        raise SystemExit("Phase420 bool return-rc path changed unexpectedly")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (REC, MODES, DISPLAY):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase420 source missing: " + str(rel))

    if not ns.check_only:
        for rel, fn in ((REC, patch_rec), (MODES, patch_modes), (DISPLAY, patch_display)):
            p = ns.root / rel
            p.write_text(fn(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase420 vrefresh round-trip parity: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
