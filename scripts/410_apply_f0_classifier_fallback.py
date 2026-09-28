#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE410_F0_CLASSIFIER_FALLBACK_V1"
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase410 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


FALLBACK_BLOCK = r'''
/* A52_PHASE410_F0_CLASSIFIER_FALLBACK_V1
 *
 * Phase409 only persisted after the exact F0 target completed/timed out.
 * If the exact matcher was never reached, the Samsung partition stayed zero
 * and transport vs matcher failure remained ambiguous.
 *
 * Phase410 arms a delayed fallback after DSI controller init. Five seconds
 * later, if the exact target has not already finalized, it freezes the hot
 * buffer and writes the same 2 MiB image anyway. This is safely after the
 * expected early display transaction and therefore does not perturb the
 * SW_TRIGGER microsecond window.
 */
#define A52_P410_REASON_TARGET     1U
#define A52_P410_REASON_FALLBACK   2U
#define A52_P410_FALLBACK_MS       5000U

static u32 a52_p410_reason;

static void a52_p410_fallback_workfn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_p410_fallback_work,
			    a52_p410_fallback_workfn);

static void a52_p410_fallback_workfn(struct work_struct *work)
{
	(void)work;

	if (!a52_p409_hot || !a52_p409_disk)
		return;
	if (atomic_cmpxchg(&a52_p409_frozen, 0, 1) != 0)
		return;

	a52_p410_reason = A52_P410_REASON_FALLBACK;
	a52_p409_wait_ret = 0xfffffffeU;
	a52_p409_dma_irq_trig = 0U;
	memset(&a52_p409_final_snapshot, 0,
	       sizeof(a52_p409_final_snapshot));
	a52_p409_final_snapshot.stage = A52_P409_FINAL;
	a52_p409_final_snapshot.aux0 = A52_P410_REASON_FALLBACK;
	a52_p409_final_snapshot.commit = A52_P409_EVENT_COMMIT;
	schedule_work(&a52_p409_write_work);
}

void a52_p410_arm_fallback(void)
{
	schedule_delayed_work(&a52_p410_fallback_work,
			      msecs_to_jiffies(A52_P410_FALLBACK_MS));
}

'''


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE409_RAM1M_DEBUG2M_DSI_V1" not in text:
        raise SystemExit("Phase410 requires Phase409 hot/cold recorder")

    text = one(
        text,
        "#define A52_P409_MAGIC              0x3930344953443241ULL /* A2DSI409 */\n",
        "#define A52_P409_MAGIC              0x3031344953443241ULL /* A2DSI410 */\n",
        "image magic",
    )
    text = one(
        text,
        "#define A52_P409_IMAGE_COMMIT       0x409c0de5U\n",
        "#define A52_P409_IMAGE_COMMIT       0x410c0de5U\n",
        "image commit",
    )
    text = one(
        text,
        "\th->phase = 409U;\n",
        "\th->phase = 410U;\n",
        "image phase",
    )

    old = "\th->writer_state = (u32)atomic_read(&a52_p409_writer_state);\n"
    new = old + "\th->reserved[0] = a52_p410_reason;\n"
    # reason is declared in FALLBACK_BLOCK, inserted before build_image use.
    # Put the block before build_image, after write-work declaration.
    anchor = "static void a52_p409_build_image(void)\n"
    text = one(text, anchor, FALLBACK_BLOCK + anchor, "fallback block")
    text = one(text, old, new, "reason header")

    sig = "void a52_p409_finalize_and_schedule(struct dsi_ctrl_hw *ctrl,"
    start = text.find(sig)
    if start < 0:
        raise SystemExit("Phase410 finalize function missing")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit("Phase410 finalize brace missing")
    fn_end = text.find("\n}", brace)
    if fn_end < 0:
        raise SystemExit("Phase410 finalize end missing")
    fn = text[start:fn_end + 2]
    old = """	if (atomic_cmpxchg(&a52_p409_frozen, 0, 1) != 0)
		return;

	/* Final state is captured once; no further hot-buffer writes follow. */
"""
    new = """	if (atomic_cmpxchg(&a52_p409_frozen, 0, 1) != 0)
		return;

	a52_p410_reason = A52_P410_REASON_TARGET;
	cancel_delayed_work(&a52_p410_fallback_work);

	/* Final state is captured once; no further hot-buffer writes follow. */
"""
    if old not in fn:
        raise SystemExit("Phase410 finalize anchor missing")
    fn = fn.replace(old, new, 1)
    text = text[:start] + fn + text[fn_end + 2:]

    text += (
        "\n/* " + MARK + " */\n"
        "static const char a52_p410_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE409_RAM1M_DEBUG2M_DSI_V1" not in text:
        raise SystemExit("Phase410 requires Phase409 controller hooks")

    decl = (
        "extern void a52_p409_finalize_and_schedule(struct dsi_ctrl_hw *ctrl, "
        "u32 wait_ret, u32 dma_irq_trig);\n"
    )
    if decl not in text:
        raise SystemExit("Phase410 Phase409 declaration anchor missing")
    text = one(
        text,
        decl,
        decl +
        "extern void a52_p410_arm_fallback(void); /* " + MARK + " */\n"
        "#define A52_P410_F0_META 11U\n"
        "#define A52_P410_F0_PAYLOAD 12U\n"
        "#define A52_P410_DSI_INIT 13U\n",
        "Phase410 declarations",
    )

    init_old = """	a52_p409_init_buffers();
	a52_p346_sideband_init();
"""
    init_new = """	a52_p409_init_buffers();
	a52_p410_arm_fallback();
	a52_p409_hot_record(&dsi_ctrl->hw, A52_P410_DSI_INIT, 0U, 0U);
	a52_p346_sideband_init();
"""
    text = one(text, init_old, init_new, "fallback arm")

    sig = "static void a52_p293_gdm_try_arm(struct dsi_ctrl *dsi_ctrl,"
    start = text.find(sig)
    if start < 0:
        raise SystemExit("Phase410 exact matcher missing")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit("Phase410 matcher brace missing")
    depth = 0
    end = -1
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    if end < 0:
        raise SystemExit("Phase410 matcher end missing")
    fn = text[start:end]

    anchor = """	const u8 *p;

	if (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||
"""
    classifier = """	const u8 *p;

	/* Phase410: classify every controller-0 F0 command before exact gating. */
	if (dsi_ctrl && msg && flags && dsi_ctrl->cell_index == 0 &&
	    msg->tx_buf && msg->tx_len >= 1) {
		u32 meta;
		u32 payload = 0;
		const u8 *q = msg->tx_buf;

		if (q[0] == 0xF0) {
			meta = ((u32)(msg->flags & 0xffffU) << 16) |
			       ((u32)(msg->type & 0xffU) << 8) |
			       ((u32)msg->tx_len & 0xffU);
			payload = (u32)q[0];
			if (msg->tx_len > 1)
				payload |= (u32)q[1] << 8;
			if (msg->tx_len > 2)
				payload |= (u32)q[2] << 16;
			a52_p409_hot_record(&dsi_ctrl->hw, A52_P410_F0_META,
				*flags, meta);
			a52_p409_hot_record(&dsi_ctrl->hw, A52_P410_F0_PAYLOAD,
				payload, 0U);
		}
	}

	if (!dsi_ctrl || !msg || !flags || dsi_ctrl->cell_index != 0 ||
"""
    if anchor not in fn:
        raise SystemExit("Phase410 matcher classifier anchor missing")
    fn = fn.replace(anchor, classifier, 1)
    text = text[:start] + fn + text[end:]
    return text


def validate(root: Path) -> None:
    hwc = (root / HWC).read_text(errors="replace")
    ctrl = (root / CTRL).read_text(errors="replace")

    for token in (
        MARK,
        "A2DSI410",
        "0x410c0de5U",
        "A52_P410_FALLBACK_MS       5000U",
        "DECLARE_DELAYED_WORK(a52_p410_fallback_work",
        "a52_p410_reason = A52_P410_REASON_FALLBACK",
        "a52_p410_reason = A52_P410_REASON_TARGET",
        "cancel_delayed_work(&a52_p410_fallback_work)",
        "h->reserved[0] = a52_p410_reason",
        "schedule_work(&a52_p409_write_work)",
    ):
        if token not in hwc:
            raise SystemExit("Phase410 HWC token missing: " + token)

    for token in (
        MARK,
        "a52_p410_arm_fallback();",
        "A52_P410_F0_META",
        "A52_P410_F0_PAYLOAD",
        "A52_P410_DSI_INIT",
        "classify every controller-0 F0 command",
        "payload |= (u32)q[2] << 16",
        "a52_p409_hot_record(&dsi_ctrl->hw, A52_P410_F0_META",
        "a52_p409_hot_record(&dsi_ctrl->hw, A52_P410_F0_PAYLOAD",
    ):
        if token not in ctrl:
            raise SystemExit("Phase410 CTRL token missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (HWC, CTRL):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase410 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))
        p = ns.root / CTRL
        p.write_text(patch_ctrl(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase410 F0 classifier + 5s fallback Samsung persist: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
