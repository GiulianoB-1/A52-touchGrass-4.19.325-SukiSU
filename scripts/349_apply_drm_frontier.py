#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
DRV = Path("drivers/a52_display/msm/msm_drv.c")
ATOMIC = Path("drivers/a52_display/msm/msm_atomic.c")
KMS = Path("drivers/a52_display/msm/sde/sde_kms.c")
MARK = "A52_PHASE349_DRM_FRONTIER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase349 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE349_DRM_FRONTIER_V1
 *
 * Phase348 proved dsi_message_tx() is never entered in the failing boot while
 * the display stack finishes bind/KMS init. Persist the first occurrence of
 * the DRM userspace/atomic/commit frontier into the upper half of the existing
 * Phase346 raw sideband.
 *
 * Each event has two replicas per 4 KiB mirror, and Phase346 already mirrors
 * the whole layout at +0x1000. Therefore four physical copies exist/event.
 *
 * copy A: +0x0800..+0x0cff
 * copy B: +0x1800..+0x1cff
 */
#define A52_P349_BASE_OFF           0x0800U
#define A52_P349_SLOT_BYTES         64U
#define A52_P349_EVENT_COUNT        10U
#define A52_P349_REPLICAS           2U
#define A52_P349_MAGIC              0x393433544e524644ULL
#define A52_P349_COMMIT             0x349c0de5U

enum a52_p349_event {
	A52_P349_INIT = 0,
	A52_P349_DRM_OPEN_ENTER = 1,
	A52_P349_DRM_OPEN_EXIT = 2,
	A52_P349_ATOMIC_CHECK_ENTER = 3,
	A52_P349_ATOMIC_CHECK_EXIT = 4,
	A52_P349_ATOMIC_COMMIT_ENTER = 5,
	A52_P349_COMMIT_TAIL_ENTER = 6,
	A52_P349_SDE_PREPARE_ENTER = 7,
	A52_P349_SDE_COMMIT_ENTER = 8,
	A52_P349_DSI_MSG_ENTER = 9,
};

struct a52_p349_slot {
	u64 magic;
	u64 ns;
	u32 event;
	u32 seq;
	u32 pid;
	u32 tgid;
	u32 cpu;
	u32 arg0;
	u32 arg1;
	u32 arg2;
	u32 arg3;
	u32 state;
	u32 commit;
	u32 version;
};

static atomic_t a52_p349_seen = ATOMIC_INIT(0);
static atomic_t a52_p349_seq = ATOMIC_INIT(0);

void a52_p349_mark(u32 event, u32 arg0, u32 arg1, u32 arg2, u32 arg3)
{
	struct a52_p349_slot slot;
	u32 bit;
	unsigned int r;

	BUILD_BUG_ON(sizeof(struct a52_p349_slot) != A52_P349_SLOT_BYTES);
	if (event >= A52_P349_EVENT_COUNT)
		return;

	bit = BIT(event);
	if (atomic_fetch_or(bit, &a52_p349_seen) & bit)
		return;

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_P349_MAGIC;
	slot.ns = ktime_get_ns();
	slot.event = event;
	slot.seq = (u32)atomic_inc_return(&a52_p349_seq);
	slot.pid = (u32)current->pid;
	slot.tgid = (u32)current->tgid;
	slot.cpu = (u32)raw_smp_processor_id();
	slot.arg0 = arg0;
	slot.arg1 = arg1;
	slot.arg2 = arg2;
	slot.arg3 = arg3;
	slot.state = (u32)atomic_read(&a52_p349_seen);
	slot.commit = A52_P349_COMMIT;
	slot.version = 1U;

	if (!READ_ONCE(a52_p346_sideband))
		return;

	for (r = 0; r < A52_P349_REPLICAS; r++) {
		unsigned int pos = A52_P349_BASE_OFF +
			(event * A52_P349_REPLICAS + r) * A52_P349_SLOT_BYTES;
		void *dst0;
		void *dst1;

		if (pos + sizeof(slot) > A52_P346_COPY_BYTES)
			return;
		dst0 = (u8 *)a52_p346_sideband + pos;
		dst1 = (u8 *)a52_p346_sideband + A52_P346_COPY_BYTES + pos;
		memcpy(dst0, &slot, sizeof(slot));
		memcpy(dst1, &slot, sizeof(slot));
		wmb();
		__flush_dcache_area(dst0, sizeof(slot));
		__flush_dcache_area(dst1, sizeof(slot));
	}
}

'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE346_DMA_RAW_SIDEBAND_V1",
        "static void *a52_p346_sideband;",
        "a52_p346_write_slot(0U, &slot);",
    ):
        if token not in text:
            raise SystemExit("Phase349 HWC prerequisite missing: " + token)

    if "#include <linux/sched.h>\n" not in text:
        inc = "#include <linux/io.h>\n"
        text = one(text, inc, inc + "#include <linux/sched.h>\n", "sched include")

    anchor = "static void *a52_p346_sideband;\n"
    text = one(text, anchor, anchor + RAW_BLOCK, "raw helper insertion")

    old = "\ta52_p346_write_slot(0U, &slot);\n}\n"
    new = "\ta52_p346_write_slot(0U, &slot);\n\ta52_p349_mark(A52_P349_INIT, 0U, 0U, 0U, 0U);\n}\n"
    return one(text, old, new, "init marker")


