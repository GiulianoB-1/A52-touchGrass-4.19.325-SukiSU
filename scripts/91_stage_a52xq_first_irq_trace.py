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
            "Record only the first A52 GICv3 interrupt before nested IRQs can be "
            "re-enabled. The former 64-event formatted trace was removed after "
            "Run 22 captured a kernel stack overflow during an IRQ 19 storm."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    path = gki / "drivers/irqchip/irq-gic-v3.c"
    if not path.is_file():
        raise SystemExit("drivers/irqchip/irq-gic-v3.c is missing")

    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "#include <linux/acpi.h>\n",
        "#include <linux/acpi.h>\n#include <linux/atomic.h>\n",
        "add atomic include",
    )

    declarations = r'''/*
 * Record only the first accepted IRQ. This call runs immediately after IAR
 * read, while local IRQs are still masked, so the persistent formatter cannot
 * recursively consume the kernel stack during an interrupt storm.
 */
extern void a52_persistent_diag_mark(const char *fmt, ...);
static atomic_t a52_first_irq_recorded = ATOMIC_INIT(0);

'''
    text = replace_once(
        text,
        "struct redist_region {\n",
        declarations + "struct redist_region {\n",
        "add one-shot IRQ trace declarations",
    )

    iar_anchor = "\tirqnr = do_read_iar(regs);\n"
    first_irq_trace = (
        iar_anchor
        + "\tif (atomic_cmpxchg(&a52_first_irq_recorded, 0, 1) == 0)\n"
        + "\t\ta52_persistent_diag_mark(\n"
        + '\t\t\t"A52IRQ FIRST cpu=%u irq=%u\\n",\n'
        + "\t\t\traw_smp_processor_id(), irqnr);\n"
    )
    text = replace_once(
        text,
        iar_anchor,
        first_irq_trace,
        "instrument one-shot first IRQ",
    )

    checks = {
        "atomic_include": "#include <linux/atomic.h>" in text,
        "persistent_helper_declared": (
            "extern void a52_persistent_diag_mark(const char *fmt, ...);" in text
        ),
        "one_shot_atomic_gate": (
            "atomic_cmpxchg(&a52_first_irq_recorded, 0, 1) == 0" in text
        ),
        "first_irq_marker": text.count("A52IRQ FIRST cpu=%u irq=%u") == 1,
        "heavy_enter_marker_absent": "A52IRQ ENTER" not in text,
        "heavy_dispatch_marker_absent": "A52IRQ DISPATCH" not in text,
        "heavy_return_marker_absent": "A52IRQ RETURN" not in text,
        "former_64_event_limit_absent": "A52_IRQ_TRACE_LIMIT 64" not in text,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("one-shot IRQ trace staging audit failed: " + ", ".join(failed))

    path.write_text(text, encoding="utf-8")
    (output / "patched-irq-gic-v3.c").write_text(text, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "trace_mode": "one-shot-first-irq-before-nested-enable",
                "trace_limit": 1,
                "prior_failure": (
                    "Run 22: Kernel stack overflow during repeated IRQ 19 tracing"
                ),
                "purpose": (
                    "preserve the first IRQ number without formatting from nested "
                    "IRQ context or amplifying an interrupt storm"
                ),
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
