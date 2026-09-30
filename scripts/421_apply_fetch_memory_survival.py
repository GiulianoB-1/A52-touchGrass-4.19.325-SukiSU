#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase421 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase421 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase421 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase421 closing brace missing: {sig}")


P421_RECORDER = r'''
/* A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1
 *
 * Corruption-tolerant fixed-slot survival lane for the first real F0 5A 5A
 * panel transaction. CRC/commit are convenience checks only. The raw repeated
 * fields are intentionally decodable even when every CRC fails after a manual
 * reset.
 *
 * copy0: B1900000 + 0x500 (11 x 128)
 * copy1: B1900000 + 0xA80 (11 x 128)
 * copy2: B1A00000 + 0x800 (11 x 128), exactly 1 MiB from copy0/1 page
 *
 * The B1A location is in Phase414's reserved 4 KiB header page. Phase414 data
 * records begin at +0x1000; Phase416 status copies end below +0x280.
 *
 * Slot layout is exactly 128 bytes:
 *   stage_a[32], flags[5], rc[5], nonce[5],
 *   stage_b[32], irq[5], ret[5], valid[5], format, crc32c, commit.
 *
 * Stage is encoded as 0xD0 + stage so an unwritten zero-cleared slot cannot
 * masquerade as S00. Every later stage carries forward all previously learned
 * small values. The nonce is random per boot/first target and repeated 5x.
 */
#define A52_P421_OFF0              0x00000500U
#define A52_P421_OFF1              0x00000A80U
#define A52_P421_OFF2              0x00000800U
#define A52_P421_SLOT_BYTES        128U
#define A52_P421_SLOTS             11U
#define A52_P421_STAGE_BASE        0xD0U
#define A52_P421_FORMAT            0xA1U
#define A52_P421_COMMIT            0x421c0de5U
#define A52_P421_V_FLAGS           BIT(0)
#define A52_P421_V_RC              BIT(1)
#define A52_P421_V_IRQ             BIT(2)
#define A52_P421_V_RET             BIT(3)

struct a52_p421_slot {
	u8 stage_a[32];
	u8 flags[5];
	s16 rc[5];
	u32 nonce[5];
	u8 stage_b[32];
	u8 irq[5];
	s16 ret[5];
	u8 valid[5];
	u8 format;
	u32 crc32c;
	u32 commit;
} __packed;

static u32 a52_p421_nonce;
static u8 a52_p421_flags;
static s16 a52_p421_rc;
static u8 a52_p421_irq;
static s16 a52_p421_ret;
static u8 a52_p421_valid;
static atomic_t a52_p421_highest = ATOMIC_INIT(-1);

static u32 a52_p421_get_nonce(void)
{
	u32 old;
	u32 nonce;

	old = READ_ONCE(a52_p421_nonce);
	if (old)
		return old;

	nonce = get_random_u32();
	if (!nonce)
		nonce = 0x42142101U;
	old = cmpxchg(&a52_p421_nonce, 0U, nonce);
	return old ? old : nonce;
}

static void a52_p421_copy_persist(void __iomem *dst,
				  const struct a52_p421_slot *slot)
{
	if (!dst || !slot)
		return;

	memcpy_toio(dst, slot, sizeof(*slot));
	/* Order the copy, clean the cached reserved mapping, then do not allow the
	 * caller to proceed into the next DSI operation until persistence reaches
	 * the architectural completion point.
	 */
	wmb();
	__flush_dcache_area((void __force *)dst, sizeof(*slot));
	dsb(sy);
}

void a52_p421_survival_record(u8 stage, u8 valid, u32 flags,
			      int rc, u32 irq, int ret)
{
	struct a52_p421_slot slot;
	void __iomem *dst;
	u8 code;
	u32 nonce;
	int previous;
	unsigned int i;

	BUILD_BUG_ON(sizeof(struct a52_p421_slot) != A52_P421_SLOT_BYTES);
	BUILD_BUG_ON(A52_P421_OFF1 +
		     A52_P421_SLOTS * A52_P421_SLOT_BYTES >
		     A52_P392_HEADER_BYTES);
	BUILD_BUG_ON(A52_P421_OFF2 +
		     A52_P421_SLOTS * A52_P421_SLOT_BYTES >
		     A52_P414_HEADER_BYTES);

	if (stage >= A52_P421_SLOTS)
		return;

	/* Stages are monotonic for the one exact target. Duplicate/late callbacks
	 * cannot rewrite a slot or move the frontier backwards.
	 */
	for (;;) {
		previous = atomic_read(&a52_p421_highest);
		if ((int)stage <= previous)
			return;
		if (atomic_cmpxchg(&a52_p421_highest, previous, stage) == previous)
			break;
	}

	nonce = a52_p421_get_nonce();
	if (valid & A52_P421_V_FLAGS)
		a52_p421_flags = (u8)flags;
	if (valid & A52_P421_V_RC)
		a52_p421_rc = (s16)rc;
	if (valid & A52_P421_V_IRQ)
		a52_p421_irq = (u8)irq;
	if (valid & A52_P421_V_RET)
		a52_p421_ret = (s16)ret;
	a52_p421_valid |= valid;

	memset(&slot, 0, sizeof(slot));
	code = (u8)(A52_P421_STAGE_BASE + stage);
	memset(slot.stage_a, code, sizeof(slot.stage_a));
	memset(slot.stage_b, code, sizeof(slot.stage_b));
	for (i = 0; i < ARRAY_SIZE(slot.flags); i++)
		slot.flags[i] = a52_p421_flags;
	for (i = 0; i < ARRAY_SIZE(slot.rc); i++)
		slot.rc[i] = a52_p421_rc;
	for (i = 0; i < ARRAY_SIZE(slot.nonce); i++)
		slot.nonce[i] = nonce;
	for (i = 0; i < ARRAY_SIZE(slot.irq); i++)
		slot.irq[i] = a52_p421_irq;
	for (i = 0; i < ARRAY_SIZE(slot.ret); i++)
		slot.ret[i] = a52_p421_ret;
	memset(slot.valid, a52_p421_valid, sizeof(slot.valid));
	slot.format = A52_P421_FORMAT;
	slot.crc32c = a52_p392_crc32c(&slot,
				      offsetof(struct a52_p421_slot, crc32c));
	slot.commit = A52_P421_COMMIT;

	/* Each physical copy is independently completed. In particular, copy0 is
	 * fully cache-cleaned + dsb before copy1 begins.
	 */
	if (READ_ONCE(a52_p392_base)) {
		dst = (u8 __iomem *)a52_p392_base + A52_P421_OFF0 +
		      (unsigned int)stage * A52_P421_SLOT_BYTES;
		a52_p421_copy_persist(dst, &slot);

		dst = (u8 __iomem *)a52_p392_base + A52_P421_OFF1 +
		      (unsigned int)stage * A52_P421_SLOT_BYTES;
		a52_p421_copy_persist(dst, &slot);
	}

	if (READ_ONCE(a52_p414_ram)) {
		dst = (u8 __iomem *)a52_p414_ram + A52_P421_OFF2 +
		      (unsigned int)stage * A52_P421_SLOT_BYTES;
		a52_p421_copy_persist(dst, &slot);
	}
}
EXPORT_SYMBOL_GPL(a52_p421_survival_record);
'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE420_UMODE_ROUNDTRIP_VREFRESH_PARITY_V1",
        "A52_PHASE405_DMA_DONE_DIRECT_V1",
        "A52_PHASE406_P392_CACHE_PERSIST_V1",
        "A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1",
        "A52_PHASE416_SEQUENTIAL_3M_DISK_PERSIST_V2",
        "static void a52_p406_persist(void __iomem *addr, size_t len)",
    ):
        if token not in text:
            raise SystemExit("Phase421 recorder prerequisite missing: " + token)

    if "#include <linux/random.h>\n" not in text:
        text = one(text, "#include <linux/kernel.h>\n",
                   "#include <linux/kernel.h>\n#include <linux/random.h>\n",
                   "random include")
    if "#include <asm/barrier.h>\n" not in text:
        text = one(text, "#include <asm/cacheflush.h>\n",
                   "#include <asm/cacheflush.h>\n#include <asm/barrier.h>\n",
                   "barrier include")

    # Phase405 occupies the same B190 slots. Its exact-F0 producer has been
    # retired since Phase411, but remove the formatter hook completely so no
    # stale legacy P276 record can collide with Phase421.
    call = "a52_p405_dsi_mirror(dst); /* A52_PHASE405_DMA_DONE_DIRECT_V1 */"
    if call in text:
        text = text.replace(call,
            "/* Phase421 owns the Phase405 fixed slots; legacy mirror retired. */",
            1)
    else:
        # The generated variable name is stable in current lineage, but fail
        # loudly rather than silently sharing slots if it changed.
        raise SystemExit("Phase421 Phase405 mirror call missing")

    old_decl = "static void a52_p405_dsi_mirror(const char *message)"
    if old_decl in text:
        text = text.replace(old_decl,
            "static void __maybe_unused a52_p405_dsi_mirror(const char *message)", 1)

    text += "\n" + P421_RECORDER + "\n"
    return text


P421_CTRL_HELPER = r'''
/* A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1 */
#define A52_P421_V_FLAGS BIT(0)
#define A52_P421_V_RC    BIT(1)
#define A52_P421_V_IRQ   BIT(2)
#define A52_P421_V_RET   BIT(3)
extern void a52_p421_survival_record(u8 stage, u8 valid, u32 flags,
				     int rc, u32 irq, int ret);