def add_extern(text: str, label: str) -> str:
    decl = "extern void a52_p349_mark(u32 event, u32 arg0, u32 arg1, u32 arg2, u32 arg3); /* " + MARK + " */\n"
    if decl in text:
        return text
    anchor = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if text.count(anchor) != 1:
        raise SystemExit(f"Phase349 {label} recorder include count {text.count(anchor)}")
    return text.replace(anchor, anchor + decl, 1)


def patch_drv(text: str) -> str:
    if MARK in text:
        return text
    text = add_extern(text, "msm_drv")

    text = one(
        text,
        '\ta52_ackfr_record("P276 296O e");\n',
        '\ta52_p349_mark(1U, 0U, 0U, 0U, 0U);\n'
        '\ta52_ackfr_record("P276 296O e");\n',
        "DRM open enter",
    )
    text = one(
        text,
        '\ta52_ackfr_record("P276 296O x r=%d", rc);\n\treturn rc;\n',
        '\ta52_ackfr_record("P276 296O x r=%d", rc);\n'
        '\ta52_p349_mark(2U, (u32)rc, 0U, 0U, 0U);\n'
        '\treturn rc;\n',
        "DRM open exit",
    )

    text = one(
        text,
        '\ta52_ackfr_record("P276 296A e");\n',
        '\ta52_p349_mark(3U, state ? (u32)state->num_connector : 0xffffffffU, 0U, 0U, 0U);\n'
        '\ta52_ackfr_record("P276 296A e");\n',
        "atomic check enter",
    )
    text = one(
        text,
        '\ta52_ackfr_record("P276 296A x r=%d", rc);\n\treturn rc;\n',
        '\ta52_ackfr_record("P276 296A x r=%d", rc);\n'
        '\ta52_p349_mark(4U, (u32)rc, 0U, 0U, 0U);\n'
        '\treturn rc;\n',
        "atomic check exit",
    )
    return text


def patch_atomic(text: str) -> str:
    if MARK in text:
        return text
    text = add_extern(text, "msm_atomic")

    text = one(
        text,
        '\ta52_ackfr_record("P276 296C e n=%d", nonblock);\n',
        '\ta52_p349_mark(5U, nonblock ? 1U : 0U, 0U, 0U, 0U);\n'
        '\ta52_ackfr_record("P276 296C e n=%d", nonblock);\n',
        "atomic commit enter",
    )

    old = '''void msm_atomic_commit_tail(struct drm_atomic_state *state)
{
	struct drm_device *dev = state->dev;
	struct msm_drm_private *priv = dev->dev_private;
	struct msm_kms *kms = priv->kms;

	kms->funcs->prepare_commit(kms, state);
'''
    new = '''void msm_atomic_commit_tail(struct drm_atomic_state *state)
{
	struct drm_device *dev = state->dev;
	struct msm_drm_private *priv = dev->dev_private;
	struct msm_kms *kms = priv->kms;

	a52_p349_mark(6U, 0U, 0U, 0U, 0U);
	kms->funcs->prepare_commit(kms, state);
'''
    return one(text, old, new, "commit tail enter")


