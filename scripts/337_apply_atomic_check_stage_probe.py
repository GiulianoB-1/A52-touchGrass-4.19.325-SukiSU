#!/usr/bin/env python3
"""Phase337 - DRM atomic-check stage probe.

Names the stage of the atomic-check chain that produces the persistent -EINVAL
observed on every composer atomic check in the Phase335 capture.

Evidence this phase exists to explain (Phase335 ramoops, RS48+CRC32C decoded):

    335  P276 296A x r=-22   HwBinder:1098_2   279.8 s -> 293.9 s
      1  P276 296A x r=0     suspend-service   293940.477 ms
         P276 314H ... sp=1                    293974.547 ms
         P276 316C q=0 ...                     293999    ms

Every composer atomic check fails, is_cont_splash_enabled is still 1 after five
minutes, and the exact-F0 sequence runs 59 ms after the only successful check.

Phase337 is diagnostic only. It performs a behavior-preserving decomposition of
drm_atomic_helper_check() into the exact stages that function performs, in the
same order and under the same conditions, so each stage's return code can be
recorded. It adds no register write, clock, power, reset or delay operation, and
it does not change the value returned to the caller.

Reference (drivers/gpu/drm/drm_atomic_helper.c, pinned Android 12 / Linux 5.10):

    ret = drm_atomic_helper_check_modeset(dev, state);
    if (ret) return ret;
    if (dev->mode_config.normalize_zpos) {
        ret = drm_atomic_normalize_zpos(dev, state);
        if (ret) return ret;
    }
    ret = drm_atomic_helper_check_planes(dev, state);
    if (ret) return ret;
    if (state->legacy_cursor_update)
        state->async_update = !drm_atomic_helper_async_check(dev, state);
    drm_self_refresh_helper_alter_state(state);
    return ret;

Records emitted:

    P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u
        n  = check sequence number
        ms = drm_atomic_helper_check_modeset return
        zp = drm_atomic_normalize_zpos return (0 when not normalized)
        pl = drm_atomic_helper_check_planes return
        nc = state->num_connector
        nd = sde_kms->splash_data.num_splash_displays

    P276 337B n=%u sec=%d
        sec = sde_kms_check_secure_transition return

    P276 337C n=%u cs=%x nd=%u          (only when the check failed)
        cs = bitmask of splash_display[i].cont_splash_enabled
        nd = num_splash_displays

Rate limiting: the composer issues roughly twenty checks per second and the R48
ring holds about 1028 records, so unlimited recording would wrap the window in
under a minute. The first 32 checks are recorded in full, then every 64th.
"""

from __future__ import annotations

import argparse
from pathlib import Path

