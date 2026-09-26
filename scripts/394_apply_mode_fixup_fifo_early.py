#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE394_MODE_FIXUP_FIFO_EARLY_V1"
DRM = Path("drivers/gpu/drm/drm_atomic_helper.c")
DSI = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase394 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase394 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase394 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase394 closing brace missing: {sig}")


DRM_HELPER = r'''

/* A52_PHASE394_MODE_FIXUP_FIFO_EARLY_V1
 * Phase387 already proved the persistent composer failure is stage 11:
 * mode_fixup(state) returns -EINVAL.  Bisect only the four actual failure
 * branches inside mode_fixup and include caller identity.  No DRM state or
 * return value is changed.
 */
static atomic_t a52_p394_fixup_fail_count = ATOMIC_INIT(0);
static atomic_t a52_p394_fixup_reported = ATOMIC_INIT(0);

static bool a52_p394_fixup_log(unsigned int n)
{
	return n <= 16U || !(n & 63U);
}

static void a52_p394_fixup_fail(unsigned int site, int ret, int obj,
				struct drm_crtc_state *cs)
{
	unsigned int n = (unsigned int)atomic_inc_return(&a52_p394_fixup_fail_count);

	if (!a52_p394_fixup_log(n))
		return;
	a52_ackfr_record("P276 394M f=%u s=%u r=%d p=%d t=%d c=%s o=%d e=%u a=%u cm=%x",
		n, site, ret, current->pid, current->tgid, current->comm, obj,
		cs ? cs->enable : 9U, cs ? cs->active : 9U,
		cs ? cs->connector_mask : 0U);
}

static void a52_p394_fixup_pass(void)
{
	unsigned int n = (unsigned int)atomic_read(&a52_p394_fixup_fail_count);
	unsigned int old;

	if (!n)
		return;
	old = (unsigned int)atomic_xchg(&a52_p394_fixup_reported, n);
	if (old == n)
		return;
	a52_ackfr_record("P276 394M pass f=%u p=%d t=%d c=%s",
		n, current->pid, current->tgid, current->comm);
}
'''


