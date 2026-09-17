#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
CORE = Path("drivers/cpuidle/cpuidle.c")
IDLE = Path("kernel/sched/idle.c")
MARK = "A52_PHASE352_CPUIDLE_IDENTITY_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase352 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE352_CPUIDLE_IDENTITY_V1
 *
 * Phase351 proved the configured cpuidle-psci/domain backend is not entered
 * before the ~12 s freeze. Probe one layer higher with FIRST-OCCURRENCE-only
 * persistence so normal idle timing is disturbed as little as possible.
 *
 * Dedicated raw sideband:
 *   0xB1BF0000..0xB1BF3FFF, two mirrored 8 KiB copies.
 * 16 events x 8 CPUs x 64 bytes = 8 KiB per mirror.
 */
#define A52_R352_SIDEBAND_PHYS  0xB1BF0000ULL
#define A52_R352_SIDEBAND_BYTES 0x4000U
#define A52_R352_COPY_BYTES     0x2000U
#define A52_R352_SLOT_BYTES     64U
#define A52_R352_EVENT_COUNT    16U
#define A52_R352_CPU_COUNT      8U
#define A52_R352_MAGIC          0x323533454c444943ULL
#define A52_R352_COMMIT         0x352c0de5U

struct a52_r352_slot {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 callback;
	u32 event;
	u32 cpu;
	s32 index;
	s32 ret;
	u32 flags;
	u32 drv_sig;
	u32 commit;
	u32 version;
};

static void *a52_r352_sideband;
static unsigned long a52_r352_seen[A52_R352_CPU_COUNT];

static u32 a52_r352_sig4(const char *name)
{
	u32 v = 0;

	if (!name)
		return 0;
	if (name[0]) v |= (u32)(u8)name[0];
	if (name[1]) v |= (u32)(u8)name[1] << 8;
	if (name[2]) v |= (u32)(u8)name[2] << 16;
	if (name[3]) v |= (u32)(u8)name[3] << 24;
	return v;
}

void a52_p352_mark_first(u32 event, s32 index, s32 ret, u32 flags,
	const char *drv_name, const void *callback)
{
	struct a52_r352_slot slot;
	unsigned int cpu;
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r352_sideband))
		return;
	cpu = (unsigned int)raw_smp_processor_id();
	if (event >= A52_R352_EVENT_COUNT || cpu >= A52_R352_CPU_COUNT)
		return;
	if (test_and_set_bit(event, &a52_r352_seen[cpu]))
		return;

	BUILD_BUG_ON(sizeof(struct a52_r352_slot) != A52_R352_SLOT_BYTES);
	BUILD_BUG_ON(A52_R352_EVENT_COUNT * A52_R352_CPU_COUNT *
		A52_R352_SLOT_BYTES != A52_R352_COPY_BYTES);

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_R352_MAGIC;
	slot.ns = ktime_get_ns();
	slot.sequence = (u64)atomic64_read(&a52_r179_sequence);
	slot.callback = (u64)(unsigned long)callback;
	slot.event = event;
	slot.cpu = cpu;
	slot.index = index;
	slot.ret = ret;
	slot.flags = flags;
	slot.drv_sig = a52_r352_sig4(drv_name);
	slot.commit = A52_R352_COMMIT;
	slot.version = 1U;

	pos = (event * A52_R352_CPU_COUNT + cpu) * A52_R352_SLOT_BYTES;
	dst0 = (u8 *)a52_r352_sideband + pos;
	dst1 = (u8 *)a52_r352_sideband + A52_R352_COPY_BYTES + pos;
	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
}

