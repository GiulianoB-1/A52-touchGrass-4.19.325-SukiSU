#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE398_ENCODER_ATOMIC_SPLIT_V1"
ENC = Path("drivers/a52_display/msm/sde/sde_encoder.c")
REC_INCLUDE = "#include <linux/a52_ack_secure_flight_recorder.h>\n"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase398 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch_encoder(text: str) -> str:
    if MARK in text:
        return text

    before_hw = {token: text.count(token) for token in (
        "writel(", "writel_relaxed(", "regmap_write(", "regmap_update_bits(",
        "clk_set_rate(", "clk_prepare_enable(", "clk_disable_unprepare(",
        "regulator_enable(", "regulator_disable(", "reset_control_assert(",
        "reset_control_deassert(", "msleep(", "usleep_range(", "udelay(",
        "sde_rm_reserve(", "sde_rm_update_topology(",
    )}

    if REC_INCLUDE not in text:
        text = one(
            text,
            "#include <linux/sde_rsc.h>\n",
            "#include <linux/sde_rsc.h>\n" + REC_INCLUDE,
            "recorder include",
        )

    marker_anchor = "#define NUM_PHYS_ENCODER_TYPES 2\n"
    marker_block = """/* A52_PHASE398_ENCODER_ATOMIC_SPLIT_V1
 * Phase397 proved the old NoC branch is gone.  The remaining repeated
 * compositor -EINVAL is Phase394 mode_fixup site 1: the SDE encoder helper's
 * atomic_check.  Split that function and its two nested helpers without
 * changing return values, state, resource allocation or hardware programming.
 */
static atomic_t a52_p398_seq = ATOMIC_INIT(0);

static bool a52_p398_log(unsigned int n)
{
	return n <= 32U || !(n & 63U);
}

static void a52_p398_fail(unsigned int stage, int ret)
{
	unsigned int n = (unsigned int)atomic_read(&a52_p398_seq);

	if (a52_p398_log(n))
		a52_ackfr_record("P398 F n=%u s=%u r=%d", n, stage, ret);
}

"""
    text = one(text, marker_anchor, marker_block + marker_anchor, "marker/state")

    # Physical encoder sub-check: identify whether a phys encoder callback is
    # responsible and whether it used atomic_check or legacy mode_fixup.
    old = """		if (phys && phys->ops.atomic_check)
			ret = phys->ops.atomic_check(phys, crtc_state,
					conn_state);
		else if (phys && phys->ops.mode_fixup)
			if (!phys->ops.mode_fixup(phys, mode, adj_mode))
				ret = -EINVAL;

		if (ret) {
			SDE_ERROR_ENC(sde_enc,
					"mode unsupported, phys idx %d\\n", i);
			break;
		}
"""
    new = """		if (phys && phys->ops.atomic_check)
			ret = phys->ops.atomic_check(phys, crtc_state,
					conn_state);
		else if (phys && phys->ops.mode_fixup)
			if (!phys->ops.mode_fixup(phys, mode, adj_mode))
				ret = -EINVAL;

		if (ret) {
			unsigned int a52_n =
				(unsigned int)atomic_read(&a52_p398_seq);
			if (a52_p398_log(a52_n))
				a52_ackfr_record(
					"P398 P n=%u i=%d r=%d im=%u sr=%u ac=%u mf=%u",
					a52_n, i, ret,
					phys ? (unsigned int)phys->intf_mode : 99U,
					phys ? (unsigned int)phys->split_role : 99U,
					phys && phys->ops.atomic_check ? 1U : 0U,
					phys && phys->ops.mode_fixup ? 1U : 0U);
			SDE_ERROR_ENC(sde_enc,
					"mode unsupported, phys idx %d\\n", i);
			break;
		}
"""
    text = one(text, old, new, "physical encoder failure split")

    # PU ROI failures: preserve behavior, capture whether connector or CRTC ROI
    # is the source.
    old = """				ret = -EINVAL;
			}
		}

		if (sde_crtc_state->user_roi_list.num_rects) {
"""
    new = """				ret = -EINVAL;
				if (a52_p398_log((unsigned int)atomic_read(&a52_p398_seq)))
					a52_ackfr_record(
						"P398 U n=%u s=0 r=%d nr=%u mw=%d mh=%d rw=%d rh=%d",
						(unsigned int)atomic_read(&a52_p398_seq), ret,
						sde_conn_state->rois.num_rects,
						mode_roi.w, mode_roi.h, roi.w, roi.h);
			}
		}

		if (sde_crtc_state->user_roi_list.num_rects) {
"""
    text = one(text, old, new, "connector ROI split")

    old = """				ret = -EINVAL;
			}
		}
	}

	return ret;
}
"""
    new = """				ret = -EINVAL;
				if (a52_p398_log((unsigned int)atomic_read(&a52_p398_seq)))
					a52_ackfr_record(
						"P398 U n=%u s=1 r=%d nr=%u mw=%d mh=%d rw=%d rh=%d",
						(unsigned int)atomic_read(&a52_p398_seq), ret,
						sde_crtc_state->user_roi_list.num_rects,
						mode_roi.w, mode_roi.h, roi.w, roi.h);
			}
		}
	}

	return ret;
}
"""
    text = one(text, old, new, "CRTC ROI split")

    # Reserve helper internal failures.
    replacements = [
        (
""" 		if (ret) {
			SDE_ERROR_ENC(sde_enc,
				"failed to get mode info, rc = %d\\n", ret);
			return ret;
		}
""".lstrip(),
""" 		if (ret) {
			if (a52_p398_log((unsigned int)atomic_read(&a52_p398_seq)))
				a52_ackfr_record("P398 R n=%u s=0 r=%d",
					(unsigned int)atomic_read(&a52_p398_seq), ret);
			SDE_ERROR_ENC(sde_enc,
				"failed to get mode info, rc = %d\\n", ret);
			return ret;
		}
""".lstrip(),
"reserve mode-info"),
        (
"""			ret = -EINVAL;
			return ret;
		}
""",
"""			ret = -EINVAL;
			if (a52_p398_log((unsigned int)atomic_read(&a52_p398_seq)))
				a52_ackfr_record("P398 R n=%u s=1 r=%d ct=%u cr=%u",
					(unsigned int)atomic_read(&a52_p398_seq), ret,
					(unsigned int)sde_conn_state->mode_info.comp_info.comp_type,
					(unsigned int)sde_conn_state->mode_info.comp_info.comp_ratio);
			return ret;
		}
""",
"reserve compression"),
        (
"""		if (ret) {
			SDE_ERROR_ENC(sde_enc,
				"RM failed to reserve resources, rc = %d\\n",
				ret);
			return ret;
		}
""",
"""		if (ret) {
			if (a52_p398_log((unsigned int)atomic_read(&a52_p398_seq)))
				a52_ackfr_record("P398 R n=%u s=2 r=%d",
					(unsigned int)atomic_read(&a52_p398_seq), ret);
			SDE_ERROR_ENC(sde_enc,
				"RM failed to reserve resources, rc = %d\\n",
				ret);
			return ret;
		}
""",
"reserve RM"),
        (
"""		if (ret) {
			SDE_ERROR_ENC(sde_enc,
				"RM failed to update topology, rc: %d\\n", ret);
			return ret;
		}
""",
"""		if (ret) {
			if (a52_p398_log((unsigned int)atomic_read(&a52_p398_seq)))
				a52_ackfr_record("P398 R n=%u s=3 r=%d",
					(unsigned int)atomic_read(&a52_p398_seq), ret);
			SDE_ERROR_ENC(sde_enc,
				"RM failed to update topology, rc: %d\\n", ret);
			return ret;
		}
""",
"reserve topology"),
        (
"""		if (ret) {
			SDE_ERROR_ENC(sde_enc,
				"connector failed to update info, rc: %d\\n",
				ret);
			return ret;
		}
""",
"""		if (ret) {
			if (a52_p398_log((unsigned int)atomic_read(&a52_p398_seq)))
				a52_ackfr_record("P398 R n=%u s=4 r=%d",
					(unsigned int)atomic_read(&a52_p398_seq), ret);
			SDE_ERROR_ENC(sde_enc,
				"connector failed to update info, rc: %d\\n",
				ret);
			return ret;
		}
""",
"reserve blob"),
    ]
    for old_r, new_r, label in replacements:
        text = one(text, old_r, new_r, label)

    # Main encoder helper stages.
    decl_anchor = """	int ret = 0;
	bool qsync_dirty = false, has_modeset = false;

	if (!drm_enc || !crtc_state || !conn_state) {
"""
    decl_new = """	int ret = 0;
	bool qsync_dirty = false, has_modeset = false;
	unsigned int a52_p398_n =
		(unsigned int)atomic_inc_return(&a52_p398_seq);

	if (!drm_enc || !crtc_state || !conn_state) {
"""
    text = one(text, decl_anchor, decl_new, "main sequence")

    invalid_anchor = """		SDE_ERROR("invalid arg(s), drm_enc %d, crtc/conn state %d/%d\\n",
				!drm_enc, !crtc_state, !conn_state);
		return -EINVAL;
	}
"""
    invalid_new = """		SDE_ERROR("invalid arg(s), drm_enc %d, crtc/conn state %d/%d\\n",
				!drm_enc, !crtc_state, !conn_state);
		if (a52_p398_log(a52_p398_n))
			a52_ackfr_record("P398 F n=%u s=6 r=%d",
				a52_p398_n, -EINVAL);
		return -EINVAL;
	}
"""
    text = one(text, invalid_anchor, invalid_new, "invalid argument stage")

    evt_anchor = """	SDE_EVT32(DRMID(drm_enc), crtc_state->mode_changed,
		crtc_state->active_changed, crtc_state->connectors_changed);

	ret = _sde_encoder_atomic_check_phys_enc(sde_enc, crtc_state,
"""
    evt_new = """	SDE_EVT32(DRMID(drm_enc), crtc_state->mode_changed,
		crtc_state->active_changed, crtc_state->connectors_changed);
	if (a52_p398_log(a52_p398_n))
		a52_ackfr_record(
			"P398 E n=%u e=%u m=%u a=%u x=%u w=%u h=%u v=%u",
			a52_p398_n, drm_enc->base.id,
			crtc_state->mode_changed, crtc_state->active_changed,
			crtc_state->connectors_changed,
			adj_mode->hdisplay, adj_mode->vdisplay, adj_mode->vrefresh);

	ret = _sde_encoder_atomic_check_phys_enc(sde_enc, crtc_state,
"""
    text = one(text, evt_anchor, evt_new, "main entry record")

    # Replace the five generic early returns one by one, preserving all calls.
    old = """	ret = _sde_encoder_atomic_check_phys_enc(sde_enc, crtc_state,
			conn_state);
	if (ret)
		return ret;

	ret = _sde_encoder_atomic_check_pu_roi(sde_enc, crtc_state,
			conn_state, sde_conn_state, sde_crtc_state);
	if (ret)
		return ret;
"""
    new = """	ret = _sde_encoder_atomic_check_phys_enc(sde_enc, crtc_state,
			conn_state);
	if (ret) {
		a52_p398_fail(0, ret);
		return ret;
	}

	ret = _sde_encoder_atomic_check_pu_roi(sde_enc, crtc_state,
			conn_state, sde_conn_state, sde_crtc_state);
	if (ret) {
		a52_p398_fail(1, ret);
		return ret;
	}
"""
    text = one(text, old, new, "main phys/ROI stages")

    old = """	ret = sde_connector_set_old_topology_name(conn_state, old_top);
	if (ret)
		return ret;

	ret = _sde_encoder_atomic_check_reserve(drm_enc, crtc_state,
			conn_state, sde_enc, sde_kms, sde_conn, sde_conn_state);
	if (ret)
		return ret;

	ret = sde_connector_roi_v1_check_roi(conn_state);
	if (ret) {
		SDE_ERROR_ENC(sde_enc, "connector roi check failed, rc: %d",
				ret);
		return ret;
	}
"""
    new = """	ret = sde_connector_set_old_topology_name(conn_state, old_top);
	if (ret) {
		a52_p398_fail(2, ret);
		return ret;
	}

	ret = _sde_encoder_atomic_check_reserve(drm_enc, crtc_state,
			conn_state, sde_enc, sde_kms, sde_conn, sde_conn_state);
	if (ret) {
		a52_p398_fail(3, ret);
		return ret;
	}

	ret = sde_connector_roi_v1_check_roi(conn_state);
	if (ret) {
		a52_p398_fail(4, ret);
		SDE_ERROR_ENC(sde_enc, "connector roi check failed, rc: %d",
				ret);
		return ret;
	}
"""
    text = one(text, old, new, "main topology/reserve/ROI stages")

    old = """	if (has_modeset && qsync_dirty &&
		!msm_is_mode_seamless_vrr(adj_mode)) {
		SDE_ERROR("invalid qsync during modeset\\n");
		return -EINVAL;
	}
"""
    new = """	if (has_modeset && qsync_dirty &&
		!msm_is_mode_seamless_vrr(adj_mode)) {
		if (a52_p398_log(a52_p398_n))
			a52_ackfr_record(
				"P398 Q n=%u hm=%u qd=%u pf=%x r=%d",
				a52_p398_n, has_modeset, qsync_dirty,
				adj_mode->private_flags, -EINVAL);
		a52_p398_fail(5, -EINVAL);
		SDE_ERROR("invalid qsync during modeset\\n");
		return -EINVAL;
	}
"""
    text = one(text, old, new, "qsync stage")

    success_anchor = """	SDE_EVT32(DRMID(drm_enc), adj_mode->flags, adj_mode->private_flags);

	return ret;
}
"""
    success_new = """	SDE_EVT32(DRMID(drm_enc), adj_mode->flags, adj_mode->private_flags);
	if (a52_p398_log(a52_p398_n))
		a52_ackfr_record("P398 X n=%u r=%d", a52_p398_n, ret);

	return ret;
}
"""
    text = one(text, success_anchor, success_new, "success record")

    after_hw = {token: text.count(token) for token in before_hw}
    if before_hw != after_hw:
        changed = [
            f"{k}:{before_hw[k]}->{after_hw[k]}"
            for k in before_hw if before_hw[k] != after_hw[k]
        ]
        raise SystemExit("Phase398 behavior-token count changed: " + ", ".join(changed))

    return text


