#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1"
KMS_REL = Path("drivers/a52_display/msm/sde/sde_kms.c")
CTRL_REL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase338 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_kms(text: str) -> str:
    decl_old = "static atomic_t a52_p337_check_sequence = ATOMIC_INIT(0);\n"
    decl_new = r'''static atomic_t a52_p337_check_sequence = ATOMIC_INIT(0);

/* A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1
 * Phase280 freezes ordinary recorder traffic at the exact-F0 q2 timeout.
 * Keep the latest atomic-check result, plus the latest failing result, in a
 * tiny in-memory latch so dsi_ctrl can snapshot them immediately before that
 * freeze. No DRM decision or hardware state is changed.
 */
struct a52_p338_atomic_state {
	u32 valid;
	u32 n;
	int ms;
	int zp;
	int pl;
	u32 sec_valid;
	int sec;
	u32 cs;
};

static DEFINE_SPINLOCK(a52_p338_lock);
static struct a52_p338_atomic_state a52_p338_last;
static struct a52_p338_atomic_state a52_p338_fail;

static void a52_p338_store_helper(unsigned int n, int ms, int zp, int pl,
		unsigned int cs)
{
	struct a52_p338_atomic_state s = {
		.valid = 1,
		.n = n,
		.ms = ms,
		.zp = zp,
		.pl = pl,
		.sec_valid = 0,
		.sec = 0,
		.cs = cs,
	};
	unsigned long flags;

	spin_lock_irqsave(&a52_p338_lock, flags);
	a52_p338_last = s;
	if (ms || zp || pl)
		a52_p338_fail = s;
	spin_unlock_irqrestore(&a52_p338_lock, flags);
}

static void a52_p338_store_secure(unsigned int n, int sec)
{
	unsigned long flags;

	spin_lock_irqsave(&a52_p338_lock, flags);
	if (a52_p338_last.valid && a52_p338_last.n == n) {
		a52_p338_last.sec_valid = 1;
		a52_p338_last.sec = sec;
		if (sec)
			a52_p338_fail = a52_p338_last;
	}
	spin_unlock_irqrestore(&a52_p338_lock, flags);
}

void a52_p338_emit_atomic_latch(void)
{
	struct a52_p338_atomic_state last;
	struct a52_p338_atomic_state fail;
	unsigned long flags;

	spin_lock_irqsave(&a52_p338_lock, flags);
	last = a52_p338_last;
	fail = a52_p338_fail;
	spin_unlock_irqrestore(&a52_p338_lock, flags);

	a52_ackfr_record("P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d",
		last.valid, last.n, last.ms, last.zp, last.pl,
		last.sec_valid, last.sec);
	if (fail.valid)
		a52_ackfr_record("P276 338F v=1 n=%u r=%d ms=%d zp=%d pl=%d sv=%u se=%d cs=%x",
			fail.n, fail.ms ? fail.ms : (fail.zp ? fail.zp :
			(fail.pl ? fail.pl : fail.sec)),
			fail.ms, fail.zp, fail.pl, fail.sec_valid, fail.sec, fail.cs);
}
'''
    text = one(text, decl_old, decl_new, "latch declarations")

    old = r'''\t\tif (a52_p337_n <= 32 || (a52_p337_n & 63) == 0) {
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
'''
    new = r'''\t\t{
\t\t\tunsigned int a52_p337_cs = 0;
\t\t\tint a52_p337_i;

\t\t\tfor (a52_p337_i = 0;
\t\t\t     a52_p337_i < MAX_DSI_DISPLAYS;
\t\t\t     a52_p337_i++)
\t\t\t\tif (sde_kms->splash_data
\t\t\t\t\t.splash_display[a52_p337_i]
\t\t\t\t\t.cont_splash_enabled)
\t\t\t\t\ta52_p337_cs |= 1u << a52_p337_i;

\t\t\ta52_p338_store_helper(a52_p337_n, a52_p337_ms,
\t\t\t\ta52_p337_zp, a52_p337_pl, a52_p337_cs);

\t\t\tif (a52_p337_n <= 32 || (a52_p337_n & 63) == 0) {
\t\t\t\ta52_ackfr_record("P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u",
\t\t\t\t\ta52_p337_n, a52_p337_ms, a52_p337_zp, a52_p337_pl,
\t\t\t\t\tstate->num_connector,
\t\t\t\t\tsde_kms->splash_data.num_splash_displays);

\t\t\t\tif (ret)
\t\t\t\t\ta52_ackfr_record("P276 337C n=%u cs=%x nd=%u",
\t\t\t\t\t\ta52_p337_n, a52_p337_cs,
\t\t\t\t\t\tsde_kms->splash_data.num_splash_displays);
\t\t\t}
\t\t}
'''
    text = one(text, old, new, "helper latch update")

    secure_old = r'''\tret = sde_kms_check_secure_transition(kms, state);
\t{
\t\tunsigned int a52_p337_n =
\t\t\t(unsigned int)atomic_read(&a52_p337_check_sequence);

\t\tif (a52_p337_n <= 32 || (a52_p337_n & 63) == 0)
\t\t\ta52_ackfr_record("P276 337B n=%u sec=%d",
\t\t\t\ta52_p337_n, ret);
\t}
'''
    secure_new = r'''\tret = sde_kms_check_secure_transition(kms, state);
\t{
\t\tunsigned int a52_p337_n =
\t\t\t(unsigned int)atomic_read(&a52_p337_check_sequence);

\t\ta52_p338_store_secure(a52_p337_n, ret);
\t\tif (a52_p337_n <= 32 || (a52_p337_n & 63) == 0)
\t\t\ta52_ackfr_record("P276 337B n=%u sec=%d",
\t\t\t\ta52_p337_n, ret);
\t}
'''
    return one(text, secure_old, secure_new, "secure latch update")


