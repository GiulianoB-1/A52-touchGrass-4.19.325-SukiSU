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

    anchor = '''static struct freq_attr *qcom_cpufreq_hw_attr[] = {
\t&cpufreq_freq_attr_scaling_available_freqs,
\t&cpufreq_freq_attr_scaling_boost_freqs,
\tNULL
};
'''

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
\tstruct cpufreq_qcom *c = policy->driver_data;
\tssize_t len = 0;
\tunsigned int i;

\tif (!c || !c->table || !c->reg_bases[REG_VOLT_LUT_TABLE])
\t\treturn scnprintf(buf, PAGE_SIZE, "unavailable\n");

\tlen += scnprintf(buf + len, PAGE_SIZE - len,
\t\t"policy_cpu=%u entries=%u\n", policy->cpu, c->lut_max_entries);

\tfor (i = 0; i < c->lut_max_entries; i++) {
\t\tu32 raw, volt_mv, vc;
\t\tunsigned int freq = c->table[i].frequency;

\t\tif (freq == CPUFREQ_ENTRY_INVALID || freq == CPUFREQ_TABLE_END)
\t\t\tcontinue;

\t\traw = readl_relaxed(c->reg_bases[REG_VOLT_LUT_TABLE] +
\t\t\t\ti * lut_row_size);
\t\tvolt_mv = raw & GENMASK(11, 0);
\t\tvc = (raw & GENMASK(21, 16)) >> 16;

\t\tlen += scnprintf(buf + len, PAGE_SIZE - len,
\t\t\t"idx=%u freq_khz=%u volt_mv=%u vc=%u raw=0x%08x\n",
\t\t\ti, freq, volt_mv, vc, raw);

\t\tif (len >= PAGE_SIZE - 96)
\t\t\tbreak;
\t}

\treturn len;
}

cpufreq_freq_attr_ro(a52_cpu_uv_probe);

static struct freq_attr *qcom_cpufreq_hw_attr[] = {
\t&cpufreq_freq_attr_scaling_available_freqs,
\t&cpufreq_freq_attr_scaling_boost_freqs,
\t&a52_cpu_uv_probe,
\tNULL
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
