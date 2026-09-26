#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE395_NOC_WALL_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
BLK = Path("block/blk-mq.c")
ICC = Path("drivers/interconnect/qcom/icc-rpmh.c")
BCM = Path("drivers/interconnect/qcom/bcm-voter.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase395 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase395 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase395 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase395 closing brace missing: {sig}")


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE394_EARLY_FIXED_LANE_V1" not in text:
        raise SystemExit("Phase395 requires Phase394 recorder lineage")

    text = one(text,
        "#define A52_R341_INTERVAL_MS 10U\n",
        "#define A52_R341_INTERVAL_MS 250U\n",
        "hardirq interval")

    # Admit sparse NoC/ICC records through both inherited gates.
    text = one(text,
        '\t       !strncmp(fmt, "I393", 4); /* A52_PHASE393_CAUSAL_CLIFF_ADMISSION_V1 */\n',
        '\t       !strncmp(fmt, "I393", 4) || /* A52_PHASE393_CAUSAL_CLIFF_ADMISSION_V1 */\n'
        '\t       !strncmp(fmt, "N395", 4); /* A52_PHASE395_NOC_WALL_V1 */\n',
        "retention admission")
    text = one(text,
        '\t    strncmp(fmt, "I393", 4))\n',
        '\t    strncmp(fmt, "I393", 4) &&\n'
        '\t    strncmp(fmt, "N395", 4))\n',
        "focused admission")

    marker = '\nstatic const char a52_p395_noc_wall_marker[] __used = "' + MARK + '";\n'
    anchor = 'late_initcall(a52_p394_early_init);\n'
    text = one(text, anchor, anchor + marker, "recorder marker")
    return text


