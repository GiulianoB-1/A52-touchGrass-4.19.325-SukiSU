#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 88_apply_spe_caps_probe.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
src = root / "drivers/misc/a52_spe_caps.c"
mk = root / "drivers/misc/Makefile"

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * A52 Phase88 raw ARM SPE capability probe.
 *
 * Diagnostic only:
 * - does not enable CONFIG_ARM_SPE_PMU
 * - does not add or alter device-tree nodes
 * - does not touch SPE IRQ routing
 * - reads each online CPU's architectural feature registers on that CPU
 *
 * The access order intentionally mirrors drivers/perf/arm_spe_pmu.c:
 *   1. ID_AA64DFR0_EL1.PMSVer
 *   2. PMBIDR_EL1 only when PMSVer != 0
 *   3. PMSIDR_EL1 only when PMBIDR.P says the buffer is not owned by
 *      a higher exception level
 */

#include <linux/bitops.h>
#include <linux/cpu.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/smp.h>
#include <linux/string.h>

#include <asm/sysreg.h>

#define A52_PHASE88_SPE_CAPS_V1 1

struct a52_spe_caps_sample {
	u64 dfr0;
	u64 pmbidr;
	u64 pmsidr;
	u32 pmsver;
	bool have_pmbidr;
	bool have_pmsidr;
};

static void a52_spe_caps_read_local(void *arg)
{
	struct a52_spe_caps_sample *s = arg;

	s->dfr0 = read_sysreg_s(SYS_ID_AA64DFR0_EL1);
	s->pmsver = (u32)((s->dfr0 >> ID_AA64DFR0_PMSVER_SHIFT) & 0xf);

	if (!s->pmsver)
		return;

	/*
	 * This is the same first SPE-specific register read performed by
	 * arm_spe_pmu.c once PMSVer says SPE exists.
	 */
	s->pmbidr = read_sysreg_s(SYS_PMBIDR_EL1);
	s->have_pmbidr = true;

	/*
	 * PMBIDR.P == 1 means the profiling buffer is owned by a higher
	 * exception level. Match the upstream driver and stop here.
	 */
	if (s->pmbidr & BIT(SYS_PMBIDR_EL1_P_SHIFT))
		return;

	s->pmsidr = read_sysreg_s(SYS_PMSIDR_EL1);
	s->have_pmsidr = true;
}

static int a52_spe_caps_show(struct seq_file *m, void *v)
{
	int cpu;

	seq_puts(m, "probe=A52_PHASE88_SPE_CAPS_V1\n");
	seq_puts(m, "pmsver_field=ID_AA64DFR0_EL1[35:32]\n");
	seq_puts(m, "pmbidr_p_semantics=1:higher_exception_level_owns_buffer\n");

	for_each_possible_cpu(cpu) {
		struct a52_spe_caps_sample s;
		int ret;

		memset(&s, 0, sizeof(s));

		if (!cpu_online(cpu)) {
			seq_printf(m, "cpu%d online=0\n", cpu);
			continue;
		}

		ret = smp_call_function_single(cpu, a52_spe_caps_read_local,
					       &s, 1);
		if (ret) {
			seq_printf(m, "cpu%d online=1 read_error=%d\n",
				   cpu, ret);
			continue;
		}

		seq_printf(m,
			   "cpu%d online=1 dfr0=0x%016llx pmsver=%u spe=%s",
			   cpu, (unsigned long long)s.dfr0, s.pmsver,
			   s.pmsver ? "present" : "absent");

		if (!s.pmsver) {
			seq_putc(m, '\n');
			continue;
		}

		if (s.have_pmbidr) {
			u32 higher_el_owned =
				!!(s.pmbidr & BIT(SYS_PMBIDR_EL1_P_SHIFT));
			u32 align_log2 =
				(u32)((s.pmbidr >> SYS_PMBIDR_EL1_ALIGN_SHIFT) &
				      SYS_PMBIDR_EL1_ALIGN_MASK);

			seq_printf(m,
				   " pmbidr=0x%016llx higher_el_owned=%u align_log2=%u",
				   (unsigned long long)s.pmbidr,
				   higher_el_owned, align_log2);
		}

		if (s.have_pmsidr)
			seq_printf(m, " pmsidr=0x%016llx",
				   (unsigned long long)s.pmsidr);

		seq_putc(m, '\n');
	}

	return 0;
}

static int __init a52_spe_caps_init(void)
{
	if (!proc_create_single("a52_spe_caps", 0444, NULL,
				a52_spe_caps_show))
		return -ENOMEM;

	pr_info("A52SPE: Phase88 raw SPE capability probe ready at /proc/a52_spe_caps\n");
	return 0;
}
late_initcall(a52_spe_caps_init);
'''

src.write_text(SOURCE, encoding="utf-8")

text = mk.read_text(encoding="utf-8")
marker = "# A52 Phase88 raw SPE capability probe"
entry = marker + "\nobj-y += a52_spe_caps.o\n"

if marker not in text:
    mk.write_text(text.rstrip() + "\n\n" + entry, encoding="utf-8")

checks = {
    "marker": "A52_PHASE88_SPE_CAPS_V1" in src.read_text(encoding="utf-8"),
    "raw_dfr0": "read_sysreg_s(SYS_ID_AA64DFR0_EL1)" in src.read_text(encoding="utf-8"),
    "pmsver": "ID_AA64DFR0_PMSVER_SHIFT" in src.read_text(encoding="utf-8"),
    "pmbidr": "read_sysreg_s(SYS_PMBIDR_EL1)" in src.read_text(encoding="utf-8"),
    "higher_el_guard": "SYS_PMBIDR_EL1_P_SHIFT" in src.read_text(encoding="utf-8"),
    "pmsidr": "read_sysreg_s(SYS_PMSIDR_EL1)" in src.read_text(encoding="utf-8"),
    "per_cpu": "smp_call_function_single" in src.read_text(encoding="utf-8"),
    "proc": 'proc_create_single("a52_spe_caps"' in src.read_text(encoding="utf-8"),
    "makefile": "a52_spe_caps.o" in mk.read_text(encoding="utf-8"),
}

failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("phase88 SPE capability probe staging audit failed: " +
                     ", ".join(failed))

print("phase88_spe_capability_probe=staged")
for name in sorted(checks):
    print(f"{name}=PASS")