def patch_ctrl(text: str) -> str:
    decl_old = r'''/* A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1 */
extern void a52_ackfr_retain_timeout_snapshot(void);
'''
    decl_new = r'''/* A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1 */
extern void a52_ackfr_retain_timeout_snapshot(void);
/* A52_PHASE338_ATOMIC_LATCH_PRE_RETENTION_SNAPSHOT_V1 */
extern void a52_p338_emit_atomic_latch(void);
'''
    text = one(text, decl_old, decl_new, "dsi extern")

    call_old = r'''\t\tif (a52_p293_gdm_armed(dsi_ctrl)) {
\t\t\ta52_ackfr_record("P276 332A q=2 g=1 d=%u st=%x m=%x",
\t\t\t\t(unsigned int)a52_p276r_deep_active(), status, mask);
\t\t\ta52_ackfr_record("P276 280Z q=2");
\t\t\ta52_ackfr_retain_timeout_snapshot();
\t\t\ta52_ackfr_record("P276 332B q=2 retained=1");
\t\t}
'''
    call_new = r'''\t\tif (a52_p293_gdm_armed(dsi_ctrl)) {
\t\t\ta52_ackfr_record("P276 332A q=2 g=1 d=%u st=%x m=%x",
\t\t\t\t(unsigned int)a52_p276r_deep_active(), status, mask);
\t\t\ta52_p338_emit_atomic_latch();
\t\t\ta52_ackfr_record("P276 280Z q=2");
\t\t\ta52_ackfr_retain_timeout_snapshot();
\t\t\ta52_ackfr_record("P276 332B q=2 retained=1");
\t\t}
'''
    return one(text, call_old, call_new, "pre-retention snapshot call")