def patch_blk(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE385_FREEZE_OBSERVER_V1" not in text:
        raise SystemExit("Phase395 requires Phase385 block observer")

    old = '''\tstatic const u32 target_ms[] = {
\t\t230U, 240U, 250U, 275U, 300U, 325U, 350U, 375U,
\t\t400U, 425U, 500U, 750U, 1000U, 1500U, 2000U,
\t};
'''
    new = '''\t/* A52_PHASE395_NOC_WALL_V1
\t * Keep the independent kthread observer alive through the ~31 s NoC wall.
\t * Targets are relative to the Phase383 loop49 frontier (~17.85 s).
\t */
\tstatic const u32 target_ms[] = {
\t\t230U, 240U, 250U, 275U, 300U, 325U, 350U, 375U,
\t\t400U, 425U, 500U, 750U, 1000U, 1500U, 2000U,
\t\t3000U, 5000U, 7500U, 10000U, 12500U, 15000U,
\t\t17500U, 20000U, 22500U, 25000U, 27500U, 30000U,
\t};
'''
    text = one(text, old, new, "extended P385 targets")

    old = '''\twhile (!kthread_should_stop() && next < ARRAY_SIZE(target_ms)) {
\t\telapsed_ms = div_u64(ktime_get_boottime_ns() - start_ns,
\t\t\t\t    NSEC_PER_MSEC);
\t\tif (elapsed_ms >= target_ms[next]) {
\t\t\ta52_r385_sample_freezes(next, elapsed_ms);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
\t\tif (msleep_interruptible(2) && kthread_should_stop())
\t\t\tbreak;
\t}
\treturn 0;
'''
    new = '''\twhile (!kthread_should_stop() && next < ARRAY_SIZE(target_ms)) {
\t\telapsed_ms = div_u64(ktime_get_boottime_ns() - start_ns,
\t\t\t\t    NSEC_PER_MSEC);
\t\tif (elapsed_ms >= target_ms[next]) {
\t\t\ta52_r385_sample_freezes(next, elapsed_ms);
\t\t\tnext++;
\t\t\tcontinue;
\t\t}
\t\tif (msleep_interruptible(2) && kthread_should_stop())
\t\t\tbreak;
\t}
\telapsed_ms = div_u64(ktime_get_boottime_ns() - start_ns,
\t\t\t    NSEC_PER_MSEC);
\ta52_ackfr_record("N395 KDONE n=%u ms=%llu stop=%u",
\t\tnext, (unsigned long long)elapsed_ms,
\t\tkthread_should_stop() ? 1U : 0U);
\treturn 0;
'''
    return one(text, old, new, "P385 finish marker")


ICC_HELPER = r'''

/* A52_PHASE395_NOC_WALL_V1
 * Sparse process-context trace of the active SM6350 RPMh interconnect path.
 * Keep hardware behavior unchanged: qcom_icc_bcm_voter_commit() is still
 * called exactly once and qcom_icc_set() still returns 0 as before.
 */
static atomic_t a52_p395_icc_count = ATOMIC_INIT(0);

static bool a52_p395_icc_window(u64 *ms, unsigned int *n)
{
	u64 now = ktime_get_boottime_ns();
	u64 now_ms = div_u64(now, NSEC_PER_MSEC);
	unsigned int id;

	if (now_ms < 14000ULL || now_ms > 45000ULL)
		return false;
	id = (unsigned int)atomic_inc_return(&a52_p395_icc_count);
	if (id > 384U)
		return false;
	*ms = now_ms;
	*n = id;
	return true;
}
'''


def patch_icc(text: str) -> str:
    if MARK in text:
        return text

    inc_anchor = '#include <linux/interconnect-provider.h>\n'
    if '#include <linux/ktime.h>\n' not in text:
        text = one(text, inc_anchor,
                   inc_anchor + '#include <linux/ktime.h>\n'
                   '#include <linux/a52_ack_secure_flight_recorder.h>\n',
                   "ICC includes")

    marker = '#include "icc-rpmh.h"\n'
    text = one(text, marker, marker + ICC_HELPER, "ICC helper")

    sig = "int qcom_icc_set(struct icc_node *src, struct icc_node *dst)"
    start, end = function_bounds(text, sig)
    fn = text[start:end]

    old = '''\tstruct qcom_icc_provider *qp;
\tstruct icc_node *node;
'''
    new = '''\tstruct qcom_icc_provider *qp;
\tstruct icc_node *node;
\tu64 a52_ms = 0;
\tunsigned int a52_n = 0;
\tint a52_ret;
\tbool a52_trace;
'''
    fn = one(fn, old, new, "ICC local state")

    old = '''\tqp = to_qcom_provider(node->provider);

\tqcom_icc_bcm_voter_commit(qp->voter);

\treturn 0;
'''
    new = '''\tqp = to_qcom_provider(node->provider);
\ta52_trace = a52_p395_icc_window(&a52_ms, &a52_n);
\tif (a52_trace)
\t\ta52_ackfr_record("N395 ICC e n=%u ms=%llu dev=%s src=%s dst=%s",
\t\t\ta52_n, (unsigned long long)a52_ms,
\t\t\tnode->provider && node->provider->dev ?
\t\t\t\tdev_name(node->provider->dev) : "-",
\t\t\tsrc && src->name ? src->name : "-",
\t\t\tdst && dst->name ? dst->name : "-");

\ta52_ret = qcom_icc_bcm_voter_commit(qp->voter);

\tif (a52_trace)
\t\ta52_ackfr_record("N395 ICC x n=%u ret=%d", a52_n, a52_ret);
\treturn 0;
'''
    fn = one(fn, old, new, "ICC commit wrapper")
    return text[:start] + fn + text[end:]


def patch_bcm(text: str) -> str:
    if MARK in text:
        return text

    inc_anchor = '#include <linux/interconnect-provider.h>\n'
    if '#include <linux/ktime.h>\n' not in text:
        text = one(text, inc_anchor,
                   inc_anchor + '#include <linux/ktime.h>\n'
                   '#include <linux/a52_ack_secure_flight_recorder.h>\n',
                   "BCM includes")

    sig = "int qcom_icc_bcm_voter_commit(struct bcm_voter *voter)"
    start, end = function_bounds(text, sig)
    fn = text[start:end]

    old = '''\tint commit_idx[MAX_VCD + 1];
\tstruct tcs_cmd cmds[MAX_BCMS];
\tint ret = 0;
'''
    new = '''\tint commit_idx[MAX_VCD + 1];
\tstruct tcs_cmd cmds[MAX_BCMS];
\tint ret = 0;
\tu64 a52_ms;
\tbool a52_trace;
'''
    fn = one(fn, old, new, "BCM local state")

    old = '''\tif (!voter)
\t\treturn 0;

\tmutex_lock(&voter->lock);
'''
    new = '''\tif (!voter)
\t\treturn 0;

\ta52_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
\ta52_trace = a52_ms >= 14000ULL && a52_ms <= 45000ULL;
\tif (a52_trace)
\t\ta52_ackfr_record("N395 BCM e ms=%llu dev=%s empty=%u",
\t\t\t(unsigned long long)a52_ms,
\t\t\tvoter->dev ? dev_name(voter->dev) : "-",
\t\t\tlist_empty(&voter->commit_list) ? 1U : 0U);

\tmutex_lock(&voter->lock);
'''
    fn = one(fn, old, new, "BCM entry")

    replacements = [
        ("RPMH_ACTIVE_ONLY_STATE", "A"),
        ("RPMH_WAKE_ONLY_STATE", "W"),
        ("RPMH_SLEEP_STATE", "S"),
    ]
    for state, tag in replacements:
        needle = f'''\tret = rpmh_write_batch(voter->dev, {state},\n\t\t\t       cmds, commit_idx);\n'''
        if needle not in fn:
            # Android 5.10 may use one-line formatting.
            needle = f'''\tret = rpmh_write_batch(voter->dev, {state}, cmds, commit_idx);\n'''
        if needle not in fn:
            raise SystemExit(f"Phase395 BCM {state} write anchor missing")
        repl = needle + (
            f'\tif (a52_trace)\n'
            f'\t\ta52_ackfr_record("N395 BCM {tag} ms=%llu n0=%d a0=%x d0=%x ret=%d",\n'
            f'\t\t\t(unsigned long long)a52_ms, commit_idx[0],\n'
            f'\t\t\tcommit_idx[0] ? cmds[0].addr : 0U,\n'
            f'\t\t\tcommit_idx[0] ? cmds[0].data : 0U, ret);\n'
        )
        fn = fn.replace(needle, repl, 1)

    old = '''\tmutex_unlock(&voter->lock);
\treturn ret;
'''
    new = '''\tmutex_unlock(&voter->lock);
\tif (a52_trace)
\t\ta52_ackfr_record("N395 BCM x ms=%llu ret=%d",
\t\t\t(unsigned long long)a52_ms, ret);
\treturn ret;
'''
    fn = one(fn, old, new, "BCM exit")

    # Put a source marker outside the function so workflow audits are simple.
    text = text[:start] + fn + text[end:]
    anchor = 'static LIST_HEAD(bcm_voters);\n'
    text = one(text, anchor,
               anchor + 'static const char a52_p395_bcm_marker[] __used = "' + MARK + '";\n',
               "BCM marker")
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text()
    blk = (root / BLK).read_text()
    icc = (root / ICC).read_text()
    bcm = (root / BCM).read_text()

    for token in (
        MARK,
        "#define A52_R341_INTERVAL_MS 250U",
        '!strncmp(fmt, "N395", 4)',
    ):
        if token not in rec:
            raise SystemExit("Phase395 recorder token missing: " + token)

    for token in (
        MARK,
        "30000U",
        "N395 KDONE n=%u ms=%llu stop=%u",
    ):
        if token not in blk:
            raise SystemExit("Phase395 block token missing: " + token)

    for token in (
        MARK,
        "N395 ICC e n=%u ms=%llu dev=%s src=%s dst=%s",
        "a52_ret = qcom_icc_bcm_voter_commit(qp->voter);",
        "N395 ICC x n=%u ret=%d",
    ):
        if token not in icc:
            raise SystemExit("Phase395 ICC token missing: " + token)

    for token in (
        MARK,
        "N395 BCM e ms=%llu dev=%s empty=%u",
        "N395 BCM A ms=%llu n0=%d a0=%x d0=%x ret=%d",
        "N395 BCM W ms=%llu n0=%d a0=%x d0=%x ret=%d",
        "N395 BCM S ms=%llu n0=%d a0=%x d0=%x ret=%d",
        "N395 BCM x ms=%llu ret=%d",
    ):
        if token not in bcm:
            raise SystemExit("Phase395 BCM token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root
    for rel in (REC, BLK, ICC, BCM):
        if not (root / rel).is_file():
            raise SystemExit("Phase395 source missing: " + str(rel))

    if not ns.check_only:
        p = root / REC; p.write_text(patch_rec(p.read_text()))
        p = root / BLK; p.write_text(patch_blk(p.read_text()))
        p = root / ICC; p.write_text(patch_icc(p.read_text()))
        p = root / BCM; p.write_text(patch_bcm(p.read_text()))

    validate(root)
    print("Phase395 NoC wall discriminator: extended IRQ/kthread + ICC/BCM trace: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