static atomic_t a52_p421_target = ATOMIC_INIT(0);

bool a52_p421_target_active(void)
{
	return atomic_read(&a52_p421_target) != 0;
}
'''


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    for token in (
        "A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1",
        "A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1",
        "A52_PHASE420_UMODE_ROUNDTRIP_VREFRESH_PARITY_V1",
        "static bool a52_p411_exact_f0(",
    ):
        if token not in text:
            raise SystemExit("Phase421 DSI prerequisite missing: " + token)

    # Remove the Phase411 functional A/B FIFO treatment. The stock secure-mode
    # safeguard remains elsewhere in dsi_message_setup_tx_mode().
    fifo = '''	/* A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1
	 * A/B treatment: bypass memory DMA for every command that fits the
	 * controller's native FIFO. Longer commands keep the original path.
	 */
	if ((cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE) {
		*flags &= ~(DSI_CTRL_CMD_FETCH_MEMORY |
			    DSI_CTRL_CMD_NON_EMBEDDED_MODE);
		*flags |= DSI_CTRL_CMD_FIFO_STORE;
		return;
	}

'''
    text = one(text, fifo,
        "	/* Phase421: restore TouchGrass stock FETCH_MEMORY selection. */\n",
        "retire FIFO treatment")

    # Global target state is visible to dsi_ctrl_hw_cmn.c so S07/S08 bracket
    # only this exact transaction.
    anchor = "static void a52_p411_note_f0(struct dsi_ctrl *dsi_ctrl,"
    text = one(text, anchor, P421_CTRL_HELPER + "\n" + anchor,
               "target helper")

    # dsi_message_tx needs a stable target boolean because the global active
    # flag is intentionally cleared on every exit.
    start, end = function_bounds(text, "static int dsi_message_tx(")
    fn = text[start:end]
    decl = "	u8 *cmdbuf;\n"
    if decl not in fn:
        raise SystemExit("Phase421 dsi_message_tx declaration anchor missing")
    fn = fn.replace(decl, decl + "	bool a52_p421_f0 = false;\n", 1)

    old = '''	a52_p414_host_note(dsi_ctrl, msg, flags);
	if (a52_p411_exact_f0(dsi_ctrl, msg))
		a52_ackfr_record("P414 F0 pre f=%x", flags ? *flags : 0U);
	a52_p411_note_f0(dsi_ctrl, msg, flags, "before-mode");

	/* Select the tx mode to transfer the command */
	dsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);
	if (a52_p411_exact_f0(dsi_ctrl, msg))
		a52_ackfr_record("P414 F0 post f=%x", flags ? *flags : 0U);
	a52_p411_note_f0(dsi_ctrl, msg, flags, "after-mode");