KMS = Path("drivers/a52_display/msm/sde/sde_kms.c")
MARK = "A52_PHASE337_ATOMIC_CHECK_STAGE_PROBE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase337 {label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def patch_kms(text: str) -> str:
    if MARK in text:
        return text
    if "a52_ackfr_record(" not in text:
        raise SystemExit("Phase337 requires the inherited flight recorder in sde_kms.c")
    if "P276 296K p" not in text:
        raise SystemExit("Phase337 requires inherited Phase296 sde_kms instrumentation")

    # 1. Declarations for the stage helpers and the sequence counter.
    include_anchor = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    include_new = (
        "#include <linux/a52_ack_secure_flight_recorder.h>\n"
        "/* " + MARK + "\n"
        " * drm_atomic_helper_check_modeset/_check_planes/_async_check live in\n"
        " * drm_atomic_helper.h; drm_atomic_normalize_zpos lives in drm_blend.h,\n"
        " * and self-refresh state alteration lives in drm_self_refresh_helper.h.\n"
        " * These may be pulled in transitively, but Phase337 names them explicitly\n"
        " * so the decomposition cannot depend on include ordering.\n"
        " */\n"
        "#include <drm/drm_atomic_helper.h>\n"
        "#include <drm/drm_blend.h>\n"
        "#include <drm/drm_self_refresh_helper.h>\n"
        "\n"
        "static atomic_t a52_p337_check_sequence = ATOMIC_INIT(0);\n"
    )
    text = one(text, include_anchor, include_new, "stage probe declarations")

    # 2. Behavior-preserving decomposition of drm_atomic_helper_check().
    check_anchor = (
        "\tret = drm_atomic_helper_check(dev, state);\n"
        "\tif (ret)\n"
        "\t\tgoto end;\n"
    )
    check_new = r'''\t{
\t\t/* A52_PHASE337_ATOMIC_CHECK_STAGE_PROBE_V1
\t\t * Same calls, same order, same conditions and same return value
\t\t * as drm_atomic_helper_check(); only the intermediate results
\t\t * are made observable.
\t\t */
\t\tint a52_p337_ms = 0;
\t\tint a52_p337_zp = 0;
\t\tint a52_p337_pl = 0;
\t\tunsigned int a52_p337_n;

\t\ta52_p337_n = (unsigned int)atomic_inc_return(&a52_p337_check_sequence);
\t\ta52_p337_ms = drm_atomic_helper_check_modeset(dev, state);
\t\tif (!a52_p337_ms) {
\t\t\tif (dev->mode_config.normalize_zpos)
\t\t\t\ta52_p337_zp = drm_atomic_normalize_zpos(dev, state);
\t\t\tif (!a52_p337_zp) {
\t\t\t\ta52_p337_pl = drm_atomic_helper_check_planes(dev, state);
\t\t\t\tif (!a52_p337_pl && state->legacy_cursor_update)
\t\t\t\t\tstate->async_update =
\t\t\t\t\t\t!drm_atomic_helper_async_check(dev, state);
\t\t\t}
\t\t}

\t\tret = a52_p337_ms ? a52_p337_ms :
\t\t\t(a52_p337_zp ? a52_p337_zp : a52_p337_pl);

\t\tif (!ret)
\t\t\tdrm_self_refresh_helper_alter_state(state);

\t\tif (a52_p337_n <= 32 || (a52_p337_n & 63) == 0) {
\t\t\ta52_ackfr_record("P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u",
\t\t\t\ta52_p337_n, a52_p337_ms, a52_p337_zp, a52_p337_pl,
\t\t\t\tstate->num_connector,
\t\t\t\tsde_kms->splash_data.num_splash_displays);

\t\t\tif (ret) {
\t\t\t\tunsigned int a52_p337_cs = 0;
\t\t\t\tint a52_p337_i;

\t\t\t\tfor (a52_p337_i = 0;
\t\t\t\t     a52_p337_i < MAX_DSI_DISPLAYS;
\t\t\t\t     a52_p337_i++)
\t\t\t\t\tif (sde_kms->splash_data
\t\t\t\t\t\t.splash_display[a52_p337_i]
\t\t\t\t\t\t.cont_splash_enabled)
\t\t\t\t\t\ta52_p337_cs |= 1u << a52_p337_i;

\t\t\t\ta52_ackfr_record("P276 337C n=%u cs=%x nd=%u",
\t\t\t\t\ta52_p337_n, a52_p337_cs,
\t\t\t\t\tsde_kms->splash_data.num_splash_displays);
\t\t\t}
\t\t}
\t}
\tif (ret)
\t\tgoto end;
'''
    text = one(text, check_anchor, check_new, "atomic helper check decomposition")

    # 3. Secure-transition return code.
    secure_anchor = (
        "\tret = sde_kms_check_secure_transition(kms, state);\n"
        "end:\n"
    )
    secure_new = (
        "\tret = sde_kms_check_secure_transition(kms, state);\n"
        "\t{\n"
        "\t\tunsigned int a52_p337_n =\n"
        "\t\t\t(unsigned int)atomic_read(&a52_p337_check_sequence);\n"
        "\n"
        "\t\tif (a52_p337_n <= 32 || (a52_p337_n & 63) == 0)\n"
        "\t\t\ta52_ackfr_record(\"P276 337B n=%u sec=%d\",\n"
        "\t\t\t\ta52_p337_n, ret);\n"
        "\t}\n"
        "end:\n"
    )
    text = one(text, secure_anchor, secure_new, "secure transition record")
    return text


