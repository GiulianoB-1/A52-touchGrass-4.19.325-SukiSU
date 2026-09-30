#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE419_UMODE_VREFRESH_PARITY_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MODES = Path("drivers/gpu/drm/drm_modes.c")
DRM = Path("drivers/a52_display/msm/dsi/dsi_drm.c")
ENCODER = Path("drivers/a52_display/msm/sde/sde_encoder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase419 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1" not in text:
        raise SystemExit("Phase419 requires Phase418 recorder")

    text = one(
        text,
        'if (strncmp(fmt, "P418", 4) &&\n',
        'if (strncmp(fmt, "P419", 4) &&\n'
        '    strncmp(fmt, "P418", 4) &&\n',
        "first P419 admission gate",
    )
    text = one(
        text,
        '\tif (!fmt || (\n\t    strncmp(fmt, "P418", 4) &&\n',
        '\tif (!fmt || (\n\t    strncmp(fmt, "P419", 4) &&\n'
        '\t    strncmp(fmt, "P418", 4) &&\n',
        "second P419 admission gate",
    )
    text = one(
        text,
        'if (unlikely(atomic_read(&a52_r280_retained)) &&\n'
        '\t    strncmp(fmt, "P418", 4) &&\n',
        'if (unlikely(atomic_read(&a52_r280_retained)) &&\n'
        '\t    strncmp(fmt, "P419", 4) &&\n'
        '\t    strncmp(fmt, "P418", 4) &&\n',
        "P419 retention bypass",
    )
    text += f'\n/* {MARK}: P419 one-shot evidence admitted to sequential disk tier. */\n'
    return text


def patch_modes(text: str) -> str:
    if MARK in text:
        return text

    include_anchor = '#include <linux/export.h>\n'
    include_new = (
        include_anchor
        + '#include <linux/atomic.h>\n'
        + '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    )
    text = one(text, include_anchor, include_new, "drm_modes includes")

    fn_anchor = '''int drm_mode_convert_umode(struct drm_device *dev,
			   struct drm_display_mode *out,
			   const struct drm_mode_modeinfo *in)
{
'''
    fn_new = '''/* A52_PHASE419_UMODE_VREFRESH_PARITY_V1
 * TouchGrass 4.19 preserves UAPI vrefresh here. Android 5.10 does not.
 * Record the old value once, then restore it with a timing-derived fallback.
 */
static atomic_t a52_p419_umode_once = ATOMIC_INIT(0);
extern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);

int drm_mode_convert_umode(struct drm_device *dev,
			   struct drm_display_mode *out,
			   const struct drm_mode_modeinfo *in)
{
'''
    text = one(text, fn_anchor, fn_new, "umode function marker")

    anchor = '''	out->vtotal = in->vtotal;
	out->vscan = in->vscan;
	out->flags = in->flags;
'''
    replacement = '''	out->vtotal = in->vtotal;
	out->vscan = in->vscan;
	out->flags = in->flags;
	{
		int a52_p419_pre = out->vrefresh;
		int a52_p419_calc = drm_mode_vrefresh(out);

		out->vrefresh = in->vrefresh ? in->vrefresh : a52_p419_calc;
		if (atomic_cmpxchg(&a52_p419_umode_once, 0, 1) == 0)
			a52_ackfr_record(
				"P419 U in=%u pre=%d calc=%d out=%d",
				in->vrefresh, a52_p419_pre,
				a52_p419_calc, out->vrefresh);
	}
'''
    text = one(text, anchor, replacement, "vrefresh parity treatment")
    text += f'\n/* {MARK}: drm_mode_convert_umode vrefresh parity active. */\n'
    return text


def patch_drm(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1" not in text:
        raise SystemExit("Phase419 requires Phase418 dsi_drm source")
    if "dsi_mode->timing.refresh_rate = drm_mode->vrefresh;" not in text:
        raise SystemExit("Phase419 expected convert_to_dsi_mode vrefresh read missing")

    default_anchor = '''static struct dsi_display_mode_priv_info default_priv_info = {
	.panel_jitter_numer = DEFAULT_PANEL_JITTER_NUMERATOR,
	.panel_jitter_denom = DEFAULT_PANEL_JITTER_DENOMINATOR,
	.panel_prefill_lines = DEFAULT_PANEL_PREFILL_LINES,
	.dsc_enabled = false,
};
'''
    default_new = default_anchor + '''
/* A52_PHASE419_UMODE_VREFRESH_PARITY_V1 */
static atomic_t a52_p419_find_once = ATOMIC_INIT(0);
'''
    text = one(text, default_anchor, default_new, "find-mode one-shot state")

    anchor = '''	rc = dsi_display_find_mode(display, &dsi_mode, &panel_dsi_mode);
	if (rc)
		return rc;
'''
    replacement = '''	rc = dsi_display_find_mode(display, &dsi_mode, &panel_dsi_mode);
	if (atomic_cmpxchg(&a52_p419_find_once, 0, 1) == 0)
		a52_ackfr_record(
			"P419 F h=%u v=%u r=%u pm=%u p=%u rc=%d",
			dsi_mode.timing.h_active, dsi_mode.timing.v_active,
			dsi_mode.timing.refresh_rate, dsi_mode.panel_mode,
			dsi_mode.pixel_clk_khz, rc);
	if (rc)
		return rc;
'''
    text = one(text, anchor, replacement, "find-mode tuple result")
    text += f'\n/* {MARK}: find-mode tuple one-shot active. */\n'
    return text


def patch_encoder(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1" not in text:
        raise SystemExit("Phase419 requires Phase418 encoder source")

    seq_anchor = 'static atomic_t a52_p418_enc_seq = ATOMIC_INIT(0);\n'
    seq_new = seq_anchor + 'static atomic_t a52_p419_step3_once = ATOMIC_INIT(0);\n'
    text = one(text, seq_anchor, seq_new, "step3 one-shot state")

    anchor = '''		ret = sde_connector_get_mode_info(&sde_conn->base,
				adj_mode, &sde_conn_state->mode_info);
		a52_p418_enc_note(a52_p418_n, 3U, ret, drm_enc,
			crtc_state, sde_conn_state);
		if (ret) {
'''
    replacement = '''		ret = sde_connector_get_mode_info(&sde_conn->base,
				adj_mode, &sde_conn_state->mode_info);
		a52_p418_enc_note(a52_p418_n, 3U, ret, drm_enc,
			crtc_state, sde_conn_state);
		if (atomic_cmpxchg(&a52_p419_step3_once, 0, 1) == 0)
			a52_ackfr_record("P419 E3 r=%d vr=%d pv=%u",
				ret, adj_mode->vrefresh,
				adj_mode->private ? 1U : 0U);
		if (ret) {
'''
    text = one(text, anchor, replacement, "step3 result")
    text += f'\n/* {MARK}: step3 one-shot active. */\n'
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    modes = (root / MODES).read_text(errors="replace")
    drm = (root / DRM).read_text(errors="replace")
    enc = (root / ENCODER).read_text(errors="replace")

    if rec.count('strncmp(fmt, "P419", 4)') < 3:
        raise SystemExit("Phase419 P419 recorder admission/retention bypass incomplete")
    for tok in (
        MARK,
        'out->vrefresh = in->vrefresh ? in->vrefresh : a52_p419_calc;',
        'P419 U in=%u pre=%d calc=%d out=%d',
        'drm_mode_vrefresh(out)',
    ):
        if tok not in modes:
            raise SystemExit("Phase419 drm_modes token missing: " + tok)

    for tok in (
        MARK,
        'dsi_mode->timing.refresh_rate = drm_mode->vrefresh;',
        'P419 F h=%u v=%u r=%u pm=%u p=%u rc=%d',
        'dsi_display_find_mode(display, &dsi_mode, &panel_dsi_mode);',
    ):
        if tok not in drm:
            raise SystemExit("Phase419 dsi_drm token missing: " + tok)

    for tok in (
        MARK,
        'P419 E3 r=%d vr=%d pv=%u',
        'P418 E n=%u s=%u r=%d id=%u rt=%u t=%d',
    ):
        if tok not in enc:
            raise SystemExit("Phase419 encoder token missing: " + tok)

    # Phase418 counters must remain intact.
    msm = (root / Path("drivers/a52_display/msm/msm_drv.c")).read_text(errors="replace")
    atomic = (root / Path("drivers/gpu/drm/drm_atomic_helper.c")).read_text(errors="replace")
    for tok in ("P418 AT in n=%u", "P418 AT out n=%u"):
        if tok not in msm:
            raise SystemExit("Phase419 lost Phase418 atomic counter: " + tok)
    if "P418 MS n=%u r=%d rt=%u" not in atomic:
        raise SystemExit("Phase419 lost Phase418 modeset counter")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (REC, MODES, DRM, ENCODER):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase419 source missing: " + str(rel))

    if not ns.check_only:
        for rel, fn in (
            (REC, patch_rec),
            (MODES, patch_modes),
            (DRM, patch_drm),
            (ENCODER, patch_encoder),
        ):
            p = ns.root / rel
            p.write_text(fn(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase419 UAPI vrefresh parity + one-shot correlation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
