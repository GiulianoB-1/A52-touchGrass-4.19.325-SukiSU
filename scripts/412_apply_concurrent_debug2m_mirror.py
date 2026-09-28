#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE412_CONCURRENT_DEBUG2M_MIRROR_V1"
CTRL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase412 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, sig: str) -> tuple[int, int]:
    start = text.find(sig)
    if start < 0:
        raise SystemExit(f"Phase412 function missing: {sig}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase412 opening brace missing: {sig}")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase412 closing brace missing: {sig}")


CONCURRENT_BLOCK = r'''
/* A52_PHASE412_CONCURRENT_DEBUG2M_MIRROR_V1
 *
 * The 1 MiB RAM recorder remains the hot path. Every committed software event
 * only updates RAM, then nudges an unbound delayed-work mirror. The worker
 * snapshots the current RAM image and writes the 2 MiB CRC32C image to
 * /dev/block/by-name/debug + 0x800000 in parallel with further RAM recording.
 *
 * No submit_bio_wait() is executed from the DSI transaction path.
 */
static atomic_t a52_p412_mirror_gen = ATOMIC_INIT(0);
static atomic_t a52_p412_flushed_gen = ATOMIC_INIT(0);
static atomic_t a52_p412_retry_count = ATOMIC_INIT(0);

static void a52_p412_soft_event(u16 stage, u32 flags)
{
	struct a52_p409_event *e;
	int index;

	if (!READ_ONCE(a52_p409_hot))
		return;

	index = atomic_inc_return(&a52_p409_index) - 1;
	if (index < 0 || index >= A52_P409_EVENT_CAPACITY) {
		atomic_inc(&a52_p409_dropped);
		return;
	}

	e = &a52_p409_hot[index];
	memset(e, 0, sizeof(*e));
	e->ns = ktime_get_ns();
	e->seq = (u32)index + 1U;
	e->stage = stage;
	e->cpu = (u16)raw_smp_processor_id();
	e->aux0 = flags;
	e->aux1 = 0x41200000U | (u32)stage;
	wmb();
	e->commit = A52_P409_EVENT_COMMIT;
}

static void a52_p412_mirror_workfn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_p412_mirror_work, a52_p412_mirror_workfn);

static void a52_p412_queue_mirror(unsigned long delay_ms)
{
	atomic_inc(&a52_p412_mirror_gen);
	mod_delayed_work(system_unbound_wq, &a52_p412_mirror_work,
			 msecs_to_jiffies(delay_ms));
}

static void a52_p412_mirror_workfn(struct work_struct *work)
{
	int generation;
	int retry;

	(void)work;
	generation = atomic_read(&a52_p412_mirror_gen);

	if (!READ_ONCE(a52_p409_hot) || !READ_ONCE(a52_p409_disk)) {
		retry = atomic_inc_return(&a52_p412_retry_count);
		if (retry <= 240)
			mod_delayed_work(system_unbound_wq,
				&a52_p412_mirror_work, msecs_to_jiffies(250));
		return;
	}

	/*
	 * Snapshot + block I/O runs here, never in the DSI caller. Events may
	 * continue arriving in the 1 MiB hot buffer while this worker writes its
	 * private 2 MiB image.
	 */
	a52_p409_write_workfn(NULL);

	if (atomic_read(&a52_p409_writer_state) == 2) {
		atomic_set(&a52_p412_flushed_gen, generation);
		atomic_set(&a52_p412_retry_count, 0);
		if (atomic_read(&a52_p412_mirror_gen) != generation)
			mod_delayed_work(system_unbound_wq,
				&a52_p412_mirror_work, 0);
		return;
	}

	retry = atomic_inc_return(&a52_p412_retry_count);
	if (retry <= 240)
		mod_delayed_work(system_unbound_wq, &a52_p412_mirror_work,
			 msecs_to_jiffies(250));
}

static void a52_p412_alive_workfn(struct work_struct *work)
{
	(void)work;
	a52_p412_soft_event(13U, 0U); /* alive +1 s */
	a52_p412_queue_mirror(0);
}

static DECLARE_DELAYED_WORK(a52_p412_alive_work, a52_p412_alive_workfn);

void a52_p412_persist_bootstrap(void)
{
	if (!READ_ONCE(a52_p409_hot) || !READ_ONCE(a52_p409_disk))
		return;

	/* Guarantees a current-boot disk image even if exact F0 is never reached. */
	a52_p412_soft_event(14U, 0x41200001U);
	a52_p412_queue_mirror(0);
	mod_delayed_work(system_unbound_wq, &a52_p412_alive_work,
			 msecs_to_jiffies(1000));
}

void a52_p412_persist_mode_event(u16 stage, u32 flags)
{
	if (!READ_ONCE(a52_p409_hot) || !READ_ONCE(a52_p409_disk))
		return;
	if (stage != 11U && stage != 12U)
		return;

	a52_p412_soft_event(stage, flags);
	a52_p412_queue_mirror(0);
}
'''


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1" not in text:
        raise SystemExit("Phase412 requires Phase411 lineage")

    text = one(
        text,
        "extern void a52_p411_persist_mode_event(u16 stage, u32 flags);\n",
        "extern void a52_p412_persist_bootstrap(void); /* " + MARK + " */\n"
        "extern void a52_p412_persist_mode_event(u16 stage, u32 flags);\n",
        "persistent declarations",
    )
    text = text.replace("a52_p411_persist_mode_event(11U",
                        "a52_p412_persist_mode_event(11U")
    text = text.replace("a52_p411_persist_mode_event(12U",
                        "a52_p412_persist_mode_event(12U")

    text = one(
        text,
        "\ta52_p409_init_buffers();\n"
        "\t/* Phase411: legacy sideband recorder retired; debug2m writer retained. */\n",
        "\ta52_p409_init_buffers();\n"
        "\ta52_p412_persist_bootstrap();\n"
        "\t/* Phase412: hot RAM + concurrent Samsung debug2m mirror. */\n",
        "bootstrap hook",
    )

    text += (
        "\n/* " + MARK + ": concurrent 1MiB RAM / 2MiB Samsung mirror enabled. */\n"
    )
    return text


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE411_FIFO_PANIC_SMMU_EXPERIMENT_V1" not in text:
        raise SystemExit("Phase412 requires Phase411 HW lineage")

    if "#include <linux/workqueue.h>\n" not in text:
        text = one(
            text,
            "#include <linux/vmalloc.h>\n",
            "#include <linux/vmalloc.h>\n#include <linux/workqueue.h>\n",
            "workqueue include",
        )

    text = one(text, "h->phase = 411U;\n", "h->phase = 412U;\n",
               "disk image phase")

    start = text.find("static atomic_t a52_p411_persist_state = ATOMIC_INIT(0);")
    if start < 0:
        raise SystemExit("Phase412 Phase411 persistence block start missing")
    _, end = function_bounds(text, "void a52_p411_persist_mode_event(u16 stage, u32 flags)")
    text = text[:start] + CONCURRENT_BLOCK + text[end:]

    text += (
        "\n/* " + MARK + ": background mirror is independent of F0 reachability. */\n"
        "static const char a52_p412_marker[] __used = \"" + MARK + "\";\n"
    )
    return text


