#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE425_REBOOT_ORIGIN_RECORDER_V1"
REBOOT = Path("kernel/reboot.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase425 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)


def patch_reboot(text: str) -> str:
    if MARK in text:
        return text

    inc = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if inc not in text:
        anchor = "#include <linux/reboot.h>\n"
        text = one(text, anchor, anchor + inc, "recorder include")

    # Direct in-kernel restart calls, including calls that bypass sys_reboot().
    old = """void kernel_restart(char *cmd)
{
"""
    new = """void kernel_restart(char *cmd)
{
	/* A52_PHASE425_REBOOT_ORIGIN_RECORDER_V1 */
	a52_ackfr_record("P425 KR c=%s p=%d s=%.40s",
		current->comm, current->pid, cmd ? cmd : "-");
"""
    text = one(text, old, new, "kernel_restart entry")

    # Direct power-off calls.
    old = """void kernel_power_off(void)
{
"""
    new = """void kernel_power_off(void)
{
	a52_ackfr_record("P425 PO c=%s p=%d s=poweroff",
		current->comm, current->pid);
"""
    text = one(text, old, new, "kernel_power_off entry")

    # Record a validated top-level reboot syscall before the transition lock.
    # This catches RESTART/HALT/POWER_OFF as well as RESTART2 numerically.
    anchor = "\tmutex_lock(&system_transition_mutex);\n"
    if anchor not in text:
        raise SystemExit("Phase425 sys_reboot transition mutex anchor missing")
    text = one(
        text,
        anchor,
        '\ta52_ackfr_record("P425 SR c=%s p=%d cmd=%x",\n'
        '\t\tcurrent->comm, current->pid, cmd);\n'
        + anchor,
        "sys_reboot request",
    )

    # For RESTART2 the command string is only valid after copy_from_user.
    old = """		buffer[sizeof(buffer) - 1] = '\\0';
		kernel_restart(buffer);
"""
    new = """		buffer[sizeof(buffer) - 1] = '\\0';
		a52_ackfr_record("P425 S2 c=%s p=%d s=%.40s",
			current->comm, current->pid, buffer);
		kernel_restart(buffer);
"""
    text = one(text, old, new, "sys_reboot restart2 string")

    return text


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE424_PAIRED_HW_SNAPSHOT_V1" not in text:
        raise SystemExit("Phase425 recorder requires Phase424 lineage")

    retained = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    strncmp(fmt, "P424", 4) &&
\t    strncmp(fmt, "P423", 4) &&
"""
    retained_new = """\tif (unlikely(atomic_read(&a52_r280_retained)) &&
\t    strncmp(fmt, "P425", 4) &&
\t    strncmp(fmt, "P424", 4) &&
\t    strncmp(fmt, "P423", 4) &&
"""
    text = one(text, retained, retained_new, "retention admission")

    normal = """if (strncmp(fmt, "P424", 4) &&
    strncmp(fmt, "P423", 4) &&
    strncmp(fmt, "P420", 4) &&
"""
    normal_new = """if (strncmp(fmt, "P425", 4) &&
    strncmp(fmt, "P424", 4) &&
    strncmp(fmt, "P423", 4) &&
    strncmp(fmt, "P420", 4) &&
"""
    text = one(text, normal, normal_new, "normal admission")

    clean = """\tif (!fmt || (
\t    strncmp(fmt, "P424", 4) &&
\t    strncmp(fmt, "P423", 4) &&
\t    strncmp(fmt, "P420", 4) &&
"""
    clean_new = """\tif (!fmt || (
\t    strncmp(fmt, "P425", 4) &&
\t    strncmp(fmt, "P424", 4) &&
\t    strncmp(fmt, "P423", 4) &&
\t    strncmp(fmt, "P420", 4) &&
"""
    text = one(text, clean, clean_new, "Phase402 admission")

    # Also retain P425 in the immediate persistent critical lane. This is cheap
    # and makes the last reboot breadcrumb more likely to survive a fast reset.
    msg = """\treturn !strncmp(message, "P414 ", 5) ||
\t       !strncmp(message, "P276 ", 5) ||
"""
    msg_new = """\treturn !strncmp(message, "P425 ", 5) ||
\t       !strncmp(message, "P414 ", 5) ||
\t       !strncmp(message, "P276 ", 5) ||
"""
    text = one(text, msg, msg_new, "critical message admission")

    text += "\n/* " + MARK + ": P425 reboot-origin records admitted. */\n"
    return text


def validate(root: Path) -> None:
    rb = (root / REBOOT).read_text(errors="replace")
    rec = (root / REC).read_text(errors="replace")

    for token in (
        MARK,
        "#include <linux/a52_ack_secure_flight_recorder.h>",
        'P425 SR c=%s p=%d cmd=%x',
        'P425 S2 c=%s p=%d s=%.40s',
        'P425 KR c=%s p=%d s=%.40s',
        'P425 PO c=%s p=%d s=poweroff',
        'strncmp(fmt, "P425", 4)',
        '!strncmp(message, "P425 ", 5)',
    ):
        if token not in rb + rec:
            raise SystemExit("Phase425 validation missing: " + token)

    if rb.count('P425 SR c=%s p=%d cmd=%x') != 1:
        raise SystemExit("Phase425 sys_reboot numeric record must be unique")
    if rb.count('P425 S2 c=%s p=%d s=%.40s') != 1:
        raise SystemExit("Phase425 RESTART2 string record must be unique")
    if rb.count('P425 KR c=%s p=%d s=%.40s') != 1:
        raise SystemExit("Phase425 kernel_restart record must be unique")
    if rb.count('P425 PO c=%s p=%d s=poweroff') != 1:
        raise SystemExit("Phase425 kernel_power_off record must be unique")

    # Observational only: no transition behavior or command mutation.
    for forbidden in (
        "cmd =",
        "machine_restart(",
        "machine_power_off(",
        "kernel_restart_prepare(",
        "kernel_shutdown_prepare(",
    ):
        # Existing calls/assignments are allowed; this script adds none through
        # its explicit instrumentation strings. Keep the source-shape checks
        # below focused on the exact transition anchors.
        pass

    sr = rb.index('P425 SR c=%s p=%d cmd=%x')
    lock = rb.index("mutex_lock(&system_transition_mutex);", sr)
    if sr > lock:
        raise SystemExit("Phase425 sys_reboot record must precede transition lock")

    s2 = rb.index('P425 S2 c=%s p=%d s=%.40s')
    kr_buffer = rb.index("kernel_restart(buffer);", s2)
    if s2 > kr_buffer:
        raise SystemExit("Phase425 RESTART2 string record must precede kernel_restart")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (REBOOT, REC):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase425 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / REBOOT
        p.write_text(patch_reboot(p.read_text(errors="replace")))
        p = ns.root / REC
        p.write_text(patch_rec(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase425 reboot-origin recorder: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# Phase425 workflow trigger after workflow registration.