static void a52_r352_start(void)
{
	a52_r352_sideband = memremap(A52_R352_SIDEBAND_PHYS,
		A52_R352_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r352_sideband) {
		a52_ackfr_record("P276 352A map=0");
		return;
	}

	memset(a52_r352_sideband, 0, A52_R352_SIDEBAND_BYTES);
	memset(a52_r352_seen, 0, sizeof(a52_r352_seen));
	wmb();
	__flush_dcache_area(a52_r352_sideband, A52_R352_SIDEBAND_BYTES);
	a52_p352_mark_first(0U, -1, 0, 0U, "INIT", NULL);
	a52_ackfr_record("P276 352A map=1");
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1" not in text:
        raise SystemExit("Phase352 requires Phase343 recorder lineage")
    anchor = "static void *a52_r343_sideband;\n"
    text = one(text, anchor, RAW_BLOCK + anchor, "raw helper insertion")
    text = one(text,
               "\ta52_r343_start();\n",
               "\ta52_r352_start();\n\ta52_r343_start();\n",
               "late-init mapping")
    return text


def patch_core(text: str) -> str:
    if MARK in text:
        return text
    inc = '#include "cpuidle.h"\n'
    text = one(text, inc,
               inc + '\nextern void a52_p352_mark_first(u32 event, s32 index, s32 ret, u32 flags, const char *drv_name, const void *callback); /* ' + MARK + ' */\n',
               "core extern")

    # Capture the first use of each idle state index at the generic wrapper.
    # This runs before coupled/non-coupled dispatch and therefore identifies
    # the actual registered backend even if target_state->enter is not called
    # directly by cpuidle_enter_state().
    anchor = "int cpuidle_enter(struct cpuidle_driver *drv, struct cpuidle_device *dev,\n\t\t  int index)\n{\n"
    if anchor not in text:
        # Some 5.10 drops use one less alignment space.
        anchor = "int cpuidle_enter(struct cpuidle_driver *drv, struct cpuidle_device *dev,\n\t\t int index)\n{\n"
    if text.count(anchor) != 1:
        raise SystemExit(f"Phase352 cpuidle_enter anchor count {text.count(anchor)}")
    repl = anchor + (
        "\tif (index >= 0 && index < 8)\n"
        "\t\ta52_p352_mark_first(4U + (u32)index, index, 0,\n"
        "\t\t\tdrv->states[index].flags, drv->name,\n"
        "\t\t\t(const void *)drv->states[index].enter);\n"
        "\ta52_p352_mark_first(15U, index, 0,\n"
        "\t\t(index >= 0 && index < drv->state_count) ? drv->states[index].flags : 0U,\n"
        "\t\tdrv ? drv->name : NULL,\n"
        "\t\t(index >= 0 && index < drv->state_count) ? (const void *)drv->states[index].enter : NULL);\n"
    )
    text = text.replace(anchor, repl, 1)

    # First direct backend callback and first return. These are intentionally
    # FIRST-only; Phase353 can bracket the identified backend on every entry.
    old = "\tentered_state = target_state->enter(dev, drv, index);\n"
    new = (
        "\ta52_p352_mark_first(12U, index, 0, target_state->flags, drv->name,\n"
        "\t\t(const void *)target_state->enter);\n"
        "\tentered_state = target_state->enter(dev, drv, index);\n"
        "\ta52_p352_mark_first(13U, index, entered_state, target_state->flags, drv->name,\n"
        "\t\t(const void *)target_state->enter);\n"
    )
    text = one(text, old, new, "backend callback boundary")
    return text


def patch_idle(text: str) -> str:
    if MARK in text:
        return text
    # kernel/sched/idle.c in this Android 5.10 tree gets cpuidle types through
    # sched.h and does not include <linux/cpuidle.h> directly. Anchor the
    # telemetry declaration after the Android scheduler hook include instead.
    inc = "#include <trace/hooks/sched.h>\n"
    text = one(text, inc,
               inc + '\nextern void a52_p352_mark_first(u32 event, s32 index, s32 ret, u32 flags, const char *drv_name, const void *callback); /* ' + MARK + ' */\n',
               "idle extern")

    # If cpuidle is unavailable, record that before falling back.
    old = "\tif (cpuidle_not_available(drv, dev)) {\n"
    new = (
        "\tif (cpuidle_not_available(drv, dev)) {\n"
        "\t\ta52_p352_mark_first(3U, -1, 0, 0U, drv ? drv->name : NULL, NULL);\n"
    )
    text = one(text, old, new, "cpuidle unavailable")

    # Record the governor's first selected state without changing selection.
    old = "\tnext_state = cpuidle_select(drv, dev, &stop_tick);\n"
    new = (
        "\tnext_state = cpuidle_select(drv, dev, &stop_tick);\n"
        "\ta52_p352_mark_first(14U, next_state, stop_tick ? 1 : 0,\n"
        "\t\t(next_state >= 0 && next_state < drv->state_count) ? drv->states[next_state].flags : 0U,\n"
        "\t\tdrv ? drv->name : NULL,\n"
        "\t\t(next_state >= 0 && next_state < drv->state_count) ? (const void *)drv->states[next_state].enter : NULL);\n"
    )
    text = one(text, old, new, "selection result")

    # The default fallback is important because Phase351 saw no PSCI backend.
    old = "\t\tarch_cpu_idle();\n"
    new = (
        "\t\ta52_p352_mark_first(1U, -1, 0, 0U, \"ARCH\", (const void *)arch_cpu_idle);\n"
        "\t\tarch_cpu_idle();\n"
        "\t\ta52_p352_mark_first(2U, -1, 0, 0U, \"ARCH\", (const void *)arch_cpu_idle);\n"
    )
    text = one(text, old, new, "default arch idle boundary")
    return text


def validate(br: str, ar: str, bc: str, ac: str, bi: str, ai: str) -> None:
    joined = ar + ac + ai
    for token in (
        MARK,
        "A52_R352_SIDEBAND_PHYS  0xB1BF0000ULL",
        "A52_R352_COMMIT         0x352c0de5U",
        "a52_r352_start();",
        "a52_p352_mark_first(1U",
        "a52_p352_mark_first(3U",
        "a52_p352_mark_first(4U + (u32)index",
        "a52_p352_mark_first(12U",
        "a52_p352_mark_first(14U",
    ):
        if token not in joined:
            raise SystemExit("Phase352 required token missing: " + token)

    # Observation only: do not add/remove the actual idle transitions.
    for token in (
        "arch_cpu_idle();",
        "cpuidle_select(drv, dev, &stop_tick)",
        "target_state->enter(dev, drv, index)",
        "cpuidle_state_is_coupled(drv, index)",
    ):
        before = bc + bi
        after = ac + ai
        if before.count(token) != after.count(token):
            raise SystemExit("Phase352 changed protected call count: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    files = {p: ns.root / p for p in (REC, CORE, IDLE)}
    for p, f in files.items():
        if not f.is_file():
            raise SystemExit("Phase352 source missing: " + str(p))
    src = {p: files[p].read_text() for p in files}

    if all(MARK in src[p] for p in files):
        print("Phase352 cpuidle identity audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase352 marker missing in check-only mode")

    out = dict(src)
    out[REC] = patch_rec(src[REC])
    out[CORE] = patch_core(src[CORE])
    out[IDLE] = patch_idle(src[IDLE])
    validate(src[REC], out[REC], src[CORE], out[CORE], src[IDLE], out[IDLE])
    for p in files:
        files[p].write_text(out[p])

    print("Phase352 generic cpuidle identity applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