def validate(kms_before: str, kms_after: str, ctrl_before: str, ctrl_after: str) -> None:
    for token in (
        MARK,
        "a52_p338_store_helper",
        "a52_p338_store_secure",
        "void a52_p338_emit_atomic_latch(void)",
        "P276 338A v=%u n=%u ms=%d zp=%d pl=%d sv=%u se=%d",
        "P276 338F v=1 n=%u r=%d ms=%d zp=%d pl=%d sv=%u se=%d cs=%x",
    ):
        if token not in kms_after:
            raise SystemExit("Phase338 KMS token missing: " + token)
    for token in (MARK, "extern void a52_p338_emit_atomic_latch(void);",
                  "a52_p338_emit_atomic_latch();"):
        if token not in ctrl_after:
            raise SystemExit("Phase338 DSI token missing: " + token)

    for token in (
        "drm_atomic_helper_check_modeset(dev, state)",
        "drm_atomic_normalize_zpos(dev, state)",
        "drm_atomic_helper_check_planes(dev, state)",
        "drm_atomic_helper_async_check(dev, state)",
        "drm_self_refresh_helper_alter_state(state)",
        "sde_kms_check_secure_transition(kms, state)",
        "P276 337A n=%u ms=%d zp=%d pl=%d nc=%d nd=%u",
        "P276 337B n=%u sec=%d",
        "P276 337C n=%u cs=%x nd=%u",
    ):
        if kms_after.count(token) != kms_before.count(token):
            raise SystemExit("Phase338 changed inherited Phase337 token count: " + token)

    for token in (
        "a52_ackfr_retain_timeout_snapshot();",
        "P276 280Z q=2",
        "P276 332A q=2 g=1 d=%u st=%x m=%x",
        "P276 332B q=2 retained=1",
    ):
        if ctrl_after.count(token) != ctrl_before.count(token):
            raise SystemExit("Phase338 changed inherited timeout token count: " + token)

    if not (ctrl_after.index("P276 332A q=2") <
            ctrl_after.index("a52_p338_emit_atomic_latch();") <
            ctrl_after.index("P276 280Z q=2") <
            ctrl_after.index("a52_ackfr_retain_timeout_snapshot();") <
            ctrl_after.index("P276 332B q=2")):
        raise SystemExit("Phase338 pre-retention ordering invalid")

    if kms_after.count("a52_ackfr_record(") != kms_before.count("a52_ackfr_record(") + 2:
        raise SystemExit("Phase338 expected exactly two new KMS recorder calls")
    if ctrl_after.count("a52_ackfr_record(") != ctrl_before.count("a52_ackfr_record("):
        raise SystemExit("Phase338 unexpectedly changed DSI recorder call count")

    if kms_after.count("\\t") != kms_before.count("\\t"):
        raise SystemExit("Phase338 introduced literal backslash-t text in KMS C")
    if ctrl_after.count("\\t") != ctrl_before.count("\\t"):
        raise SystemExit("Phase338 introduced literal backslash-t text in DSI C")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = Path(ns.root)
    kms_path = root / KMS_REL
    ctrl_path = root / CTRL_REL
    if not kms_path.is_file() or not ctrl_path.is_file():
        raise SystemExit("Phase338 source missing")

    kms_before = kms_path.read_text(encoding="utf-8")
    ctrl_before = ctrl_path.read_text(encoding="utf-8")

    if MARK in kms_before and MARK in ctrl_before:
        for token in (
            "a52_p338_store_helper", "a52_p338_store_secure",
            "void a52_p338_emit_atomic_latch(void)",
            "a52_p338_emit_atomic_latch();",
            "P276 338A v=%u", "P276 338F v=1",
        ):
            if token not in (kms_before + ctrl_before):
                raise SystemExit("Phase338 check-only token missing: " + token)
        print("Phase338 atomic latch pre-retention snapshot audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase338 marker missing in check-only mode")
    if "A52_PHASE337_ATOMIC_CHECK_STAGE_PROBE_V1" not in kms_before:
        raise SystemExit("Phase338 requires Phase337 KMS source")
    if "A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1" not in ctrl_before:
        raise SystemExit("Phase338 requires Phase332+ timeout source")

    kms_after = patch_kms(kms_before)
    ctrl_after = patch_ctrl(ctrl_before)
    validate(kms_before, kms_after, ctrl_before, ctrl_after)
    kms_path.write_text(kms_after, encoding="utf-8")
    ctrl_path.write_text(ctrl_after, encoding="utf-8")
    print("Phase338 atomic latch pre-retention snapshot applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