def validate(root: Path) -> None:
    text = (root / ENC).read_text(encoding="utf-8", errors="strict")
    required = (
        MARK,
        "P398 E n=%u e=%u m=%u a=%u x=%u w=%u h=%u v=%u",
        "P398 F n=%u s=%u r=%d",
        "P398 P n=%u i=%d r=%d im=%u sr=%u ac=%u mf=%u",
        "P398 U n=%u s=0 r=%d nr=%u mw=%d mh=%d rw=%d rh=%d",
        "P398 U n=%u s=1 r=%d nr=%u mw=%d mh=%d rw=%d rh=%d",
        "P398 R n=%u s=0 r=%d",
        "P398 R n=%u s=1 r=%d ct=%u cr=%u",
        "P398 R n=%u s=2 r=%d",
        "P398 R n=%u s=3 r=%d",
        "P398 R n=%u s=4 r=%d",
        "P398 Q n=%u hm=%u qd=%u pf=%x r=%d",
        "P398 X n=%u r=%d",
        "_sde_encoder_atomic_check_phys_enc(sde_enc, crtc_state",
        "_sde_encoder_atomic_check_pu_roi(sde_enc, crtc_state",
        "_sde_encoder_atomic_check_reserve(drm_enc, crtc_state",
        "sde_connector_roi_v1_check_roi(conn_state)",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase398 token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / ENC
    if not path.is_file():
        raise SystemExit("Phase398 missing source: " + str(ENC))

    if not ns.check_only:
        original = path.read_text(encoding="utf-8", errors="strict")
        updated = patch_encoder(original)
        if updated == original:
            raise SystemExit("Phase398 patch produced no change")
        path.write_text(updated, encoding="utf-8")

    validate(ns.root)
    print("Phase398 encoder atomic split audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
