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
        description="Move built-in ramoops registration before normal initcalls"
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
        "static int __init ramoops_init(void)\n",
        "int __init ramoops_init(void)\n",
        "make ramoops_init callable",
    )
    ram = replace_once(
        ram,
        "postcore_initcall(ramoops_init);\n",
        "/* A52 bring-up: init/main.c invokes built-in ramoops immediately after\n"
        " * driver_init(), before the normal initcall levels. This keeps the\n"
        " * platform bus available while capturing pure/core/postcore hangs. */\n",
        "remove normal postcore registration",
    )

    declaration_anchor = "static void __init do_basic_setup(void)\n"
    declaration = (
        "#if IS_BUILTIN(CONFIG_PSTORE_RAM)\n"
        "extern int __init ramoops_init(void);\n"
        "#endif\n\n"
        + declaration_anchor
    )
    main_c = replace_once(
        main_c,
        declaration_anchor,
        declaration,
        "declare ramoops_init before do_basic_setup",
    )

    driver_anchor = "\tdriver_init();\n\tinit_irq_proc();\n"
    driver_replacement = (
        "\tdriver_init();\n"
        "#if IS_BUILTIN(CONFIG_PSTORE_RAM)\n"
        "\t/* A52 bring-up: the platform bus now exists, but no normal initcall\n"
        "\t * has run yet. Register the persistent console here so CON_PRINTBUFFER\n"
        "\t * copies the existing printk backlog and captures the first blocking\n"
        "\t * pure/core/postcore initcall. */\n"
        "\tif (ramoops_init())\n"
        "\t\tpr_err(\"A52 pre-initcall ramoops registration failed\\n\");\n"
        "\telse\n"
        "\t\tpr_info(\"A52 pre-initcall ramoops registration complete\\n\");\n"
        "#endif\n"
        "\tinit_irq_proc();\n"
    )
    main_c = replace_once(
        main_c,
        driver_anchor,
        driver_replacement,
        "invoke ramoops immediately after driver_init",
    )

    ram_path.write_text(ram, encoding="utf-8")
    main_path.write_text(main_c, encoding="utf-8")

    checks = {
        "ramoops_init_global": "int __init ramoops_init(void)" in ram,
        "postcore_initcall_removed": "postcore_initcall(ramoops_init);" not in ram,
        "main_declares_ramoops": "extern int __init ramoops_init(void);" in main_c,
        "main_calls_after_driver_init": (
            "driver_init();\n#if IS_BUILTIN(CONFIG_PSTORE_RAM)" in main_c
            and "A52 pre-initcall ramoops registration complete" in main_c
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
                "purpose": "register ramoops after driver_init and before normal initcalls",
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