def validate(root: Path) -> None:
    ctrl = (root / CTRL).read_text(errors="replace")
    hwc = (root / HWC).read_text(errors="replace")

    for token in (
        MARK,
        "a52_p409_init_buffers();",
        "a52_p412_persist_bootstrap();",
        "a52_p412_persist_mode_event(11U",
        "a52_p412_persist_mode_event(12U",
    ):
        if token not in ctrl:
            raise SystemExit("Phase412 CTRL token missing: " + token)
    if "a52_p411_persist_mode_event(11U" in ctrl or \
       "a52_p411_persist_mode_event(12U" in ctrl:
        raise SystemExit("Phase412 old Phase411 persistence calls remain")

    for token in (
        MARK,
        "h->phase = 412U;",
        "system_unbound_wq",
        "a52_p412_soft_event(14U, 0x41200001U);",
        "a52_p412_soft_event(13U, 0U);",
        "a52_p409_write_workfn(NULL);",
        "a52_p412_queue_mirror(0);",
        "atomic_read(&a52_p412_mirror_gen) != generation",
    ):
        if token not in hwc:
            raise SystemExit("Phase412 HWC token missing: " + token)

    if "static atomic_t a52_p411_persist_state" in hwc:
        raise SystemExit("Phase412 old one-shot Phase411 persistence state remains")
    if "atomic_set(&a52_p409_frozen, 1);" in hwc:
        raise SystemExit("Phase412 must not freeze the hot RAM recorder")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (CTRL, HWC):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase412 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / CTRL
        p.write_text(patch_ctrl(p.read_text(errors="replace")))
        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase412 concurrent debug2m mirror: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
