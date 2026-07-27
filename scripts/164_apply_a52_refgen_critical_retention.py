#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
REPORT_NAME = "phase27-a52-refgen-critical-retention-report.json"
MARKER = "A52_USR2_CRITICAL_PERSIST_V1"

HELPER = r'''
/* A52_USR2_CRITICAL_PERSIST_V1
 *
 * Preserve the bounded early boot window, then persist only records required
 * to diagnose the REFGEN/display failure. Routine ION/QSEE traffic after the
 * bounded window must not evict REFGEN, display, watchdog, or heartbeat data.
 */
static bool a52_usr2_is_critical_message(const char *message)
{
	if (!message)
		return false;

	return !strncmp(message, "BOOT ", 5) ||
	       !strncmp(message, "HB ", 3) ||
	       !strncmp(message, "REFGEN ", 7) ||
	       !strncmp(message, "DISP ", 5) ||
	       !strncmp(message, "WDT ", 4);
}
'''.strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor mismatch: expected 1, found {count}")
    return text.replace(old, new, 1)


def patch_text(text: str) -> str:
    if MARKER in text:
        audit_text(text)
        return text

    text = replace_once(
        text,
        "#define A52_USR2_CAPACITY 1536U",
        "#define A52_USR2_CAPACITY 768U",
        "capacity",
    )

    function_anchor = "void a52_ackfr_record(const char *fmt, ...)\n"
    text = replace_once(
        text,
        function_anchor,
        HELPER + "\n\n" + function_anchor,
        "critical helper",
    )

    declaration_old = (
        "\tunsigned int written;\n"
        "\tbool buffered;\n"
        "\tva_list args;\n"
        "\tu64 seq;\n"
    )
    declaration_new = (
        "\tunsigned int written;\n"
        "\tbool buffered;\n"
        "\tbool critical;\n"
        "\tva_list args;\n"
        "\tu64 seq;\n"
    )
    text = replace_once(
        text, declaration_old, declaration_new, "critical declaration"
    )

    policy_old = (
        "\tseq = (u64)atomic64_inc_return(&a52_usr2_sequence);\n"
        "\t/* A52_USR2_CONTINUOUS_PERSIST */\n"
        "\tbuffered = seq <= A52_USR2_CAPACITY;\n"
        "\tif (!buffered)\n"
        "\t\tatomic64_inc(&a52_usr2_dropped);\n"
    )
    policy_new = (
        "\tseq = (u64)atomic64_inc_return(&a52_usr2_sequence);\n"
        "\tbuffered = seq <= A52_USR2_CAPACITY;\n"
        "\tif (!buffered)\n"
        "\t\tatomic64_inc(&a52_usr2_dropped);\n"
    )
    text = replace_once(text, policy_old, policy_new, "continuous persistence")

    format_old = (
        "\tva_start(args, fmt);\n"
        "\tvscnprintf(event.message, sizeof(event.message), fmt, args);\n"
        "\tva_end(args);\n"
    )
    format_new = (
        format_old
        + "\tcritical = a52_usr2_is_critical_message(event.message);\n"
        + "\tif (!buffered && !critical)\n"
        + "\t\treturn;\n"
    )
    text = replace_once(text, format_old, format_new, "formatted policy gate")

    control_old = (
        '"A52USR2 %s release=%s capacity=%u line_len=%u '
        'banks=console,ftrace metadata_only=1 commit=%08x\\n",'
    )
    control_new = (
        '"A52USR2 %s release=%s capacity=%u line_len=%u '
        'banks=console,ftrace metadata_only=1 '
        'policy=critical-after-capacity commit=%08x\\n",'
    )
    text = replace_once(text, control_old, control_new, "control policy marker")

    audit_text(text)
    return text


def audit_text(text: str) -> None:
    required = (
        MARKER,
        "#define A52_USR2_CAPACITY 768U",
        "a52_usr2_is_critical_message",
        'strncmp(message, "REFGEN ", 7)',
        'strncmp(message, "DISP ", 5)',
        'strncmp(message, "WDT ", 4)',
        'strncmp(message, "HB ", 3)',
        "if (!buffered && !critical)",
        "policy=critical-after-capacity",
    )
    missing = [item for item in required if item not in text]
    forbidden = (
        "#define A52_USR2_CAPACITY 1536U",
        "A52_USR2_CONTINUOUS_PERSIST",
    )
    present_forbidden = [item for item in forbidden if item in text]
    if missing or present_forbidden:
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if present_forbidden:
            detail.append("forbidden=" + ",".join(present_forbidden))
        raise SystemExit("critical retention audit failed: " + " ".join(detail))


def run(gki: Path, output: Path) -> dict[str, object]:
    recorder = gki / RECORDER_REL
    if not recorder.is_file():
        raise SystemExit(f"recorder source missing: {recorder}")

    original = recorder.read_text(encoding="utf-8", errors="strict")
    final = patch_text(original)
    recorder.write_text(final, encoding="utf-8")

    report = {
        "status": "a52-refgen-critical-retention-v1-staged",
        "hardware_validated": False,
        "source": str(RECORDER_REL),
        "changed": final != original,
        "capacity": 768,
        "policy": "bounded-first-window-then-critical-only",
        "critical_prefixes": ["BOOT ", "HB ", "REFGEN ", "DISP ", "WDT "],
        "routine_after_capacity_dropped": True,
        "critical_after_capacity_persisted": True,
        "payload_capture": False,
        "evidence_basis": {
            "capture_sha256": "797f1e95958032b0f4052373b2920fc4c12c0ef77f2c8d544e171d4b36e12f10",
            "observed_a52usr2_markers": 1224,
            "observed_heartbeat_ticks": 24,
            "observed_refgen_markers": 0,
            "observed_display_markers": 0,
            "finding": "continuous ION/QSEE persistence evicted the early REFGEN/display window",
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def self_test() -> None:
    fixture = r'''
#include <linux/string.h>
#define A52_USR2_CAPACITY 1536U
static void a52_usr2_write_control(const char *kind)
{
	len = scnprintf(line, sizeof(line),
		"A52USR2 %s release=%s capacity=%u line_len=%u banks=console,ftrace metadata_only=1 commit=%08x\n",
		kind, init_utsname()->release, A52_USR2_CAPACITY,
		A52_USR2_LINE_LEN, A52_USR2_COMMIT);
}
void a52_ackfr_record(const char *fmt, ...)
{
	struct a52_usr2_event event;
	unsigned long irq_flags;
	unsigned int written;
	bool buffered;
	va_list args;
	u64 seq;

	seq = (u64)atomic64_inc_return(&a52_usr2_sequence);
	/* A52_USR2_CONTINUOUS_PERSIST */
	buffered = seq <= A52_USR2_CAPACITY;
	if (!buffered)
		atomic64_inc(&a52_usr2_dropped);

	memset(&event, 0, sizeof(event));
	va_start(args, fmt);
	vscnprintf(event.message, sizeof(event.message), fmt, args);
	va_end(args);

	if (buffered) {
		do_store();
	}
}
'''.lstrip()
    patched = patch_text(fixture)
    audit_text(patched)
    if patch_text(patched) != patched:
        raise SystemExit("idempotence self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preserve REFGEN/display evidence after the bounded A52USR2 boot window."
    )
    parser.add_argument("--gki", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print(json.dumps({"status": "self-test-passed"}, sort_keys=True))
        return 0
    if args.gki is None or args.output is None:
        parser.error("--gki and --output are required unless --self-test is used")

    report = run(args.gki.resolve(), args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
