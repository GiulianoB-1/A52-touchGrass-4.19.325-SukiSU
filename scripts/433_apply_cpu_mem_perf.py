#!/usr/bin/env python3
import argparse
import re
import shutil
from pathlib import Path

MARK = "A52_PHASE433_CPU_MEM_PERF_V1"
PORT_FILES = [
    "drivers/devfreq/arm-memlat-mon.c",
    "drivers/devfreq/bimc-bwmon.c",
    "drivers/devfreq/devfreq_devbw.c",
    "drivers/devfreq/devfreq_qcom_fw.c",
    "drivers/devfreq/devfreq_simple_dev.c",
    "drivers/devfreq/governor_bw_hwmon.c",
    "drivers/devfreq/governor_bw_hwmon.h",
    "drivers/devfreq/governor_memlat.c",
    "drivers/devfreq/governor_memlat.h",
    "include/soc/qcom/devfreq_devbw.h",
]

KCONFIG_BLOCK = r'''

# A52_PHASE433_CPU_MEM_PERF_V1
config QCOM_BIMC_BWMON
	tristate "QCOM BIMC Bandwidth monitor hardware"
	depends on ARCH_QCOM
	help
	  Qualcomm BIMC/LLCC bandwidth monitor used by the downstream bandwidth
	  governor on the A52/SM7225 vendor device tree.

config ARM_MEMLAT_MON
	tristate "ARM CPU Memory Latency monitor hardware"
	depends on ARCH_QCOM && PERF_EVENTS
	help
	  Qualcomm ARM PMU based memory-latency monitor used by the vendor DT.

config DEVFREQ_GOV_QCOM_BW_HWMON
	tristate "QCOM hardware bandwidth monitor governor"
	depends on QCOM_BIMC_BWMON
	help
	  Downstream Qualcomm hardware bandwidth monitor devfreq governor.

config DEVFREQ_GOV_MEMLAT
	tristate "QCOM memory latency governor"
	depends on ARM_MEMLAT_MON
	help
	  Downstream Qualcomm CPU memory-latency devfreq governor.

config ARM_QCOM_DEVFREQ_FW
	bool "Qualcomm firmware devfreq driver"
	depends on ARCH_QCOM
	select DEVFREQ_GOV_PERFORMANCE
	select DEVFREQ_GOV_POWERSAVE
	select DEVFREQ_GOV_USERSPACE
	default n
	help
	  Qualcomm firmware-backed devfreq driver, used for the L3 performance
	  state controller on the A52/SM7225 vendor device tree.

config DEVFREQ_SIMPLE_DEV
	tristate "Simple clock devfreq device"
	select DEVFREQ_GOV_PERFORMANCE
	select DEVFREQ_GOV_POWERSAVE
	select DEVFREQ_GOV_USERSPACE
	help
	  Downstream simple clock devfreq device support.

config QCOM_DEVFREQ_DEVBW
	bool "Qualcomm devfreq bandwidth voting device"
	depends on ARCH_QCOM
	select DEVFREQ_GOV_PERFORMANCE
	select DEVFREQ_GOV_POWERSAVE
	select DEVFREQ_GOV_USERSPACE
	default n
	help
	  Qualcomm device-to-device IB/AB bandwidth voting devfreq driver.
'''

MAKE_BLOCK = r'''

# A52_PHASE433_CPU_MEM_PERF_V1
obj-$(CONFIG_QCOM_BIMC_BWMON)              += bimc-bwmon.o
obj-$(CONFIG_ARM_MEMLAT_MON)                += arm-memlat-mon.o
obj-$(CONFIG_DEVFREQ_GOV_QCOM_BW_HWMON)    += governor_bw_hwmon.o
obj-$(CONFIG_DEVFREQ_GOV_MEMLAT)            += governor_memlat.o
obj-$(CONFIG_ARM_QCOM_DEVFREQ_FW)           += devfreq_qcom_fw.o
obj-$(CONFIG_QCOM_DEVFREQ_DEVBW)            += devfreq_devbw.o
obj-$(CONFIG_DEVFREQ_SIMPLE_DEV)             += devfreq_simple_dev.o
'''

