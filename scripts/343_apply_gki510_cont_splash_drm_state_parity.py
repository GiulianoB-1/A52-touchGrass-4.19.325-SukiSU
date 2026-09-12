#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE343_GKI510_CONT_SPLASH_DRM_STATE_PARITY_V1"
KMS_REL = Path("drivers/a52_display/msm/sde/sde_kms.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase343 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    old = """\tcrtc->state->encoder_mask = (1 << drm_encoder_index(encoder));\n"""
    new = """\t/* A52_PHASE343_GKI510_CONT_SPLASH_DRM_STATE_PARITY_V1
\t * Qualcomm native 5.10 continuous-splash code seeds the complete
\t * connector/CRTC relationship before userspace atomic takeover.
\t * TouchGrass 4.19 only seeded encoder_mask.  On 5.10 that leaves the
\t * inherited CRTC state enabled with connector_mask == 0 and leaves the
\t * connector state detached from the CRTC.  Plane-only atomic updates
\t * duplicate that inconsistent state and can fail the generic DRM
\t * enabled/connectors invariant with -EINVAL before driver checks run.
\t */
\tcrtc->state->encoder_mask = (1 << drm_encoder_index(encoder));
\tcrtc->state->connector_mask = drm_connector_mask(connector);
\tconnector->state->crtc = crtc;
"""
    return one(text, old, new, "continuous-splash state anchor")


def validate(before: str, after: str) -> None:
    required = (
        MARK,
        "crtc->state->connector_mask = drm_connector_mask(connector);",
        "connector->state->crtc = crtc;",
        "crtc->state->encoder_mask = (1 << drm_encoder_index(encoder));",
    )
    for token in required:
        if token not in after:
            raise SystemExit("Phase343 required token missing: " + token)

    if after.count(MARK) != 1:
        raise SystemExit("Phase343 marker count is not exactly one")

    if after.count("drm_connector_mask(connector)") != \
            before.count("drm_connector_mask(connector)") + 1:
        raise SystemExit("Phase343 expected exactly one connector_mask addition")

    if after.count("connector->state->crtc = crtc;") != \
            before.count("connector->state->crtc = crtc;") + 1:
        raise SystemExit("Phase343 expected exactly one connector->state->crtc addition")

    if after.count("drm_encoder_index(encoder)") != \
            before.count("drm_encoder_index(encoder)"):
        raise SystemExit("Phase343 changed inherited encoder-mask behavior")

    protected = (
        "writel_relaxed(", "writel(", "writeq_relaxed(", "readl_relaxed(",
        "regmap_write(", "regmap_update_bits(", "DSI_W32(", "SDE_REG_WRITE(",
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(",
        "pm_runtime_get_sync(", "pm_runtime_put_sync(",
        "sde_power_resource_enable(",
        "drm_mode_config_reset(", "drm_dev_register(",
        "drm_atomic_set_mode_for_crtc(",
    )
    for token in protected:
        if after.count(token) != before.count(token):
            raise SystemExit(
                f"Phase343 changed protected behavior {token}: "
                f"{before.count(token)} -> {after.count(token)}"
            )

    if after.count("\\t") != before.count("\\t"):
        raise SystemExit("Phase343 introduced literal backslash-t text")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--before")
    ns = ap.parse_args()

    path = Path(ns.root) / KMS_REL
    if not path.is_file():
        raise SystemExit("Phase343 sde_kms.c missing")

    current = path.read_text(encoding="utf-8")

    if ns.check_only:
        if MARK not in current:
            raise SystemExit("Phase343 marker missing in check-only mode")
        if not ns.before:
            raise SystemExit("Phase343 --check-only requires --before")
        before_path = Path(ns.before)
        if not before_path.is_file():
            raise SystemExit("Phase343 before source missing")
        validate(before_path.read_text(encoding="utf-8"), current)
        print("Phase343 GKI 5.10 continuous-splash DRM state parity audit: PASS")
        return 0

    if MARK in current:
        raise SystemExit("Phase343 marker already present")
    if "sde_kms_cont_splash_config" not in current:
        raise SystemExit("Phase343 requires TouchGrass SDE continuous-splash code")
    if "A52_PHASE335_QSMMUV500_SACR_CACHE_LOCK_PARITY_V1" in current:
        # Phase335 marker is in arm-smmu.c, not sde_kms.c. This is intentionally
        # not a lineage requirement for this individual source file.
        pass

    updated = patch(current)
    validate(current, updated)
    path.write_text(updated, encoding="utf-8")
    print("Phase343 GKI 5.10 continuous-splash DRM state parity applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