def validate(before: str, after: str) -> None:
    required = (
        MARK,
        "a52_p337_check_sequence",
        "drm_atomic_helper_check_modeset(dev, state)",
        "drm_atomic_normalize_zpos(dev, state)",
        "drm_atomic_helper_check_planes(dev, state)",
        "drm_atomic_helper_async_check(dev, state)",
        "drm_self_refresh_helper_alter_state(state)",
        "P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u",
        "P276 337B n=%u sec=%d",
        "P276 337C n=%u cs=%x nd=%u",
    )
    for token in required:
        if token not in after:
            raise SystemExit("Phase337 required token missing: " + token)

    # Phase337 is diagnostic only: no hardware-affecting operation may change.
    behavior_tokens = (
        "writel_relaxed(", "writel(", "writeq_relaxed(", "readl_relaxed(", "regmap_write(",
        "regmap_update_bits(", "DSI_W32(", "SDE_REG_WRITE(",
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(", "wmb(", "mb(",
        "pm_runtime_get_sync(", "pm_runtime_put_sync(",
        "sde_power_resource_enable(",
    )
    for token in behavior_tokens:
        if before.count(token) != after.count(token):
            raise SystemExit(
                f"Phase337 functional-scope violation {token}: "
                f"{before.count(token)} -> {after.count(token)}"
            )

    # The wrapper is replaced by exactly its own published stages.
    if after.count("drm_atomic_helper_check(dev, state)") != \
            before.count("drm_atomic_helper_check(dev, state)") - 1:
        raise SystemExit("Phase337 must replace exactly one drm_atomic_helper_check call")
    for stage in (
        "drm_atomic_helper_check_modeset(dev, state)",
        "drm_atomic_normalize_zpos(dev, state)",
        "drm_atomic_helper_check_planes(dev, state)",
        "drm_atomic_helper_async_check(dev, state)",
        "drm_self_refresh_helper_alter_state(state)",
    ):
        if after.count(stage) != before.count(stage) + 1:
            raise SystemExit(f"Phase337 must add exactly one {stage} call")

    if after.count("drm_self_refresh_helper_alter_state(state)") != \
            before.count("drm_self_refresh_helper_alter_state(state)") + 1:
        raise SystemExit(
            "Phase337 must preserve drm_atomic_helper_check self-refresh alteration"
        )

    # The secure-transition call itself must not be duplicated or removed.
    if after.count("sde_kms_check_secure_transition(kms, state)") != \
            before.count("sde_kms_check_secure_transition(kms, state)"):
        raise SystemExit("Phase337 must not change the secure transition call count")

    if after.count("a52_ackfr_record(") != before.count("a52_ackfr_record(") + 3:
        raise SystemExit("Phase337 must add exactly three recorder calls")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--before-kms", type=Path)
    ns = ap.parse_args()

    kms_path = ns.root / KMS
    if not kms_path.is_file():
        raise SystemExit("Phase337 required source missing: " + str(kms_path))

    kms = kms_path.read_text(encoding="utf-8")

    if ns.check_only:
        if not ns.before_kms or not ns.before_kms.is_file():
            raise SystemExit("Phase337 --check-only requires --before-kms")
        validate(ns.before_kms.read_text(encoding="utf-8"), kms)
        print("Phase337 DRM atomic-check stage probe audit: PASS")
        return 0

    updated = patch_kms(kms)
    if updated == kms:
        raise SystemExit("Phase337 patch produced an incomplete/no-op change")

    validate(kms, updated)
    kms_path.write_text(updated, encoding="utf-8")
    print("Phase337 DRM atomic-check stage probe applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