TELEMETRY_C = r'''// SPDX-License-Identifier: GPL-2.0-only
/* A52_PHASE433_CPU_MEM_PERF_V1
 * Passive proof that the OSM CPU domains and Qualcomm memory/L3 devfreq stack
 * are alive. No register writes are performed here.
 */
#include <linux/a52_ack_secure_flight_recorder.h>
#include <linux/cpufreq.h>
#include <linux/device.h>
#include <linux/io.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/workqueue.h>

#define P433_DOMAINS 2
#define P433_LUT_ROWS 40
#define P433_LUT_OFF 0x110
#define P433_VOLT_OFF 0x114
#define P433_ROW_SIZE 0x20
#define P433_PERF_STATE 0x920
#define P433_MAP_SIZE 0x1000

static const phys_addr_t p433_phys[P433_DOMAINS] = {
	0x18323000ULL, 0x18325800ULL,
};
static const unsigned int p433_targets_s[] = { 20, 60, 120 };
static void __iomem *p433_base[P433_DOMAINS];
static struct delayed_work p433_work;
static unsigned int p433_next;

static u64 p433_ms(void)
{
	return div_u64(ktime_get_boottime_ns(), NSEC_PER_MSEC);
}

static void p433_cpu_policy(unsigned int sample, unsigned int cpu)
{
	struct cpufreq_policy *p = cpufreq_cpu_get(cpu);
	const char *gov = "-";
	unsigned int cur = 0;

	if (!p) {
		a52_ackfr_record("P433 CPU s=%u t=%llu cpu=%u no-policy",
				 sample, p433_ms(), cpu);
		return;
	}
	if (p->governor)
		gov = p->governor->name;
	cur = cpufreq_quick_get(cpu);
	a52_ackfr_record("P433 CPU s=%u t=%llu c=%u cur=%u min=%u max=%u g=%.12s",
			 sample, p433_ms(), cpu, cur, p->min, p->max, gov);
	cpufreq_cpu_put(p);
}

static void p433_dump_domain(unsigned int sample, unsigned int d)
{
	unsigned int i;
	u32 en, ps;

	if (!p433_base[d]) {
		a52_ackfr_record("P433 DOM s=%u t=%llu d=%u map=0",
				 sample, p433_ms(), d);
		return;
	}
	en = readl_relaxed(p433_base[d]);
	ps = readl_relaxed(p433_base[d] + P433_PERF_STATE);
	a52_ackfr_record("P433 DOM s=%u t=%llu d=%u en=%08x ps=%08x",
			 sample, p433_ms(), d, en, ps);
	for (i = 0; i < P433_LUT_ROWS; i++) {
		u32 lut = readl_relaxed(p433_base[d] + P433_LUT_OFF +
					i * P433_ROW_SIZE);
		u32 volt = readl_relaxed(p433_base[d] + P433_VOLT_OFF +
					 i * P433_ROW_SIZE);
		a52_ackfr_record("P433 LUT s=%u d=%u i=%u f=%08x v=%08x",
				 sample, d, i, lut, volt);
	}
}

static void p433_bound(unsigned int sample, const char *compat)
{
	struct device_node *np;
	unsigned int total = 0, bound = 0;

	for_each_compatible_node(np, NULL, compat) {
		struct platform_device *pdev;

		total++;
		pdev = of_find_device_by_node(np);
		if (!pdev)
			continue;
		if (pdev->dev.driver)
			bound++;
		put_device(&pdev->dev);
	}
	a52_ackfr_record("P433 B s=%u t=%llu n=%u b=%u c=%.28s",
			 sample, p433_ms(), total, bound, compat);
}

static void p433_sample(unsigned int sample)
{
	static const char * const compat[] = {
		"qcom,cpufreq-hw",
		"qcom,devfreq-fw",
		"qcom,devfreq-fw-voter",
		"qcom,devbw",
		"qcom,bimc-bwmon4",
		"qcom,bimc-bwmon5",
		"qcom,arm-memlat-cpugrp",
		"qcom,arm-memlat-mon",
	};
	unsigned int i;

	p433_cpu_policy(sample, 0);
	p433_cpu_policy(sample, 6);
	for (i = 0; i < P433_DOMAINS; i++)
		p433_dump_domain(sample, i);
	for (i = 0; i < ARRAY_SIZE(compat); i++)
		p433_bound(sample, compat[i]);
}

static void p433_schedule_next(void)
{
	u64 now, target;
	unsigned long delay;

	if (p433_next >= ARRAY_SIZE(p433_targets_s))
		return;
	now = p433_ms();
	target = (u64)p433_targets_s[p433_next] * MSEC_PER_SEC;
	delay = target > now ? msecs_to_jiffies(target - now) : 0;
	schedule_delayed_work(&p433_work, delay);
}

static void p433_workfn(struct work_struct *work)
{
	unsigned int sample = p433_targets_s[p433_next];

	p433_sample(sample);
	p433_next++;
	p433_schedule_next();
}

static int __init a52_p433_perf_init(void)
{
	unsigned int i;

	for (i = 0; i < P433_DOMAINS; i++)
		p433_base[i] = ioremap(p433_phys[i], P433_MAP_SIZE);
	INIT_DELAYED_WORK(&p433_work, p433_workfn);
	a52_ackfr_record("P433 PERF init t=%llu cpu+ddr+l3=1", p433_ms());
	p433_sample(0);
	p433_schedule_next();
	return 0;
}
late_initcall(a52_p433_perf_init);
'''


