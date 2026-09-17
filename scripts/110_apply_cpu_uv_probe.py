#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 CPU UV PROBE: read-only qcom-cpufreq-hw voltage LUT"


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

    anchor = '''static struct freq_attr *qcom_cpufreq_hw_attr[] = {\n\t&cpufreq_freq_attr_scaling_available_freqs,\n\t&cpufreq_freq_attr_scaling_boost_freqs,\n\tNULL\n};\n'''

    block = r'''/*
 * A52 CPU UV PROBE: read-only qcom-cpufreq-hw voltage LUT
 *
 * Exposes the hardware frequency/voltage LUT already programmed by Qualcomm
 * hardware. This is diagnostic only: it performs no writes to the LUT, OPP
 * table, performance state, governor, thermal/DCVSH controls, or voltage rail.
 *
 * Format per line:
 *   idx=<n> freq_khz=<kHz> volt_mv=<mV> vc=<code> raw=0x<register>
 */
static ssize_t show_a52_cpu_uv_probe(struct cpufreq_policy *policy, char *buf)
{
	struct cpufreq_qcom *c = policy->driver_data;
	ssize_t len = 0;
	unsigned int i;

	if (!c || !c->table || !c->reg_bases[REG_VOLT_LUT_TABLE])
		return scnprintf(buf, PAGE_SIZE, "unavailable\n");

	len += scnprintf(buf + len, PAGE_SIZE - len,
		"policy_cpu=%u entries=%u\n", policy->cpu, c->lut_max_entries);

	for (i = 0; i < c->lut_max_entries; i++) {
		u32 raw, volt_mv, vc;
		unsigned int freq = c->table[i].frequency;

		if (freq == CPUFREQ_ENTRY_INVALID || freq == CPUFREQ_TABLE_END)
			continue;

		raw = readl_relaxed(c->reg_bases[REG_VOLT_LUT_TABLE] +
				i * lut_row_size);
		volt_mv = raw & GENMASK(11, 0);
		vc = (raw & GENMASK(21, 16)) >> 16;

		len += scnprintf(buf + len, PAGE_SIZE - len,
			"idx=%u freq_khz=%u volt_mv=%u vc=%u raw=0x%08x\n",
			i, freq, volt_mv, vc, raw);

		if (len >= PAGE_SIZE - 96)
			break;
	}

	return len;
}

cpufreq_freq_attr_ro(a52_cpu_uv_probe);

static struct freq_attr *qcom_cpufreq_hw_attr[] = {
	&cpufreq_freq_attr_scaling_available_freqs,
	&cpufreq_freq_attr_scaling_boost_freqs,
	&a52_cpu_uv_probe,
	NULL
};
'''

    text = replace_once(text, anchor, block, "cpufreq probe insertion")
    src.write_text(text)
    print(f"[patched] {src}: {MARKER}")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {Path(sys.argv[0]).name} <kernel-root>")
    patch(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
