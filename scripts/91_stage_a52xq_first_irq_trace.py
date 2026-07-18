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
            "Instrument the A52 GICv3 IRQ entry path so the persistent early-boot "
            "ring records the first interrupts seen after local_irq_enable()."
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

    include_anchor = "#include <linux/acpi.h>\n"
    include_replacement = (
        "#include <linux/acpi.h>\n"
        "#include <linux/atomic.h>\n"
    )
    text = replace_once(
        text,
        include_anchor,
        include_replacement,
        "add atomic include",
    )

    struct_anchor = "struct redist_region {\n"
    declarations = r'''/*
 * Recovery-readable trace for the first IRQs accepted immediately after
 * start_kernel() enables DAIF.I. The Workflow 83 persistent ring is already
 * initialized at this point. Keep the trace bounded so an interrupt storm
 * cannot wrap away the first unmatched entry.
 */
#define A52_IRQ_TRACE_LIMIT 64
extern void a52_persistent_diag_mark(const char *fmt, ...);
static atomic_t a52_irq_trace_count = ATOMIC_INIT(0);

'''
    text = replace_once(
        text,
        struct_anchor,
        declarations + struct_anchor,
        "add IRQ trace declarations",
    )

    function_old = r'''static asmlinkage void __exception_irq_entry gic_handle_irq(struct pt_regs *regs)
{
	u32 irqnr;

	irqnr = do_read_iar(regs);

	/* Check for special IDs first */
	if ((irqnr >= 1020 && irqnr <= 1023))
		return;

	if (gic_supports_nmi() &&
	    unlikely(gic_read_rpr() == GICD_INT_RPR_PRI(GICD_INT_NMI_PRI))) {
		gic_handle_nmi(irqnr, regs);
		return;
	}

	if (gic_prio_masking_enabled()) {
		gic_pmr_mask_irqs();
		gic_arch_enable_irqs();
	}

	if (static_branch_likely(&supports_deactivate_key))
		gic_write_eoir(irqnr);
	else
		isb();

	if (handle_domain_irq(gic_data.domain, irqnr, regs)) {
		WARN_ONCE(true, "Unexpected interrupt received!\n");
		log_abnormal_wakeup_reason("unexpected HW IRQ %u", irqnr);
		gic_deactivate_unhandled(irqnr);
	}
}
'''

    function_new = r'''static asmlinkage void __exception_irq_entry gic_handle_irq(struct pt_regs *regs)
{
	u32 irqnr;
	int a52_seq;
	int a52_ret;

	irqnr = do_read_iar(regs);
	a52_seq = atomic_inc_return(&a52_irq_trace_count);
	if (a52_seq <= A52_IRQ_TRACE_LIMIT)
		a52_persistent_diag_mark(
			"A52IRQ ENTER seq=%d cpu=%u irq=%u\n",
			a52_seq, raw_smp_processor_id(), irqnr);

	/* Check for special IDs first */
	if ((irqnr >= 1020 && irqnr <= 1023)) {
		if (a52_seq <= A52_IRQ_TRACE_LIMIT)
			a52_persistent_diag_mark(
				"A52IRQ SPECIAL seq=%d cpu=%u irq=%u\n",
				a52_seq, raw_smp_processor_id(), irqnr);
		return;
	}

	if (gic_supports_nmi() &&
	    unlikely(gic_read_rpr() == GICD_INT_RPR_PRI(GICD_INT_NMI_PRI))) {
		if (a52_seq <= A52_IRQ_TRACE_LIMIT)
			a52_persistent_diag_mark(
				"A52IRQ NMI-BEGIN seq=%d cpu=%u irq=%u\n",
				a52_seq, raw_smp_processor_id(), irqnr);
		gic_handle_nmi(irqnr, regs);
		if (a52_seq <= A52_IRQ_TRACE_LIMIT)
			a52_persistent_diag_mark(
				"A52IRQ NMI-END seq=%d cpu=%u irq=%u\n",
				a52_seq, raw_smp_processor_id(), irqnr);
		return;
	}

	if (gic_prio_masking_enabled()) {
		gic_pmr_mask_irqs();
		gic_arch_enable_irqs();
	}

	if (static_branch_likely(&supports_deactivate_key))
		gic_write_eoir(irqnr);
	else
		isb();

	if (a52_seq <= A52_IRQ_TRACE_LIMIT)
		a52_persistent_diag_mark(
			"A52IRQ DISPATCH seq=%d cpu=%u irq=%u\n",
			a52_seq, raw_smp_processor_id(), irqnr);

	a52_ret = handle_domain_irq(gic_data.domain, irqnr, regs);

	if (a52_seq <= A52_IRQ_TRACE_LIMIT)
		a52_persistent_diag_mark(
			"A52IRQ RETURN seq=%d cpu=%u irq=%u ret=%d\n",
			a52_seq, raw_smp_processor_id(), irqnr, a52_ret);

	if (a52_ret) {
		WARN_ONCE(true, "Unexpected interrupt received!\n");
		log_abnormal_wakeup_reason("unexpected HW IRQ %u", irqnr);
		gic_deactivate_unhandled(irqnr);
	}
}
'''

    text = replace_once(
        text,
        function_old,
        function_new,
        "instrument gic_handle_irq",
    )

    checks = {
        "atomic_include": "#include <linux/atomic.h>" in text,
        "bounded_trace": "#define A52_IRQ_TRACE_LIMIT 64" in text,
        "persistent_helper_declared": (
            "extern void a52_persistent_diag_mark(const char *fmt, ...);" in text
        ),
        "irq_enter_marker": "A52IRQ ENTER seq=%d cpu=%u irq=%u" in text,
        "irq_dispatch_marker": "A52IRQ DISPATCH seq=%d cpu=%u irq=%u" in text,
        "irq_return_marker": "A52IRQ RETURN seq=%d cpu=%u irq=%u ret=%d" in text,
        "special_irq_marker": "A52IRQ SPECIAL seq=%d cpu=%u irq=%u" in text,
        "nmi_markers": (
            "A52IRQ NMI-BEGIN" in text and "A52IRQ NMI-END" in text
        ),
        "domain_return_captured": (
            "a52_ret = handle_domain_irq(gic_data.domain, irqnr, regs);" in text
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("first IRQ trace staging audit failed: " + ", ".join(failed))

    path.write_text(text, encoding="utf-8")
    (output / "patched-irq-gic-v3.c").write_text(text, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "trace_limit": 64,
                "trace_point": "gic_handle_irq",
                "purpose": (
                    "identify the first IRQ whose dispatch begins but does not return "
                    "after start_kernel local_irq_enable"
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
