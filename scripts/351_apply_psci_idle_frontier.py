#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
PSCI = Path("drivers/cpuidle/cpuidle-psci.c")
MARK = "A52_PHASE351_PSCI_IDLE_FRONTIER_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase351 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


RAW_BLOCK = r'''
/* A52_PHASE351_PSCI_IDLE_FRONTIER_V1
 *
 * Phase350 excluded stop_machine/cpu-stopper as the ~12 s global-freeze
 * mechanism. TouchGrass 4.19 uses the older ARM cpuidle + Qualcomm MSM_PM /
 * QTI_SYSTEM_PM architecture, while this 5.10 GKI is configured with
 * ARM_PSCI_CPUIDLE_DOMAIN. Probe the newer hierarchical PSCI-idle path.
 *
 * Dedicated raw sideband (currently unused pmsg tail):
 *   0xB1BF4000..0xB1BF7FFF, two mirrored 8 KiB copies.
 *
 * Layout is a fixed event x CPU matrix. Each cell stores the LAST occurrence
 * of that event on that CPU. Comparing timestamps between ENTER/RETURN cells
 * therefore identifies a final non-returning low-power transition.
 */
#define A52_R351_SIDEBAND_PHYS  0xB1BF4000ULL
#define A52_R351_SIDEBAND_BYTES 0x4000U
#define A52_R351_COPY_BYTES     0x2000U
#define A52_R351_SLOT_BYTES     64U
#define A52_R351_EVENT_COUNT    16U
#define A52_R351_CPU_COUNT      8U
#define A52_R351_MAGIC          0x313533454c444950ULL
#define A52_R351_COMMIT         0x351c0de5U

struct a52_r351_slot {
	u64 magic;
	u64 ns;
	u64 sequence;
	u64 jiffies64;
	u32 event;
	u32 cpu;
	u32 pid;
	u32 tgid;
	u32 state;
	u32 arg0;
	u32 commit;
	u32 version;
};

static void *a52_r351_sideband;

void a52_p351_mark(u32 event, u32 state, u32 arg0)
{
	struct a52_r351_slot slot;
	unsigned int cpu;
	unsigned int pos;
	void *dst0;
	void *dst1;

	if (!READ_ONCE(a52_r351_sideband))
		return;
	cpu = (unsigned int)raw_smp_processor_id();
	if (event >= A52_R351_EVENT_COUNT || cpu >= A52_R351_CPU_COUNT)
		return;

	BUILD_BUG_ON(sizeof(struct a52_r351_slot) != A52_R351_SLOT_BYTES);
	BUILD_BUG_ON(A52_R351_EVENT_COUNT * A52_R351_CPU_COUNT *
		A52_R351_SLOT_BYTES != A52_R351_COPY_BYTES);

	memset(&slot, 0, sizeof(slot));
	slot.magic = A52_R351_MAGIC;
	slot.ns = ktime_get_ns();
	slot.sequence = (u64)atomic64_read(&a52_r179_sequence);
	slot.jiffies64 = get_jiffies_64();
	slot.event = event;
	slot.cpu = cpu;
	slot.pid = (u32)current->pid;
	slot.tgid = (u32)current->tgid;
	slot.state = state;
	slot.arg0 = arg0;
	slot.commit = A52_R351_COMMIT;
	slot.version = 1U;

	pos = (event * A52_R351_CPU_COUNT + cpu) * A52_R351_SLOT_BYTES;
	dst0 = (u8 *)a52_r351_sideband + pos;
	dst1 = (u8 *)a52_r351_sideband + A52_R351_COPY_BYTES + pos;
	memcpy(dst0, &slot, sizeof(slot));
	memcpy(dst1, &slot, sizeof(slot));
	wmb();
	__flush_dcache_area(dst0, sizeof(slot));
	__flush_dcache_area(dst1, sizeof(slot));
}

static void a52_r351_start(void)
{
	a52_r351_sideband = memremap(A52_R351_SIDEBAND_PHYS,
		A52_R351_SIDEBAND_BYTES, MEMREMAP_WB);
	if (!a52_r351_sideband) {
		a52_ackfr_record("P276 351A map=0");
		return;
	}

	memset(a52_r351_sideband, 0, A52_R351_SIDEBAND_BYTES);
	wmb();
	__flush_dcache_area(a52_r351_sideband, A52_R351_SIDEBAND_BYTES);
	a52_p351_mark(0U, 0U, 0U);
	a52_ackfr_record("P276 351A map=1");
}

'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1" not in text:
        raise SystemExit("Phase351 requires Phase343 recorder lineage")

    anchor = "static void *a52_r343_sideband;\n"
    if anchor not in text:
        raise SystemExit("Phase351 Phase343 sideband anchor missing")
    text = one(text, anchor, RAW_BLOCK + anchor, "raw sideband helper")
    text = one(text,
               "\ta52_r343_start();\n",
               "\ta52_r343_start();\n\ta52_r351_start();\n",
               "late-init mapping")
    return text


def patch_psci(text: str) -> str:
    if MARK in text:
        return text

    required = (
        "static int psci_enter_domain_idle_state(",
        "ret = cpu_pm_enter();",
        "pm_runtime_put_sync_suspend(pd_dev);",
        "ret = psci_cpu_suspend_enter(state) ? -1 : idx;",
        "pm_runtime_get_sync(pd_dev);",
        "cpu_pm_exit();",
        "static int psci_enter_idle_state(",
        "static int psci_idle_cpuhp_up(unsigned int cpu)",
        "static int psci_idle_cpuhp_down(unsigned int cpu)",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase351 PSCI prerequisite missing: " + token)

    inc = '#include "dt_idle_states.h"\n'
    text = one(text, inc,
               inc + '\nextern void a52_p351_mark(u32 event, u32 state, u32 arg0); /* ' + MARK + ' */\n',
               "PSCI extern")

    # Hierarchical domain path. Keep declarations before statements: introduce
    # one telemetry-only local for the return value that upstream discards.
    text = one(text,
               "\tu32 state;\n\tint ret;\n\n\tret = cpu_pm_enter();\n",
               "\tu32 state;\n\tint ret;\n\tint p351_pmret;\n\n"
               "\ta52_p351_mark(3U, (u32)idx, 0U);\n"
               "\ta52_p351_mark(4U, (u32)idx, 0U);\n"
               "\tret = cpu_pm_enter();\n"
               "\ta52_p351_mark(5U, (u32)idx, (u32)ret);\n",
               "domain/cpu_pm entry")

    text = one(text,
               "\tif (s2idle)\n"
               "\t\tdev_pm_genpd_suspend(pd_dev);\n"
               "\telse\n"
               "\t\tpm_runtime_put_sync_suspend(pd_dev);\n",
               "\tif (s2idle)\n"
               "\t\tdev_pm_genpd_suspend(pd_dev);\n"
               "\telse {\n"
               "\t\ta52_p351_mark(6U, (u32)idx, 0U);\n"
               "\t\tp351_pmret = pm_runtime_put_sync_suspend(pd_dev);\n"
               "\t\ta52_p351_mark(7U, (u32)idx, (u32)p351_pmret);\n"
               "\t}\n",
               "runtime PM put boundary")

    text = one(text,
               "\tret = psci_cpu_suspend_enter(state) ? -1 : idx;\n",
               "\ta52_p351_mark(8U, state, (u32)idx);\n"
               "\tret = psci_cpu_suspend_enter(state) ? -1 : idx;\n"
               "\ta52_p351_mark(9U, state, (u32)ret);\n",
               "PSCI suspend boundary")

    text = one(text,
               "\telse\n\t\tpm_runtime_get_sync(pd_dev);\n\n"
               "\ttrace_android_vh_cpuidle_psci_exit(dev, s2idle);\n",
               "\telse {\n"
               "\t\ta52_p351_mark(10U, state, 0U);\n"
               "\t\tp351_pmret = pm_runtime_get_sync(pd_dev);\n"
               "\t\ta52_p351_mark(11U, state, (u32)p351_pmret);\n"
               "\t}\n\n"
               "\ttrace_android_vh_cpuidle_psci_exit(dev, s2idle);\n",
               "runtime PM get boundary")

    text = one(text,
               "\trcu_irq_exit_irqson();\n\n\tcpu_pm_exit();\n",
               "\trcu_irq_exit_irqson();\n\n"
               "\ta52_p351_mark(12U, state, 0U);\n"
               "\tcpu_pm_exit();\n"
               "\ta52_p351_mark(13U, state, (u32)ret);\n",
               "cpu_pm exit boundary")

    # Simple PSCI path: only trace the deepest simple state to avoid turning
    # every shallow idle/WFI into persistent writes.
    old_simple = (
        "static int psci_enter_idle_state(struct cpuidle_device *dev,\n"
        "\t\t\t\tstruct cpuidle_driver *drv, int idx)\n"
        "{\n"
        "\tu32 *state = __this_cpu_read(psci_cpuidle_data.psci_states);\n\n"
        "\treturn psci_enter_state(idx, state[idx]);\n"
        "}\n"
    )
    new_simple = (
        "static int psci_enter_idle_state(struct cpuidle_device *dev,\n"
        "\t\t\t\tstruct cpuidle_driver *drv, int idx)\n"
        "{\n"
        "\tu32 *state = __this_cpu_read(psci_cpuidle_data.psci_states);\n"
        "\tint ret;\n\n"
        "\tif (idx == drv->state_count - 1)\n"
        "\t\ta52_p351_mark(1U, state[idx], (u32)idx);\n"
        "\tret = psci_enter_state(idx, state[idx]);\n"
        "\tif (idx == drv->state_count - 1)\n"
        "\t\ta52_p351_mark(2U, state[idx], (u32)ret);\n"
        "\treturn ret;\n"
        "}\n"
    )
    text = one(text, old_simple, new_simple, "deepest simple PSCI state")

    # CPUHP callbacks share events 14/15; arg0 distinguishes up(1)/down(2).
    text = one(text,
               "static int psci_idle_cpuhp_up(unsigned int cpu)\n{\n"
               "\tstruct device *pd_dev = __this_cpu_read(psci_cpuidle_data.dev);\n\n",
               "static int psci_idle_cpuhp_up(unsigned int cpu)\n{\n"
               "\tstruct device *pd_dev = __this_cpu_read(psci_cpuidle_data.dev);\n"
               "\tint p351_ret = 0;\n\n"
               "\ta52_p351_mark(14U, cpu, 1U);\n",
               "CPUHP up enter")
    text = one(text,
               "\tif (pd_dev)\n\t\tpm_runtime_get_sync(pd_dev);\n\n\treturn 0;\n}\n",
               "\tif (pd_dev)\n\t\tp351_ret = pm_runtime_get_sync(pd_dev);\n\n"
               "\ta52_p351_mark(15U, cpu, 0x10000U | ((u32)p351_ret & 0xffffU));\n"
               "\treturn 0;\n}\n",
               "CPUHP up exit")

    text = one(text,
               "static int psci_idle_cpuhp_down(unsigned int cpu)\n{\n"
               "\tstruct device *pd_dev = __this_cpu_read(psci_cpuidle_data.dev);\n\n",
               "static int psci_idle_cpuhp_down(unsigned int cpu)\n{\n"
               "\tstruct device *pd_dev = __this_cpu_read(psci_cpuidle_data.dev);\n"
               "\tint p351_ret = 0;\n\n"
               "\ta52_p351_mark(14U, cpu, 2U);\n",
               "CPUHP down enter")
    text = one(text,
               "\tif (pd_dev) {\n\t\tpm_runtime_put_sync(pd_dev);\n"
               "\t\t/* Clear domain state to start fresh at next online. */\n",
               "\tif (pd_dev) {\n\t\tp351_ret = pm_runtime_put_sync(pd_dev);\n"
               "\t\t/* Clear domain state to start fresh at next online. */\n",
               "CPUHP down PM call")
    text = one(text,
               "\t}\n\n\treturn 0;\n}\n\nstatic void psci_idle_init_cpuhp(void)\n",
               "\t}\n\n"
               "\ta52_p351_mark(15U, cpu, 0x20000U | ((u32)p351_ret & 0xffffU));\n"
               "\treturn 0;\n}\n\nstatic void psci_idle_init_cpuhp(void)\n",
               "CPUHP down exit")
    return text


def validate(before_rec: str, after_rec: str, before_psci: str, after_psci: str) -> None:
    joined = after_rec + after_psci
    for token in (
        MARK,
        "A52_R351_SIDEBAND_PHYS  0xB1BF4000ULL",
        "A52_R351_COMMIT         0x351c0de5U",
        "a52_r351_start();",
        "a52_p351_mark(3U",
        "a52_p351_mark(6U",
        "a52_p351_mark(8U",
        "a52_p351_mark(10U",
        "a52_p351_mark(12U",
        "a52_p351_mark(14U",
    ):
        if token not in joined:
            raise SystemExit("Phase351 validation token missing: " + token)

    # Calls must remain exactly once: telemetry brackets existing behavior;
    # it must not add/remove an idle, PSCI, runtime-PM or cpu_pm transition.
    protected = (
        "cpu_pm_enter()",
        "pm_runtime_put_sync_suspend(pd_dev)",
        "psci_cpu_suspend_enter(state)",
        "pm_runtime_get_sync(pd_dev)",
        "cpu_pm_exit()",
        "psci_enter_state(idx, state[idx])",
        "pm_runtime_put_sync(pd_dev)",
    )
    for token in protected:
        if before_psci.count(token) != after_psci.count(token):
            raise SystemExit("Phase351 changed protected call count: " + token)

    for token in (
        "else {\n\t\ta52_p351_mark(6U",
        "else {\n\t\ta52_p351_mark(10U",
        "trace_android_vh_cpuidle_psci_exit(dev, s2idle);\n\n\trcu_irq_exit_irqson();\n\n\ta52_p351_mark(12U",
    ):
        if token not in after_psci:
            raise SystemExit("Phase351 control-flow validation missing: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    rp = ns.root / REC
    pp = ns.root / PSCI
    if not rp.is_file() or not pp.is_file():
        raise SystemExit("Phase351 required source missing")

    rec = rp.read_text()
    psci = pp.read_text()

    if MARK in rec and MARK in psci:
        for token in ("A52_R351_COMMIT", "a52_p351_mark(8U", "a52_p351_mark(13U"):
            if token not in rec + psci:
                raise SystemExit("Phase351 check-only token missing: " + token)
        print("Phase351 PSCI idle frontier audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase351 marker missing in check-only mode")

    nrec = patch_rec(rec)
    npsci = patch_psci(psci)
    validate(rec, nrec, psci, npsci)
    rp.write_text(nrec)
    pp.write_text(npsci)
    print("Phase351 PSCI idle frontier applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
