#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def mark_after(text: str, statement: str, marker: str, label: str) -> str:
    return replace_once(
        text,
        statement,
        statement + f'\ta52_persistent_diag_mark("A52DIAG START {marker}\\n");\n',
        label,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize an A52 persistent diagnostic ring immediately after mm_init, "
            "provide a raw persistent-RAM fallback, and trace start_kernel/initcalls"
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    ram_path = gki / "fs/pstore/ram.c"
    main_path = gki / "init/main.c"
    if not ram_path.is_file() or not main_path.is_file():
        raise SystemExit("expected GKI fs/pstore/ram.c and init/main.c")

    ram = ram_path.read_text(encoding="utf-8")
    main_c = main_path.read_text(encoding="utf-8")

    init_anchor = "static int __init ramoops_init(void)\n"
    diagnostic_code = r'''#define A52_DIAG_CONSOLE_PHYS 0xB1B40000ULL
#define A52_DIAG_CONSOLE_SIZE 0x00040000UL
#define A52_DIAG_HEADER_SIZE 12U
#define A52_DIAG_DATA_SIZE (A52_DIAG_CONSOLE_SIZE - A52_DIAG_HEADER_SIZE)
#define A52_DIAG_PERSISTENT_RAM_SIG 0x43474244U
#define A52_DIAG_LINE_SIZE 256

static struct persistent_ram_zone *a52_diag_prz;
static u8 __iomem *a52_diag_raw;
static u32 a52_diag_raw_start;
static u32 a52_diag_raw_size;

static void a52_diag_raw_write(const char *s, unsigned int count)
{
	unsigned int first;

	if (!a52_diag_raw || !count)
		return;

	if (count > A52_DIAG_DATA_SIZE) {
		s += count - A52_DIAG_DATA_SIZE;
		count = A52_DIAG_DATA_SIZE;
	}

	first = min_t(unsigned int, count,
		      A52_DIAG_DATA_SIZE - a52_diag_raw_start);
	memcpy_toio(a52_diag_raw + A52_DIAG_HEADER_SIZE +
		    a52_diag_raw_start, s, first);
	if (count > first)
		memcpy_toio(a52_diag_raw + A52_DIAG_HEADER_SIZE,
			    s + first, count - first);

	a52_diag_raw_start += count;
	while (a52_diag_raw_start >= A52_DIAG_DATA_SIZE)
		a52_diag_raw_start -= A52_DIAG_DATA_SIZE;
	a52_diag_raw_size = min_t(u32, A52_DIAG_DATA_SIZE,
				 a52_diag_raw_size + count);

	wmb();
	writel_relaxed(a52_diag_raw_start, a52_diag_raw + 4);
	writel_relaxed(a52_diag_raw_size, a52_diag_raw + 8);
	wmb();
}

void a52_persistent_diag_mark(const char *fmt, ...)
{
	char line[A52_DIAG_LINE_SIZE];
	va_list args;
	int len;

	if (IS_ERR_OR_NULL(a52_diag_prz) && !a52_diag_raw)
		return;

	va_start(args, fmt);
	len = vscnprintf(line, sizeof(line), fmt, args);
	va_end(args);

	if (len <= 0)
		return;

	if (a52_diag_prz)
		persistent_ram_write(a52_diag_prz, line, len);
	else
		a52_diag_raw_write(line, len);
	wmb();
}

int __init a52_persistent_diag_init(void)
{
	struct persistent_ram_ecc_info ecc = { };
	int prz_ret;

	if (a52_diag_prz || a52_diag_raw)
		return 0;

	a52_diag_prz = persistent_ram_new(A52_DIAG_CONSOLE_PHYS,
					   A52_DIAG_CONSOLE_SIZE, 0, &ecc,
					   1, PRZ_FLAG_ZAP_OLD,
					   "a52-early-diag");
	if (!IS_ERR(a52_diag_prz)) {
		a52_diag_prz->type = PSTORE_TYPE_CONSOLE;
		a52_persistent_diag_mark(
			"A52DIAG START READY backend=persistent_ram phys=0x%llx size=0x%lx\n",
			(unsigned long long)A52_DIAG_CONSOLE_PHYS,
			A52_DIAG_CONSOLE_SIZE);
		return 0;
	}

	prz_ret = PTR_ERR(a52_diag_prz);
	a52_diag_prz = NULL;

	/*
	 * Fallback that bypasses persistent_ram_new's resource/mapping path.
	 * It writes the exact no-ECC persistent_ram header OrangeFox expects:
	 * DBGC signature, next-write offset, valid byte count, then ring data.
	 */
	a52_diag_raw = ioremap_wc(A52_DIAG_CONSOLE_PHYS,
				 A52_DIAG_CONSOLE_SIZE);
	if (!a52_diag_raw)
		a52_diag_raw = ioremap(A52_DIAG_CONSOLE_PHYS,
				      A52_DIAG_CONSOLE_SIZE);
	if (!a52_diag_raw)
		return prz_ret ? prz_ret : -ENOMEM;

	memset_io(a52_diag_raw, 0, A52_DIAG_CONSOLE_SIZE);
	writel_relaxed(A52_DIAG_PERSISTENT_RAM_SIG, a52_diag_raw);
	writel_relaxed(0, a52_diag_raw + 4);
	writel_relaxed(0, a52_diag_raw + 8);
	a52_diag_raw_start = 0;
	a52_diag_raw_size = 0;
	wmb();

	a52_persistent_diag_mark(
		"A52DIAG START READY backend=raw-fallback persistent_ram_ret=%d phys=0x%llx size=0x%lx\n",
		prz_ret, (unsigned long long)A52_DIAG_CONSOLE_PHYS,
		A52_DIAG_CONSOLE_SIZE);
	return 0;
}

'''
    ram = replace_once(
        ram,
        init_anchor,
        diagnostic_code + "static int __init __maybe_unused ramoops_init(void)\n",
        "add early persistent diagnostic ring",
    )
    ram = replace_once(
        ram,
        "postcore_initcall(ramoops_init);\n",
        "/* A52 diagnostic build owns the console quarter directly. */\n",
        "disable conflicting normal ramoops registration",
    )

    start_anchor = (
        "asmlinkage __visible void __init __no_sanitize_address start_kernel(void)\n"
    )
    declarations = (
        "#if IS_BUILTIN(CONFIG_PSTORE_RAM)\n"
        "extern int __init a52_persistent_diag_init(void);\n"
        "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
        "#else\n"
        "static inline int __init a52_persistent_diag_init(void) { return -ENODEV; }\n"
        "static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n"
        "#endif\n\n"
        + start_anchor
    )
    main_c = replace_once(
        main_c,
        start_anchor,
        declarations,
        "declare early diagnostic helpers before start_kernel",
    )

    mm_anchor = "\tmm_init();\n\tpoking_init();\n"
    mm_replacement = (
        "\tmm_init();\n"
        "\tif (a52_persistent_diag_init())\n"
        "\t\tpr_err(\"A52 early persistent diagnostic initialization failed\\n\");\n"
        "\telse\n"
        "\t\ta52_persistent_diag_mark(\"A52DIAG START after mm_init\\n\");\n"
        "\ta52_persistent_diag_mark(\"A52DIAG START before poking_init\\n\");\n"
        "\tpoking_init();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG START after poking_init\\n\");\n"
    )
    main_c = replace_once(
        main_c,
        mm_anchor,
        mm_replacement,
        "initialize diagnostics immediately after mm_init",
    )

    start_markers = [
        ("\tftrace_init();\n", "after ftrace_init", "mark ftrace init"),
        ("\tearly_trace_init();\n", "after early_trace_init", "mark early trace init"),
        ("\tsched_init();\n", "after sched_init", "mark scheduler init"),
        ("\tradix_tree_init();\n", "after radix_tree_init", "mark radix tree init"),
        ("\thousekeeping_init();\n", "after housekeeping_init", "mark housekeeping init"),
        ("\tworkqueue_init_early();\n", "after workqueue_init_early", "mark early workqueue init"),
        ("\trcu_init();\n", "after rcu_init", "mark rcu init"),
        ("\ttrace_init();\n", "after trace_init", "mark trace init"),
        ("\tcontext_tracking_init();\n", "after context_tracking_init", "mark context tracking"),
        ("\tearly_irq_init();\n", "after early_irq_init", "mark early irq init"),
        ("\tinit_IRQ();\n", "after init_IRQ", "mark arch irq init"),
        ("\ttick_init();\n", "after tick_init", "mark tick init"),
        ("\trcu_init_nohz();\n", "after rcu_init_nohz", "mark rcu nohz"),
        ("\tinit_timers();\n", "after init_timers", "mark timers"),
        ("\thrtimers_init();\n", "after hrtimers_init", "mark hrtimers"),
        ("\tsoftirq_init();\n", "after softirq_init", "mark softirq"),
        ("\ttimekeeping_init();\n", "after timekeeping_init", "mark timekeeping"),
        ("\tkfence_init();\n", "after kfence_init", "mark kfence"),
        ("\ttime_init();\n", "after time_init", "mark time init"),
        ("\trandom_init(command_line);\n", "after random_init", "mark random init"),
        ("\tperf_event_init();\n", "after perf_event_init", "mark perf init"),
        ("\tcall_function_init();\n", "after call_function_init", "mark call function init"),
        ("\tkmem_cache_init_late();\n", "after kmem_cache_init_late", "mark late slab init"),
        ("\tconsole_init();\n", "after console_init", "mark console init"),
        ("\tlockdep_init();\n", "after lockdep_init", "mark lockdep init"),
        ("\tlocking_selftest();\n", "after locking_selftest", "mark locking selftest"),
        ("\tsetup_per_cpu_pageset();\n", "after setup_per_cpu_pageset", "mark per cpu pageset"),
        ("\tsched_clock_init();\n", "after sched_clock_init", "mark sched clock"),
        ("\tcalibrate_delay();\n", "after calibrate_delay", "mark delay calibration"),
        ("\tarch_cpu_finalize_init();\n", "after arch_cpu_finalize_init", "mark arch cpu finalize"),
        ("\tsecurity_init();\n", "after security_init", "mark security init"),
        ("\tvfs_caches_init();\n", "after vfs_caches_init", "mark vfs caches"),
        ("\tproc_root_init();\n", "after proc_root_init", "mark proc root"),
        ("\tcgroup_init();\n", "after cgroup_init", "mark cgroup init"),
    ]
    for statement, marker, label in start_markers:
        main_c = mark_after(main_c, statement, marker, label)

    main_c = replace_once(
        main_c,
        "\tearly_boot_irqs_disabled = false;\n\tlocal_irq_enable();\n",
        "\tearly_boot_irqs_disabled = false;\n"
        "\ta52_persistent_diag_mark(\"A52DIAG START before local_irq_enable\\n\");\n"
        "\tlocal_irq_enable();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG START after local_irq_enable\\n\");\n",
        "instrument boot IRQ enable",
    )

    main_c = replace_once(
        main_c,
        "\t/* Do the rest non-__init'ed, we're now alive */\n\tarch_call_rest_init();\n",
        "\t/* Do the rest non-__init'ed, we're now alive */\n"
        "\ta52_persistent_diag_mark(\"A52DIAG START before arch_call_rest_init\\n\");\n"
        "\tarch_call_rest_init();\n",
        "mark transition to rest_init",
    )

    rest_anchor = (
        "\trcu_scheduler_starting();\n"
        "\t/*\n"
        "\t * We need to spawn init first so that it obtains pid 1, however\n"
    )
    rest_replacement = (
        "\ta52_persistent_diag_mark(\"A52DIAG REST before rcu_scheduler_starting\\n\");\n"
        "\trcu_scheduler_starting();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG REST after rcu_scheduler_starting\\n\");\n"
        "\t/*\n"
        "\t * We need to spawn init first so that it obtains pid 1, however\n"
    )
    main_c = replace_once(
        main_c,
        rest_anchor,
        rest_replacement,
        "instrument rest_init entry",
    )
    main_c = replace_once(
        main_c,
        "\tpid = kernel_thread(kernel_init, NULL, CLONE_FS);\n",
        "\ta52_persistent_diag_mark(\"A52DIAG REST before kernel_init thread\\n\");\n"
        "\tpid = kernel_thread(kernel_init, NULL, CLONE_FS);\n"
        "\ta52_persistent_diag_mark(\"A52DIAG REST after kernel_init thread pid=%d\\n\", pid);\n",
        "instrument kernel_init thread creation",
    )
    main_c = replace_once(
        main_c,
        "\tpid = kernel_thread(kthreadd, NULL, CLONE_FS | CLONE_FILES);\n",
        "\ta52_persistent_diag_mark(\"A52DIAG REST before kthreadd thread\\n\");\n"
        "\tpid = kernel_thread(kthreadd, NULL, CLONE_FS | CLONE_FILES);\n"
        "\ta52_persistent_diag_mark(\"A52DIAG REST after kthreadd thread pid=%d\\n\", pid);\n",
        "instrument kthreadd creation",
    )
    main_c = replace_once(
        main_c,
        "\tcomplete(&kthreadd_done);\n",
        "\tcomplete(&kthreadd_done);\n"
        "\ta52_persistent_diag_mark(\"A52DIAG REST after kthreadd completion\\n\");\n",
        "mark kthreadd completion",
    )

    call_anchor = (
        "\tdo_trace_initcall_start(fn);\n"
        "\tret = fn();\n"
        "\tdo_trace_initcall_finish(fn, ret);\n"
    )
    call_replacement = (
        "\ta52_persistent_diag_mark(\"A52DIAG INIT BEGIN %pS\\n\", fn);\n"
        "\tdo_trace_initcall_start(fn);\n"
        "\tret = fn();\n"
        "\tdo_trace_initcall_finish(fn, ret);\n"
        "\ta52_persistent_diag_mark(\"A52DIAG INIT END %pS ret=%d\\n\", fn, ret);\n"
    )
    main_c = replace_once(
        main_c,
        call_anchor,
        call_replacement,
        "instrument all early and normal initcalls",
    )

    level_anchor = (
        "\ttrace_initcall_level(initcall_level_names[level]);\n"
        "\tfor (fn = initcall_levels[level]; fn < initcall_levels[level+1]; fn++)\n"
    )
    level_replacement = (
        "\ta52_persistent_diag_mark(\"A52DIAG LEVEL %s BEGIN\\n\",\n"
        "\t\t\t\t initcall_level_names[level]);\n"
        "\ttrace_initcall_level(initcall_level_names[level]);\n"
        "\tfor (fn = initcall_levels[level]; fn < initcall_levels[level+1]; fn++)\n"
    )
    main_c = replace_once(
        main_c,
        level_anchor,
        level_replacement,
        "mark normal initcall levels",
    )

    driver_anchor = "\tdriver_init();\n\tinit_irq_proc();\n"
    driver_replacement = (
        "\ta52_persistent_diag_mark(\"A52DIAG BASIC before driver_init\\n\");\n"
        "\tdriver_init();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG BASIC after driver_init\\n\");\n"
        "\ta52_persistent_diag_mark(\"A52DIAG BASIC before init_irq_proc\\n\");\n"
        "\tinit_irq_proc();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG BASIC after init_irq_proc\\n\");\n"
    )
    main_c = replace_once(
        main_c,
        driver_anchor,
        driver_replacement,
        "instrument driver model setup",
    )

    setup_anchor = (
        "\tdo_ctors();\n"
        "\tusermodehelper_enable();\n"
        "\tdo_initcalls();\n"
    )
    setup_replacement = (
        "\ta52_persistent_diag_mark(\"A52DIAG BASIC before do_ctors\\n\");\n"
        "\tdo_ctors();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG BASIC after do_ctors\\n\");\n"
        "\tusermodehelper_enable();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG BASIC after usermodehelper_enable\\n\");\n"
        "\tdo_initcalls();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG BASIC after do_initcalls\\n\");\n"
    )
    main_c = replace_once(
        main_c,
        setup_anchor,
        setup_replacement,
        "instrument basic setup stages",
    )

    ram_path.write_text(ram, encoding="utf-8")
    main_path.write_text(main_c, encoding="utf-8")

    checks = {
        "valid_persistent_ram_signature_fallback": (
            "A52_DIAG_PERSISTENT_RAM_SIG 0x43474244U" in ram
            and "writel_relaxed(A52_DIAG_PERSISTENT_RAM_SIG" in ram
        ),
        "persistent_ram_primary_backend": "persistent_ram_new(A52_DIAG_CONSOLE_PHYS" in ram,
        "raw_ioremap_fallback": (
            "ioremap_wc(A52_DIAG_CONSOLE_PHYS" in ram
            and "backend=raw-fallback" in ram
        ),
        "normal_ramoops_disabled": "postcore_initcall(ramoops_init);" not in ram,
        "initialized_after_mm_init": (
            "mm_init();\n\tif (a52_persistent_diag_init())" in main_c
        ),
        "start_kernel_milestones": (
            "A52DIAG START after sched_init" in main_c
            and "A52DIAG START after init_IRQ" in main_c
            and "A52DIAG START before arch_call_rest_init" in main_c
        ),
        "rest_init_milestones": (
            "A52DIAG REST before kernel_init thread" in main_c
            and "A52DIAG REST after kthreadd completion" in main_c
        ),
        "initcall_begin_end": (
            "A52DIAG INIT BEGIN %pS" in main_c
            and "A52DIAG INIT END %pS ret=%d" in main_c
        ),
        "driver_init_no_longer_initializes_diag": (
            "driver_init();\n\tif (a52_persistent_diag_init())" not in main_c
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("staging audit failed: " + ", ".join(failed))

    (out / "patched-ram.c").write_text(ram, encoding="utf-8")
    (out / "patched-init-main.c").write_text(main_c, encoding="utf-8")
    (out / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "purpose": (
                    "capture start_kernel, rest_init, early-initcall and normal-initcall "
                    "progress in an OrangeFox-compatible persistent RAM ring"
                ),
                "initialization_point": "immediately after mm_init",
                "console_zone": {
                    "physical_address": "0xB1B40000",
                    "size": "0x00040000",
                    "primary_backend": "persistent_ram_new",
                    "fallback_backend": "raw DBGC persistent_ram ring via ioremap",
                },
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
