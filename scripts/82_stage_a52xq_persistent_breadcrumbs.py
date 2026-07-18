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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add an uncached persistent breadcrumb console and instrument every "
            "normal initcall for A52 GKI bring-up"
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

    ram = replace_once(
        ram,
        "#include <linux/compiler.h>\n#include <linux/pstore_ram.h>\n",
        "#include <linux/compiler.h>\n#include <linux/console.h>\n#include <linux/pstore_ram.h>\n",
        "add console header",
    )

    init_anchor = "static int __init ramoops_init(void)\n"
    diagnostic_code = r'''#define A52_DIAG_CONSOLE_PHYS 0xB1B40000ULL
#define A52_DIAG_CONSOLE_SIZE 0x00040000UL
#define A52_DIAG_LINE_SIZE 256

static struct persistent_ram_zone *a52_diag_prz;

void a52_persistent_diag_mark(const char *fmt, ...)
{
	char line[A52_DIAG_LINE_SIZE];
	va_list args;
	int len;

	if (IS_ERR_OR_NULL(a52_diag_prz))
		return;

	va_start(args, fmt);
	len = vscnprintf(line, sizeof(line), fmt, args);
	va_end(args);

	if (len > 0) {
		persistent_ram_write(a52_diag_prz, line, len);
		wmb();
	}
}

static void a52_diag_console_write(struct console *con, const char *s,
				   unsigned int count)
{
	if (IS_ERR_OR_NULL(a52_diag_prz) || !count)
		return;

	persistent_ram_write(a52_diag_prz, s, count);
	wmb();
}

static struct console a52_diag_console = {
	.name = "a52diag",
	.write = a52_diag_console_write,
	.flags = CON_PRINTBUFFER | CON_ENABLED | CON_ANYTIME,
	.index = -1,
};

int __init a52_persistent_diag_init(void)
{
	struct persistent_ram_ecc_info ecc = { };

	/*
	 * Use the console quarter of OrangeFox's proven 1 MiB ramoops layout.
	 * An uncached mapping is intentional so hard-reset breadcrumbs reach DRAM.
	 * Recovery will decode this as console-ramoops-0 on the next boot.
	 */
	a52_diag_prz = persistent_ram_new(A52_DIAG_CONSOLE_PHYS,
					   A52_DIAG_CONSOLE_SIZE, 0, &ecc,
					   1, PRZ_FLAG_ZAP_OLD,
					   "a52-diag-console");
	if (IS_ERR(a52_diag_prz)) {
		int ret = PTR_ERR(a52_diag_prz);

		a52_diag_prz = NULL;
		return ret;
	}

	a52_diag_prz->type = PSTORE_TYPE_CONSOLE;
	a52_persistent_diag_mark(
		"A52DIAG READY phys=0x%llx size=0x%lx memtype=uncached\n",
		(unsigned long long)A52_DIAG_CONSOLE_PHYS,
		A52_DIAG_CONSOLE_SIZE);

	/* CON_PRINTBUFFER attempts to copy the printk backlog. The explicit
	 * breadcrumb writes remain useful even if console selection rejects it. */
	register_console(&a52_diag_console);
	a52_persistent_diag_mark("A52DIAG CONSOLE flags=0x%x\n",
				 a52_diag_console.flags);
	return 0;
}

'''
    ram = replace_once(
        ram,
        init_anchor,
        diagnostic_code + "static int __init __maybe_unused ramoops_init(void)\n",
        "add direct persistent diagnostic console",
    )
    ram = replace_once(
        ram,
        "postcore_initcall(ramoops_init);\n",
        "/* A52 diagnostic build uses a dedicated persistent console zone.\n"
        " * Do not start the normal ramoops backend against the same memory. */\n",
        "disable conflicting normal ramoops registration",
    )

    do_one_anchor = "int __init_or_module do_one_initcall(initcall_t fn)\n"
    declarations = (
        "#if IS_BUILTIN(CONFIG_PSTORE_RAM)\n"
        "extern int __init a52_persistent_diag_init(void);\n"
        "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
        "#else\n"
        "static inline int __init a52_persistent_diag_init(void) { return -ENODEV; }\n"
        "static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n"
        "#endif\n\n"
        + do_one_anchor
    )
    main_c = replace_once(
        main_c,
        do_one_anchor,
        declarations,
        "declare breadcrumb helpers",
    )

    call_anchor = (
        "\tdo_trace_initcall_start(fn);\n"
        "\tret = fn();\n"
        "\tdo_trace_initcall_finish(fn, ret);\n"
    )
    call_replacement = (
        "\ta52_persistent_diag_mark(\"A52DIAG BEGIN %pS\\n\", fn);\n"
        "\tdo_trace_initcall_start(fn);\n"
        "\tret = fn();\n"
        "\tdo_trace_initcall_finish(fn, ret);\n"
        "\ta52_persistent_diag_mark(\"A52DIAG END %pS ret=%d\\n\", fn, ret);\n"
    )
    main_c = replace_once(
        main_c,
        call_anchor,
        call_replacement,
        "instrument initcall begin and end",
    )

    driver_anchor = "\tdriver_init();\n\tinit_irq_proc();\n"
    driver_replacement = (
        "\tdriver_init();\n"
        "\tif (a52_persistent_diag_init())\n"
        "\t\tpr_err(\"A52 persistent breadcrumb initialization failed\\n\");\n"
        "\telse\n"
        "\t\ta52_persistent_diag_mark(\"A52DIAG AFTER driver_init\\n\");\n"
        "\ta52_persistent_diag_mark(\"A52DIAG BEFORE init_irq_proc\\n\");\n"
        "\tinit_irq_proc();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG AFTER init_irq_proc\\n\");\n"
    )
    main_c = replace_once(
        main_c,
        driver_anchor,
        driver_replacement,
        "initialize breadcrumbs immediately after driver_init",
    )

    setup_anchor = (
        "\tdo_ctors();\n"
        "\tusermodehelper_enable();\n"
        "\tdo_initcalls();\n"
    )
    setup_replacement = (
        "\ta52_persistent_diag_mark(\"A52DIAG BEFORE do_ctors\\n\");\n"
        "\tdo_ctors();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG AFTER do_ctors\\n\");\n"
        "\tusermodehelper_enable();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG AFTER usermodehelper_enable\\n\");\n"
        "\tdo_initcalls();\n"
        "\ta52_persistent_diag_mark(\"A52DIAG AFTER do_initcalls\\n\");\n"
    )
    main_c = replace_once(
        main_c,
        setup_anchor,
        setup_replacement,
        "instrument basic setup stages",
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
        "mark initcall level start",
    )

    ram_path.write_text(ram, encoding="utf-8")
    main_path.write_text(main_c, encoding="utf-8")

    checks = {
        "uncached_console_zone": (
            "A52_DIAG_CONSOLE_PHYS 0xB1B40000ULL" in ram
            and "1, PRZ_FLAG_ZAP_OLD" in ram
        ),
        "normal_ramoops_disabled": "postcore_initcall(ramoops_init);" not in ram,
        "console_backlog_requested": "CON_PRINTBUFFER | CON_ENABLED | CON_ANYTIME" in ram,
        "initcall_begin_instrumented": "A52DIAG BEGIN %pS" in main_c,
        "initcall_end_instrumented": "A52DIAG END %pS ret=%d" in main_c,
        "initialized_after_driver_model": (
            "driver_init();\n\tif (a52_persistent_diag_init())" in main_c
        ),
        "basic_setup_markers": "A52DIAG AFTER do_initcalls" in main_c,
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
                    "write uncached persistent initcall breadcrumbs directly into "
                    "the OrangeFox console ramoops quarter"
                ),
                "console_zone": {
                    "physical_address": "0xB1B40000",
                    "size": "0x00040000",
                    "memory_type": "uncached",
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