def die(msg):
    raise SystemExit(msg)


def remove_if_block_containing(s: str, needle: str) -> str:
    pos = s.find(needle)
    if pos < 0:
        return s
    start = s.rfind("if (", 0, pos)
    if start < 0:
        return s
    line_start = s.rfind("\n", 0, start) + 1
    brace = s.find("{", start, pos + len(needle) + 256)
    if brace < 0:
        return s
    depth = 0
    end = None
    for i in range(brace, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return s
    if end < len(s) and s[end] == "\n":
        end += 1
    return s[:line_start] + s[end:]


def apply(root: Path, tg: Path):
    for rel in PORT_FILES:
        src = tg / rel
        dst = root / rel
        if not src.is_file():
            die(f"Phase433 missing TouchGrass source: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    for rel in ["drivers/devfreq/governor_bw_hwmon.c",
                "drivers/devfreq/governor_memlat.c"]:
        p = root / rel
        s = p.read_text()
        s = s.replace("DEVFREQ_GOV_INTERVAL", "DEVFREQ_GOV_UPDATE_INTERVAL")
        s = s.replace("devfreq_interval_update", "devfreq_update_interval")

        # Android common 5.10 no longer carries the downstream Qualcomm
        # tracepoints. They are observational only, so remove the calls rather
        # than porting trace ABI that is unrelated to frequency scaling.
        s = re.sub(r"\n\s*trace_(?:bw_hwmon_(?:meas|update)|memlat_dev_(?:meas|update))"
                   r"\(.*?\);\n", "\n", s, flags=re.S)

        if rel.endswith("governor_bw_hwmon.c"):
            # 5.10 devfreq folded the old max_freq/dev_suspended fields into
            # scaling_max_freq and the suspend_count nesting counter.
            s = s.replace("node->hw->df->max_freq",
                          "node->hw->df->scaling_max_freq")
            s = s.replace("!df->dev_suspended",
                          "atomic_read(&df->suspend_count) == 0")
            s = s.replace("df->dev_suspended",
                          "atomic_read(&df->suspend_count) > 0")
        p.write_text(s)

    p = root / "drivers/devfreq/devfreq_qcom_fw.c"
    s = p.read_text()
    s = re.sub(r'^#include <linux/sec_(?:debug|smem)\.h>\n', '', s, flags=re.M)
    s = re.sub(r'^\s*sec_smem_clk_osm_add_log_l3\([^\n]*\);\n', '', s, flags=re.M)
    s = re.sub(r'\nstatic int devfreq_panic_callback\(.*?\n};\n\n', '\n', s,
               count=1, flags=re.S)
    s = re.sub(r'^static struct device \*dev_node;\n', '', s, flags=re.M)
    s = remove_if_block_containing(s, "qcom,support-panic-notifier")
    p.write_text(s)

    fdt = root / "drivers/of/fdt.c"
    fs = fdt.read_text()
    if MARK not in fs:
        fs += r'''

/* A52_PHASE433_CPU_MEM_PERF_V1 */
int of_fdt_get_ddrtype(void)
{
	int memory, len;
	const fdt32_t *prop;

	memory = fdt_path_offset(initial_boot_params, "/memory");
	if (memory < 0)
		return -ENOENT;
	prop = fdt_getprop(initial_boot_params, memory, "ddr_device_type", &len);
	if (!prop || len != sizeof(*prop))
		return -ENOENT;
	return fdt32_to_cpu(*prop);
}
'''
        fdt.write_text(fs)

    hdr = root / "include/linux/of_fdt.h"
    hs = hdr.read_text()
    if MARK not in hs:
        pos = hs.rfind("#endif")
        if pos < 0:
            die("include/linux/of_fdt.h: no endif")
        hs = hs[:pos] + ("\n/* A52_PHASE433_CPU_MEM_PERF_V1 */\n"
                        "int of_fdt_get_ddrtype(void);\n\n") + hs[pos:]
        hdr.write_text(hs)

    kcfg = root / "drivers/devfreq/Kconfig"
    ks = kcfg.read_text()
    if MARK not in ks:
        anchor = 'source "drivers/devfreq/event/Kconfig"'
        if anchor in ks:
            ks = ks.replace(anchor, KCONFIG_BLOCK + "\n" + anchor, 1)
        else:
            idx = ks.rfind("endif # PM_DEVFREQ")
            if idx < 0:
                die("drivers/devfreq/Kconfig: no insertion anchor")
            ks = ks[:idx] + KCONFIG_BLOCK + "\n" + ks[idx:]
        kcfg.write_text(ks)

    mk = root / "drivers/devfreq/Makefile"
    ms = mk.read_text()
    if MARK not in ms:
        mk.write_text(ms.rstrip() + MAKE_BLOCK + "\n")

    telemetry = root / "drivers/a52_secure/a52_phase433_perf.c"
    telemetry.write_text(TELEMETRY_C)
    amk = root / "drivers/a52_secure/Makefile"
    ams = amk.read_text()
    line = "obj-y += a52_phase433_perf.o"
    if line not in ams:
        amk.write_text(ams.rstrip() + "\n# A52_PHASE433_CPU_MEM_PERF_V1\n" + line + "\n")

    # The inherited focused recorder has explicit phase-prefix admission gates.
    # Without P433 here, all CPU/DDR/L3 telemetry calls are silently discarded.
    recorder = root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
    rs = recorder.read_text()
    p432_gate = 'strncmp(fmt, "P432", 4) &&'
    p433_gate = 'strncmp(fmt, "P433", 4) &&'
    if p433_gate not in rs:
        gate_count = rs.count(p432_gate)
        if gate_count < 3:
            die(f"Phase433 recorder admission anchors missing: found {gate_count}")
        rs = rs.replace(
            p432_gate,
            p433_gate + '\n    ' + p432_gate,
        )
        recorder.write_text(rs)


def check(root: Path):
    required = PORT_FILES + [
        "drivers/a52_secure/a52_phase433_perf.c",
        "drivers/devfreq/Kconfig",
        "drivers/devfreq/Makefile",
        "drivers/of/fdt.c",
        "include/linux/of_fdt.h",
        "drivers/a52_secure/a52_ack_secure_flight_recorder.c",
    ]
    for rel in required:
        if not (root / rel).is_file():
            die(f"Phase433 check missing: {rel}")
    for rel in ["drivers/devfreq/Kconfig", "drivers/devfreq/Makefile",
                "drivers/of/fdt.c", "include/linux/of_fdt.h",
                "drivers/a52_secure/a52_phase433_perf.c"]:
        if MARK not in (root / rel).read_text(errors="replace"):
            die(f"Phase433 marker missing: {rel}")
    for rel in ["drivers/devfreq/governor_bw_hwmon.c",
                "drivers/devfreq/governor_memlat.c"]:
        s = (root / rel).read_text(errors="replace")
        if "DEVFREQ_GOV_INTERVAL" in s or "devfreq_interval_update" in s:
            die(f"Phase433 old devfreq interval API remains: {rel}")
        if re.search(r"trace_(?:bw_hwmon_|memlat_dev_)", s):
            die(f"Phase433 downstream-only devfreq tracepoint remains: {rel}")
        if rel.endswith("governor_bw_hwmon.c"):
            if "->max_freq" in s or "dev_suspended" in s:
                die("Phase433 old struct devfreq fields remain")
    s = (root / "drivers/devfreq/devfreq_qcom_fw.c").read_text(errors="replace")
    if "sec_smem_clk_osm_add_log_l3" in s or "linux/sec_smem.h" in s:
        die("Phase433 Samsung-only L3 logging remains")
    recorder = (root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c").read_text(errors="replace")
    if recorder.count('strncmp(fmt, "P433", 4) &&') < 3:
        die("Phase433 recorder admission missing from one or more focused gates")
    print("Phase433 CPU + DDR/L3 performance port: PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path)
    ap.add_argument("--touchgrass", type=Path)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    if args.root is None:
        ap.error("--root is required")
    root = args.root.resolve()
    if not args.check_only:
        if args.touchgrass is None:
            ap.error("--touchgrass is required when applying")
        apply(root, args.touchgrass.resolve())
    check(root)


if __name__ == "__main__":
    main()