def patch_drm(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE387_DUAL_DISPLAY_FAULT_SPLITTER_V1" not in text:
        raise SystemExit("Phase394 requires Phase387 modeset splitter")
    if "#include <linux/sched.h>\n" not in text:
        text = one(text, "#include <linux/ktime.h>\n",
                   "#include <linux/ktime.h>\n#include <linux/sched.h>\n",
                   "DRM sched include")

    sig = "static int\nmode_fixup(struct drm_atomic_state *state)"
    start, _ = function_bounds(text, sig)
    text = text[:start] + DRM_HELPER + text[start:]
    start, end = function_bounds(text, sig)
    fn = text[start:end]

    old = '''\t\tret = drm_atomic_bridge_chain_check(bridge,\n\t\t\t\t\t\t    new_crtc_state,\n\t\t\t\t\t\t    new_conn_state);\n\t\tif (ret) {\n\t\t\tDRM_DEBUG_ATOMIC("Bridge atomic check failed\\n");\n\t\t\treturn ret;\n\t\t}\n'''
    new = '''\t\tret = drm_atomic_bridge_chain_check(bridge,\n\t\t\t\t\t\t    new_crtc_state,\n\t\t\t\t\t\t    new_conn_state);\n\t\tif (ret) {\n\t\t\tDRM_DEBUG_ATOMIC("Bridge atomic check failed\\n");\n\t\t\ta52_p394_fixup_fail(0, ret, encoder->base.id, new_crtc_state);\n\t\t\treturn ret;\n\t\t}\n'''
    fn = one(fn, old, new, "bridge atomic check failure")

    old = '''\t\t\tif (ret) {\n\t\t\t\tDRM_DEBUG_ATOMIC("[ENCODER:%d:%s] check failed\\n",\n\t\t\t\t\t\t encoder->base.id, encoder->name);\n\t\t\t\treturn ret;\n\t\t\t}\n'''
    new = '''\t\t\tif (ret) {\n\t\t\t\tDRM_DEBUG_ATOMIC("[ENCODER:%d:%s] check failed\\n",\n\t\t\t\t\t\t encoder->base.id, encoder->name);\n\t\t\t\ta52_p394_fixup_fail(1, ret, encoder->base.id, new_crtc_state);\n\t\t\t\treturn ret;\n\t\t\t}\n'''
    fn = one(fn, old, new, "encoder atomic check failure")

    old = '''\t\t\tif (!ret) {\n\t\t\t\tDRM_DEBUG_ATOMIC("[ENCODER:%d:%s] fixup failed\\n",\n\t\t\t\t\t\t encoder->base.id, encoder->name);\n\t\t\t\treturn -EINVAL;\n\t\t\t}\n'''
    new = '''\t\t\tif (!ret) {\n\t\t\t\tDRM_DEBUG_ATOMIC("[ENCODER:%d:%s] fixup failed\\n",\n\t\t\t\t\t\t encoder->base.id, encoder->name);\n\t\t\t\ta52_p394_fixup_fail(2, -EINVAL, encoder->base.id, new_crtc_state);\n\t\t\t\treturn -EINVAL;\n\t\t\t}\n'''
    fn = one(fn, old, new, "encoder mode_fixup failure")

    old = '''\t\tif (!ret) {\n\t\t\tDRM_DEBUG_ATOMIC("[CRTC:%d:%s] fixup failed\\n",\n\t\t\t\t\t crtc->base.id, crtc->name);\n\t\t\treturn -EINVAL;\n\t\t}\n'''
    new = '''\t\tif (!ret) {\n\t\t\tDRM_DEBUG_ATOMIC("[CRTC:%d:%s] fixup failed\\n",\n\t\t\t\t\t crtc->base.id, crtc->name);\n\t\t\ta52_p394_fixup_fail(3, -EINVAL, crtc->base.id, new_crtc_state);\n\t\t\treturn -EINVAL;\n\t\t}\n'''
    fn = one(fn, old, new, "CRTC mode_fixup failure")

    fn = one(fn, "\n\treturn 0;\n}",
             "\n\ta52_p394_fixup_pass();\n\treturn 0;\n}",
             "mode_fixup success")
    return text[:start] + fn + text[end:]


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE293_GKI_DMA_DONE_REFERENCE_V1" not in text:
        raise SystemExit("Phase394 requires Phase293 exact F0 matcher")

    sig = "static void a52_p293_gdm_try_arm(struct dsi_ctrl *dsi_ctrl,"
    start, end = function_bounds(text, sig)
    fn = text[start:end]
    anchor = '''\ta52_ackfr_record("P276 303 S00p p=%02x%02x%02x", p[0], p[1], p[2]);\n\ta52_p314_ctrl_prestate(dsi_ctrl);\n'''
    repl = anchor + '''\t/* A52_PHASE394_MODE_FIXUP_FIFO_EARLY_V1\n\t * Binary DSI test: this helper has already matched only controller 0,\n\t * FETCH_MEMORY, flags 0x8, type 0x29, len 3, payload F0 5A 5A.\n\t * Route only that command through the controller FIFO.\n\t */\n\ta52_ackfr_record("P276 394F route old=%x", *flags);\n\t*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;\n\t*flags |= DSI_CTRL_CMD_FIFO_STORE;\n\ta52_ackfr_record("P276 394F route new=%x", *flags);\n'''
    fn = one(fn, anchor, repl, "exact F0 FIFO reroute")
    text = text[:start] + fn + text[end:]

    result = '''\t\ta52_ackfr_record("P276 303 S08 ret=%d irq=%d in=%x st=%x", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS));\n'''
    result_new = result + '''\t\ta52_ackfr_record("P276 394F result ret=%d irq=%d in=%x st=%x", ret,\n\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_INT_CTRL),\n\t\t\tDSI_R32(&dsi_ctrl->hw, DSI_STATUS));\n'''
    text = one(text, result, result_new, "FIFO result record")
    return text


EARLY_BLOCK = r'''

/* A52_PHASE394_EARLY_FIXED_LANE_V1
 * Six non-wrapping process-context snapshots live in otherwise-unused bytes of
 * the Phase392 4 KiB metadata header.  Circular persistent records cannot
 * overwrite them.  They answer whether normal scheduled work still runs at
 * 15/20/25/30/35/40 s on boots that later reset with a TZ/NoC watchdog reason.
 */
#define A52_P394_EARLY_OFFSET 512U
#define A52_P394_EARLY_SLOTS 6U
#define A52_P394_EARLY_BYTES 128U
#define A52_P394_EARLY_MAGIC 0x34393345524c5931ULL
#define A52_P394_EARLY_COMMIT 0x394c0de5U

struct a52_p394_early_slot {
	u64 magic;
	u64 ts_ns;
	u64 r48_seq;
	u64 jiffies64;
	u64 frontier_ns;
	u32 target_ms;
	u32 cpu;
	u32 irq_sideband_index;
	u32 retained;
	u64 persistent_seq;
	u64 persistent_dropped;
	char comm[TASK_COMM_LEN];
	u32 crc32c;
	u32 commit;
	u8 reserved[32];
} __packed;

static const u32 a52_p394_early_targets[A52_P394_EARLY_SLOTS] = {
	15000U, 20000U, 25000U, 30000U, 35000U, 40000U,
};
static struct delayed_work a52_p394_early_work;
static unsigned int a52_p394_early_index;

static void a52_p394_early_schedule(void)
{
	u64 now_ms;
	u64 delay_ms;

	if (a52_p394_early_index >= A52_P394_EARLY_SLOTS)
		return;
	now_ms = div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
	if (now_ms >= a52_p394_early_targets[a52_p394_early_index])
		delay_ms = 1;
	else
		delay_ms = a52_p394_early_targets[a52_p394_early_index] - now_ms;
	schedule_delayed_work(&a52_p394_early_work,
		msecs_to_jiffies((unsigned int)delay_ms));
}

static void a52_p394_early_workfn(struct work_struct *work)
{
	struct a52_p394_early_slot slot;
	unsigned int index = a52_p394_early_index;
	int cpu;

	(void)work;
	if (index >= A52_P394_EARLY_SLOTS)
		return;

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_P394_EARLY_MAGIC;
	slot.ts_ns = ktime_get_boottime_ns();
	slot.r48_seq = (u64)atomic64_read(&a52_r179_sequence);
	slot.jiffies64 = get_jiffies_64();
	slot.frontier_ns = a52_ackfr_frontier_trigger_ns();
	slot.target_ms = a52_p394_early_targets[index];
	cpu = get_cpu();
	slot.cpu = (u32)cpu;
	put_cpu();
	slot.irq_sideband_index = (u32)atomic_read(&a52_r341_sideband_index);
	slot.retained = (u32)atomic_read(&a52_r280_retained);
	slot.persistent_seq = READ_ONCE(a52_p392_seq);
	slot.persistent_dropped = (u64)atomic64_read(&a52_p392_dropped);
	get_task_comm(slot.comm, current);
	slot.crc32c = a52_p392_crc32c(&slot,
		offsetof(struct a52_p394_early_slot, crc32c));
	slot.commit = A52_P394_EARLY_COMMIT;

	BUILD_BUG_ON(sizeof(struct a52_p394_early_slot) != A52_P394_EARLY_BYTES);
	BUILD_BUG_ON(A52_P394_EARLY_OFFSET +
		A52_P394_EARLY_SLOTS * A52_P394_EARLY_BYTES > A52_P392_HEADER_BYTES);
	if (READ_ONCE(a52_p392_base)) {
		memcpy_toio((u8 __iomem *)a52_p392_base + A52_P394_EARLY_OFFSET +
			index * A52_P394_EARLY_BYTES, &slot, sizeof(slot));
		wmb();
	}

	a52_p394_early_index++;
	a52_p394_early_schedule();
}

static int __init a52_p394_early_init(void)
{
	INIT_DELAYED_WORK(&a52_p394_early_work, a52_p394_early_workfn);
	a52_p394_early_index = 0;
	a52_p394_early_schedule();
	return 0;
}
late_initcall(a52_p394_early_init);
'''


def patch_rec(text: str) -> str:
    if "A52_PHASE394_EARLY_FIXED_LANE_V1" in text:
        return text
    if "A52_PHASE393_IRQ_RPMH_SMMU_CLIFF_V1" not in text or \
       "A52_PHASE392_PERSISTENT_GAP_DUAL_BACKEND_V1" not in text:
        raise SystemExit("Phase394 requires Phase392+393 recorder")
    sig = "static int __init a52_r393_irq_arm_init(void)"
    _, end = function_bounds(text, sig)
    tail = text.find("late_initcall(a52_r393_irq_arm_init);", end)
    if tail < 0:
        raise SystemExit("Phase394 Phase393 late_initcall missing")
    tail += len("late_initcall(a52_r393_irq_arm_init);")
    return text[:tail] + EARLY_BLOCK + text[tail:]


def validate(root: Path) -> None:
    drm = (root / DRM).read_text()
    dsi = (root / DSI).read_text()
    rec = (root / REC).read_text()
    for token in (
        MARK,
        "P276 394M f=%u s=%u r=%d p=%d t=%d c=%s",
        "a52_p394_fixup_fail(0, ret",
        "a52_p394_fixup_fail(1, ret",
        "a52_p394_fixup_fail(2, -EINVAL",
        "a52_p394_fixup_fail(3, -EINVAL",
        "P276 394M pass",
    ):
        if token not in drm:
            raise SystemExit("Phase394 DRM token missing: " + token)
    for token in (
        MARK,
        "P276 394F route old=%x",
        "*flags &= ~DSI_CTRL_CMD_FETCH_MEMORY;",
        "*flags |= DSI_CTRL_CMD_FIFO_STORE;",
        "P276 394F result ret=%d irq=%d in=%x st=%x",
    ):
        if token not in dsi:
            raise SystemExit("Phase394 DSI token missing: " + token)
    for token in (
        "A52_PHASE394_EARLY_FIXED_LANE_V1",
        "#define A52_P394_EARLY_OFFSET 512U",
        "15000U, 20000U, 25000U, 30000U, 35000U, 40000U",
        "a52_p394_early_workfn",
        "a52_r341_sideband_index",
    ):
        if token not in rec:
            raise SystemExit("Phase394 recorder token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root
    for rel in (DRM, DSI, REC):
        if not (root / rel).is_file():
            raise SystemExit("Phase394 source missing: " + str(rel))
    if not ns.check_only:
        p = root / DRM; p.write_text(patch_drm(p.read_text()))
        p = root / DSI; p.write_text(patch_dsi(p.read_text()))
        p = root / REC; p.write_text(patch_rec(p.read_text()))
    validate(root)
    print("Phase394 mode-fixup bisect + exact F0 FIFO + fixed early lane: PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