'''
    new = '''	a52_p414_host_note(dsi_ctrl, msg, flags);
	a52_p421_f0 = a52_p411_exact_f0(dsi_ctrl, msg);
	if (a52_p421_f0) {
		atomic_set(&a52_p421_target, 1);
		a52_p421_survival_record(0U, A52_P421_V_FLAGS,
			flags ? *flags : 0U, 0, 0U, 0);
		a52_ackfr_record("P414 F0 pre f=%x", flags ? *flags : 0U);
	}
	a52_p411_note_f0(dsi_ctrl, msg, flags, "before-mode");

	/* Select the tx mode to transfer the command */
	dsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);
	if (a52_p421_f0) {
		a52_p421_survival_record(1U, A52_P421_V_FLAGS,
			flags ? *flags : 0U, 0, 0U, 0);
		a52_ackfr_record("P414 F0 post f=%x", flags ? *flags : 0U);
	}
	a52_p411_note_f0(dsi_ctrl, msg, flags, "after-mode");
'''
    if old not in fn:
        raise SystemExit("Phase421 Phase414 F0 chronology anchor missing")
    fn = fn.replace(old, new, 1)

    old = '''	rc = dsi_message_validate_tx_mode(dsi_ctrl, msg->tx_len, flags);
	if (rc) {
'''
    new = '''	rc = dsi_message_validate_tx_mode(dsi_ctrl, msg->tx_len, flags);
	if (a52_p421_f0)
		a52_p421_survival_record(2U, A52_P421_V_FLAGS | A52_P421_V_RC,
			*flags, rc, 0U, 0);
	if (rc) {
'''
    if old not in fn:
        raise SystemExit("Phase421 validation anchor missing")
    fn = fn.replace(old, new, 1)

    old = '''	rc = mipi_dsi_create_packet(&packet, msg);
	if (rc) {
'''
    new = '''	rc = mipi_dsi_create_packet(&packet, msg);
	if (a52_p421_f0)
		a52_p421_survival_record(3U, A52_P421_V_RC, 0U, rc, 0U, 0);
	if (rc) {
'''
    if old not in fn:
        raise SystemExit("Phase421 packet anchor missing")
    fn = fn.replace(old, new, 1)

    old = '''	rc = dsi_ctrl_copy_and_pad_cmd(dsi_ctrl,
			&packet,
			&buffer,
			&length);
	if (rc) {
'''
    new = '''	rc = dsi_ctrl_copy_and_pad_cmd(dsi_ctrl,
			&packet,
			&buffer,
			&length);
	if (a52_p421_f0)
		a52_p421_survival_record(4U, A52_P421_V_RC, 0U, rc, 0U, 0);
	if (rc) {
'''
    if old not in fn:
        raise SystemExit("Phase421 copy-and-pad anchor missing")
    fn = fn.replace(old, new, 1)

    # Always clear target state on both success and pre-kickoff error exits.
    old = '''error:
	if (buffer)
		devm_kfree(&dsi_ctrl->pdev->dev, buffer);
	return rc;
'''
    new = '''error:
	if (buffer)
		devm_kfree(&dsi_ctrl->pdev->dev, buffer);
	if (a52_p421_f0)
		atomic_set(&a52_p421_target, 0);
	return rc;
'''
    if old not in fn:
        raise SystemExit("Phase421 message exit anchor missing")
    fn = fn.replace(old, new, 1)
    text = text[:start] + fn + text[end:]

    # Suppress every legacy deep-active observer inside the actual kickoff and
    # wait functions while the P421 target is active. These are diagnostics,
    # not stock transfer behavior, and include the Phase278/279 SMMU/MMIO reads.
    for sig in ("static void dsi_kickoff_msg_tx(",
                "static void dsi_ctrl_dma_cmd_wait_for_done"):
        start, end = function_bounds(text, sig)
        fn = text[start:end]
        if "a52_p276r_deep_active()" in fn:
            fn = fn.replace("a52_p276r_deep_active()",
                "(a52_p276r_deep_active() && !a52_p421_target_active())")
        text = text[:start] + fn + text[end:]

    # S05: immediately before stock video-done wait / IRQ preparation.
    start, end = function_bounds(text, "static void dsi_kickoff_msg_tx(")
    fn = text[start:end]
    old = '''	if (!(flags & DSI_CTRL_CMD_DEFER_TRIGGER)) {
		dsi_ctrl_wait_for_video_done(dsi_ctrl);
'''
    new = '''	if (!(flags & DSI_CTRL_CMD_DEFER_TRIGGER)) {
		if (a52_p421_target_active())
			a52_p421_survival_record(5U, 0U, 0U, 0, 0U, 0);
		dsi_ctrl_wait_for_video_done(dsi_ctrl);
'''
    if old not in fn:
        raise SystemExit("Phase421 S05 kickoff anchor missing")
    fn = fn.replace(old, new, 1)

    old = '''		reinit_completion(&dsi_ctrl->irq_info.cmd_dma_done);

		if (flags & DSI_CTRL_CMD_FETCH_MEMORY) {
'''
    new = '''		reinit_completion(&dsi_ctrl->irq_info.cmd_dma_done);
		if (a52_p421_target_active())
			a52_p421_survival_record(6U, A52_P421_V_IRQ, 0U, 0,
				(unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig), 0);

		if (flags & DSI_CTRL_CMD_FETCH_MEMORY) {
'''
    if old not in fn:
        raise SystemExit("Phase421 S06 IRQ-arm anchor missing")
    fn = fn.replace(old, new, 1)
    text = text[:start] + fn + text[end:]

    # S10: completion wait returned. This is intentionally before the inherited
    # Phase411 timeout panic.
    start, end = function_bounds(text, "static void dsi_ctrl_dma_cmd_wait_for_done")
    fn = text[start:end]
    wait = '''	ret = wait_for_completion_timeout(
			&dsi_ctrl->irq_info.cmd_dma_done,
			msecs_to_jiffies(DSI_CTRL_TX_TO_MS));
'''
    if wait not in fn:
        raise SystemExit("Phase421 completion wait anchor missing")
    fn = fn.replace(wait, wait + '''	if (a52_p421_target_active())
		a52_p421_survival_record(10U, A52_P421_V_IRQ | A52_P421_V_RET,
			0U, 0, (unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig),
			ret);
''', 1)
    text = text[:start] + fn + text[end:]

    # S09: DMA_DONE really reached the ISR. Record after the real atomic is set
    # but before disable/complete.
    start, end = function_bounds(text, "static irqreturn_t dsi_ctrl_isr(")
    fn = text[start:end]
    old = '''	if (status & DSI_CMD_MODE_DMA_DONE) {
		atomic_set(&dsi_ctrl->dma_irq_trig, 1);
		dsi_ctrl_disable_status_interrupt(dsi_ctrl,
'''
    new = '''	if (status & DSI_CMD_MODE_DMA_DONE) {
		atomic_set(&dsi_ctrl->dma_irq_trig, 1);
		if (a52_p421_target_active())
			a52_p421_survival_record(9U, A52_P421_V_IRQ, 0U, 0,
				(unsigned int)atomic_read(&dsi_ctrl->dma_irq_trig), 0);
		dsi_ctrl_disable_status_interrupt(dsi_ctrl,
'''
    if old not in fn:
        raise SystemExit("Phase421 ISR anchor missing")
    fn = fn.replace(old, new, 1)
    text = text[:start] + fn + text[end:]

    text += "\n/* " + MARK + ": stock memory path + corruption-tolerant frontier. */\n"
    return text


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1" not in text:
        raise SystemExit("Phase421 HWC requires Phase411 observer retirement")

    inc = '#include "sde_dbg.h"\n'
    decl = (
        '#include "sde_dbg.h"\n\n'
        '/* A52_PHASE421_FETCH_MEMORY_SURVIVAL_V1 */\n'
        'extern bool a52_p421_target_active(void);\n'
        'extern void a52_p421_survival_record(u8 stage, u8 valid, u32 flags,\n'
        '\t\t\t\t     int rc, u32 irq, int ret);\n'
    )
    if "extern bool a52_p421_target_active(void);" not in text:
        text = one(text, inc, decl, "HWC declarations")

    # Phase411 deliberately reduced this hot path to the real trigger write.
    # Add only persistent software breadcrumbs, with S07 after DMA programming
    # + wmb and S08 after the MMIO SW_TRIGGER write returns.
    old = '''	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
'''
    new = '''	if (!(flags & DSI_CTRL_HW_CMD_WAIT_FOR_TRIGGER)) {
		if (a52_p421_target_active())
			a52_p421_survival_record(7U, 0U, 0U, 0, 0U, 0);
		DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);
		if (a52_p421_target_active())
			a52_p421_survival_record(8U, 0U, 0U, 0, 0U, 0);
'''
    text = one(text, old, new, "S07/S08 trigger bracket")
    text += "\n/* " + MARK + ": S07/S08 SW_TRIGGER bracket. */\n"
    return text


def validate(root: Path) -> None:
    rec = (root / REC).read_text(errors="replace")
    ctrl = (root / CTRL).read_text(errors="replace")
    hwc = (root / HWC).read_text(errors="replace")

    for token in (
        MARK,
        "#define A52_P421_OFF0              0x00000500U",
        "#define A52_P421_OFF1              0x00000A80U",
        "#define A52_P421_OFF2              0x00000800U",
        "u8 stage_a[32];",
        "u32 nonce[5];",
        "u8 stage_b[32];",
        "a52_p421_get_nonce",
        "get_random_u32()",
        "__flush_dcache_area((void __force *)dst, sizeof(*slot));",
        "dsb(sy);",
        "a52_p421_copy_persist(dst, &slot);",
        "EXPORT_SYMBOL_GPL(a52_p421_survival_record);",
        "Phase421 owns the Phase405 fixed slots",
    ):
        if token not in rec:
            raise SystemExit("Phase421 recorder token missing: " + token)

    if "a52_p405_dsi_mirror(dst); /* A52_PHASE405_DMA_DONE_DIRECT_V1 */" in rec:
        raise SystemExit("Phase421 legacy Phase405 slot writer still active")

    for token in (
        MARK,
        "static atomic_t a52_p421_target = ATOMIC_INIT(0);",
        "bool a52_p421_target_active(void)",
        "a52_p421_survival_record(0U, A52_P421_V_FLAGS",
        "a52_p421_survival_record(1U, A52_P421_V_FLAGS",
        "a52_p421_survival_record(2U, A52_P421_V_FLAGS | A52_P421_V_RC",
        "a52_p421_survival_record(3U, A52_P421_V_RC",
        "a52_p421_survival_record(4U, A52_P421_V_RC",
        "a52_p421_survival_record(5U, 0U",
        "a52_p421_survival_record(6U, A52_P421_V_IRQ",
        "a52_p421_survival_record(9U, A52_P421_V_IRQ",
        "a52_p421_survival_record(10U, A52_P421_V_IRQ | A52_P421_V_RET",
        "(a52_p276r_deep_active() && !a52_p421_target_active())",
    ):
        if token not in ctrl:
            raise SystemExit("Phase421 CTRL token missing: " + token)

    if "(cmd_len + 4) <= DSI_CTRL_MAX_CMD_FIFO_STORE_SIZE" in ctrl:
        raise SystemExit("Phase421 Phase411 short-command FIFO treatment remains")

    # The stock secure-mode FIFO fallback must still exist.
    setup_start, setup_end = function_bounds(ctrl, "void dsi_message_setup_tx_mode(")
    setup = ctrl[setup_start:setup_end]
    if "if (dsi_ctrl->secure_mode)" not in setup or        "*flags |= DSI_CTRL_CMD_FIFO_STORE" not in setup:
        raise SystemExit("Phase421 stock secure-mode FIFO safeguard lost")

    for token in (
        MARK,
        "extern bool a52_p421_target_active(void);",
        "a52_p421_survival_record(7U, 0U",
        "DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);",
        "a52_p421_survival_record(8U, 0U",
    ):
        if token not in hwc:
            raise SystemExit("Phase421 HWC token missing: " + token)

    p7 = hwc.index("a52_p421_survival_record(7U, 0U")
    trig = hwc.index("DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);", p7)
    p8 = hwc.index("a52_p421_survival_record(8U, 0U", trig)
    if not p7 < trig < p8:
        raise SystemExit("Phase421 S07/S08 ordering invalid")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (REC, CTRL, HWC):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase421 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / REC
        p.write_text(patch_rec(p.read_text(errors="replace")))
        p = ns.root / CTRL
        p.write_text(patch_ctrl(p.read_text(errors="replace")))
        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase421 FETCH_MEMORY + survival lane: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