def patch_kms(text: str) -> str:
    if MARK in text:
        return text
    text = add_extern(text, "sde_kms")

    # This vendor tree enforces declaration-before-statement. Keep the
    # inherited A52_ACKFR_SCOPE where it is, and place our executable marker
    # only after the complete local declaration block.
    text = one(
        text,
        '\tint i, rc;\n\n'
        '\ta52_ackfr_record("P276 296K p");\n',
        '\tint i, rc;\n\n'
        '\ta52_p349_mark(7U, 0U, 0U, 0U, 0U);\n'
        '\ta52_ackfr_record("P276 296K p");\n',
        "SDE prepare enter after declarations",
    )

    text = one(
        text,
        '\tint i;\n\n'
        '\ta52_ackfr_record("P276 296K c");\n',
        '\tint i;\n\n'
        '\ta52_p349_mark(8U, 0U, 0U, 0U, 0U);\n'
        '\ta52_ackfr_record("P276 296K c");\n',
        "SDE commit enter after declarations",
    )
    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    decl = 'extern void a52_p346_sideband_init(void); /* A52_PHASE346_DMA_RAW_SIDEBAND_V1 */\n'
    if decl not in text:
        raise SystemExit("Phase349 CTRL Phase346 declaration missing")
    text = text.replace(
        decl,
        decl + 'extern void a52_p349_mark(u32 event, u32 arg0, u32 arg1, u32 arg2, u32 arg3); /* ' + MARK + ' */\n',
        1,
    )

    anchor = '''static int dsi_message_tx(struct dsi_ctrl *dsi_ctrl,
			  const struct mipi_dsi_msg *msg,
			  u32 *flags)
{
	int rc = 0;
'''
    if text.count(anchor) != 1:
        raise SystemExit(f"Phase349 dsi_message_tx anchor count {text.count(anchor)}")

    # CONFIG_DISPLAY_SAMSUNG contributes a local declaration after cmdbuf.
    # Split that declaration from its first debug statement so our executable
    # marker remains at the function frontier without violating the old C
    # declaration ordering enforced by this kernel.
    exec_anchor = '''	u8 *cmdbuf;

#if defined(CONFIG_DISPLAY_SAMSUNG)
	struct samsung_display_driver_data *vdd = ss_get_vdd(dsi_ctrl->cell_index);
	if (vdd->debug_data && vdd->debug_data->print_cmds)
		print_cmd_desc(msg, vdd);
#endif

'''
    exec_repl = '''	u8 *cmdbuf;

#if defined(CONFIG_DISPLAY_SAMSUNG)
	struct samsung_display_driver_data *vdd = ss_get_vdd(dsi_ctrl->cell_index);
#endif

	a52_p349_mark(9U,
		msg ? (u32)msg->type : 0xffffffffU,
		msg ? (u32)msg->tx_len : 0xffffffffU,
		msg ? (u32)msg->flags : 0xffffffffU,
		flags ? *flags : 0xffffffffU);

#if defined(CONFIG_DISPLAY_SAMSUNG)
	if (vdd->debug_data && vdd->debug_data->print_cmds)
		print_cmd_desc(msg, vdd);
#endif

'''
    return one(text, exec_anchor, exec_repl, "DSI message enter after declarations")


def validate(before: dict[Path, str], after: dict[Path, str]) -> None:
    combined = "\n".join(after.values())
    for token in (
        MARK,
        "A52_P349_MAGIC              0x393433544e524644ULL",
        "A52_P349_COMMIT             0x349c0de5U",
        "A52_P349_EVENT_COUNT        10U",
        "A52_P349_REPLICAS           2U",
        "a52_p349_mark(A52_P349_INIT",
        "a52_p349_mark(1U",
        "a52_p349_mark(2U",
        "a52_p349_mark(3U",
        "a52_p349_mark(4U",
        "a52_p349_mark(5U",
        "a52_p349_mark(6U",
        "a52_p349_mark(7U",
        "a52_p349_mark(8U",
        "a52_p349_mark(9U",
    ):
        if token not in combined:
            raise SystemExit("Phase349 required token missing: " + token)


    kms = after[KMS]
    ctrl = after[CTRL]
    if 'a52_p349_mark(7U, 0U, 0U, 0U, 0U);\n\tstruct sde_kms *sde_kms;' in kms:
        raise SystemExit("Phase349 prepare marker precedes declarations")
    if 'a52_p349_mark(8U, 0U, 0U, 0U, 0U);\n\tstruct sde_kms *sde_kms;' in kms:
        raise SystemExit("Phase349 commit marker precedes declarations")
    if 'a52_p349_mark(9U,' in ctrl:
        marker = ctrl.index('a52_p349_mark(9U,')
        samsung_decl = ctrl.index('struct samsung_display_driver_data *vdd =', ctrl.index('static int dsi_message_tx('))
        if marker < samsung_decl:
            raise SystemExit("Phase349 DSI marker precedes Samsung declaration")

    protected = (
        "DSI_W32(", "DSI_R32(", "wait_for_completion_timeout(",
        "clk_set_rate(", "clk_set_parent(", "clk_prepare_enable(",
        "clk_disable_unprepare(", "regulator_enable(", "regulator_disable(",
        "reset_control_assert(", "reset_control_deassert(",
        "udelay(", "usleep_range(", "msleep(",
    )
    for path in before:
        for token in protected:
            if before[path].count(token) != after[path].count(token):
                raise SystemExit(f"Phase349 changed protected primitive {token} in {path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    paths = [HWC, CTRL, DRV, ATOMIC, KMS]
    files = {p: ns.root / p for p in paths}
    for p, f in files.items():
        if not f.is_file():
            raise SystemExit("Phase349 source missing: " + str(p))

    src = {p: files[p].read_text() for p in paths}
    if all(MARK in src[p] for p in paths):
        print("Phase349 DRM frontier audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase349 marker missing in check-only mode")

    out = dict(src)
    out[HWC] = patch_hwc(src[HWC])
    out[CTRL] = patch_ctrl(src[CTRL])
    out[DRV] = patch_drv(src[DRV])
    out[ATOMIC] = patch_atomic(src[ATOMIC])
    out[KMS] = patch_kms(src[KMS])
    validate(src, out)

    for p in paths:
        files[p].write_text(out[p])

    print("Phase349 DRM frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
