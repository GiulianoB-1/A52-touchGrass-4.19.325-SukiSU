#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 CPU UV P1: policy6 2208 MHz 924 -> 916 mV with MMIO readback"
TARGET_FREQ_KHZ = 2208000
STOCK_MV = 924
TARGET_MV = 916
TARGET_VC = 11
TARGET_POLICY_CPU = 6


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    src = root / "drivers/cpufreq/qcom-cpufreq-hw.c"
    text = src.read_text()

    if MARKER in text:
        print(f"[already] {src}: {MARKER}")
        return

    if "A52 CPU UV PROBE: read-only qcom-cpufreq-hw voltage LUT" not in text:
        raise SystemExit("CPU UV probe marker missing; apply script 110 first")

    status_anchor = """static struct freq_attr *qcom_cpufreq_hw_attr[] = {
\t&cpufreq_freq_attr_scaling_available_freqs,
\t&cpufreq_freq_attr_scaling_boost_freqs,
\t&a52_cpu_uv_probe,
\tNULL
};
"""

    status_block = r'''/*
 * A52 CPU UV P1: policy6 2208 MHz 924 -> 916 mV with MMIO readback
 *
 * This is intentionally limited to one hardware LUT entry. The write is only
 * attempted when every stock invariant matches: policy6, 2208 MHz, 924 mV,
 * and VC=11. Upper register bits are preserved. The register is read back
 * immediately; if the exact requested raw value is not observed, the original
 * raw value is restored and the driver continues using the restored voltage.
 */
#define A52_CPU_UV_P1_POLICY_CPU 6U
#define A52_CPU_UV_P1_FREQ_KHZ 2208000U
#define A52_CPU_UV_P1_STOCK_MV 924U
#define A52_CPU_UV_P1_TARGET_MV 916U
#define A52_CPU_UV_P1_VC 11U

static unsigned int a52_cpu_uv_p1_attempted;
static unsigned int a52_cpu_uv_p1_applied;
static unsigned int a52_cpu_uv_p1_policy_cpu;
static unsigned int a52_cpu_uv_p1_index;
static unsigned int a52_cpu_uv_p1_freq_khz;
static unsigned int a52_cpu_uv_p1_original_mv;
static unsigned int a52_cpu_uv_p1_target_mv = A52_CPU_UV_P1_TARGET_MV;
static unsigned int a52_cpu_uv_p1_readback_mv;
static unsigned int a52_cpu_uv_p1_vc;
static u32 a52_cpu_uv_p1_raw_before;
static u32 a52_cpu_uv_p1_raw_after;
static u32 a52_cpu_uv_p1_raw_final;

static ssize_t show_a52_cpu_uv_p1(struct cpufreq_policy *policy, char *buf)
{
	return scnprintf(buf, PAGE_SIZE,
		"attempted=%u applied=%u policy_cpu=%u idx=%u freq_khz=%u original_mv=%u target_mv=%u readback_mv=%u vc=%u raw_before=0x%08x raw_after=0x%08x raw_final=0x%08x\n",
		a52_cpu_uv_p1_attempted, a52_cpu_uv_p1_applied,
		a52_cpu_uv_p1_policy_cpu, a52_cpu_uv_p1_index,
		a52_cpu_uv_p1_freq_khz, a52_cpu_uv_p1_original_mv,
		a52_cpu_uv_p1_target_mv, a52_cpu_uv_p1_readback_mv,
		a52_cpu_uv_p1_vc, a52_cpu_uv_p1_raw_before,
		a52_cpu_uv_p1_raw_after, a52_cpu_uv_p1_raw_final);
}
cpufreq_freq_attr_ro(a52_cpu_uv_p1);

static struct freq_attr *qcom_cpufreq_hw_attr[] = {
	&cpufreq_freq_attr_scaling_available_freqs,
	&cpufreq_freq_attr_scaling_boost_freqs,
	&a52_cpu_uv_probe,
	&a52_cpu_uv_p1,
	NULL
};
'''

    text = replace_once(text, status_anchor, status_block, "CPU UV P1 status insertion")

    loop_anchor = """\t\tcur_freq = c->table[i].frequency;

\t\tdev_dbg(dev, \"index=%d freq=%d, core_count %d\\n\",
"""

    loop_block = r'''		cur_freq = c->table[i].frequency;

		if (!a52_cpu_uv_p1_attempted &&
				cpumask_first(&c->related_cpus) == A52_CPU_UV_P1_POLICY_CPU &&
				cur_freq == A52_CPU_UV_P1_FREQ_KHZ &&
				(data & GENMASK(11, 0)) == A52_CPU_UV_P1_STOCK_MV &&
				((data & GENMASK(21, 16)) >> 16) == A52_CPU_UV_P1_VC) {
			u32 raw_before = data;
			u32 raw_target = (raw_before & ~GENMASK(11, 0)) |
					A52_CPU_UV_P1_TARGET_MV;
			u32 raw_readback, raw_final;

			a52_cpu_uv_p1_attempted = 1;
			a52_cpu_uv_p1_policy_cpu = cpumask_first(&c->related_cpus);
			a52_cpu_uv_p1_index = i;
			a52_cpu_uv_p1_freq_khz = cur_freq;
			a52_cpu_uv_p1_original_mv = raw_before & GENMASK(11, 0);
			a52_cpu_uv_p1_vc = (raw_before & GENMASK(21, 16)) >> 16;
			a52_cpu_uv_p1_raw_before = raw_before;

			writel_relaxed(raw_target, base_volt + i * lut_row_size);
			raw_readback = readl_relaxed(base_volt + i * lut_row_size);
			a52_cpu_uv_p1_raw_after = raw_readback;
			a52_cpu_uv_p1_readback_mv = raw_readback & GENMASK(11, 0);

			if (raw_readback == raw_target) {
				a52_cpu_uv_p1_applied = 1;
				raw_final = raw_readback;
				dev_info(dev,
					"A52 CPU UV P1: policy%u idx=%u %u kHz %u -> %u mV VC=%u applied raw 0x%08x -> 0x%08x\n",
					a52_cpu_uv_p1_policy_cpu, i, cur_freq,
					a52_cpu_uv_p1_original_mv,
					A52_CPU_UV_P1_TARGET_MV, a52_cpu_uv_p1_vc,
					raw_before, raw_readback);
			} else {
				writel_relaxed(raw_before, base_volt + i * lut_row_size);
				raw_final = readl_relaxed(base_volt + i * lut_row_size);
				dev_warn(dev,
					"A52 CPU UV P1: write rejected/mismatched at %u kHz: wanted 0x%08x read 0x%08x; restored 0x%08x\n",
					cur_freq, raw_target, raw_readback, raw_final);
			}

			a52_cpu_uv_p1_raw_final = raw_final;
			data = raw_final;
			volt = (data & GENMASK(11, 0)) * 1000;
			vc = data & GENMASK(21, 16);
		}

		dev_dbg(dev, "index=%d freq=%d, core_count %d\n",
'''

    text = replace_once(text, loop_anchor, loop_block, "CPU UV P1 LUT write insertion")

    src.write_text(text)
    print(f"[patched] {src}: {MARKER}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <kernel-root>")
    patch(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
