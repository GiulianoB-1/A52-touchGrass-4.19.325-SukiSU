#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MARKER = "A52_ACK_SECURE_FLIGHT_RECORDER"
HEADER_REL = Path("include/linux/a52_ack_secure_flight_recorder.h")
SOURCE_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
SECURE_MAKEFILE_REL = Path("drivers/a52_secure/Makefile")
ION_REL = Path("drivers/staging/android/ion/ion.c")
QSEE_REL = Path("drivers/a52_secure/qseecom.c")

HEADER = r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_A52_ACK_SECURE_FLIGHT_RECORDER_H
#define _LINUX_A52_ACK_SECURE_FLIGHT_RECORDER_H

#include <linux/compiler.h>

void a52_ackfr_record(const char *fmt, ...) __printf(1, 2);

#endif
'''

SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * A52 ACK 5.10 secure-startup flight recorder.
 *
 * Preserve the first ION/QSEECOM events in memory, then copy the bounded
 * timeline into the existing persistent diagnostic console. The dump is
 * repeated at 12, 45 and 90 seconds so either an early reset or a manual
 * recovery reboot leaves a complete working set in RAMOOPS.
 */
#define pr_fmt(fmt) "A52ACKFR: " fmt

#include <linux/atomic.h>
#include <linux/init.h>
#include <linux/jiffies.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/sched.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/workqueue.h>

#include <linux/a52_ack_secure_flight_recorder.h>

#define A52_ACKFR_CAPACITY 512U
#define A52_ACKFR_MESSAGE_LEN 136U
#define A52_ACKFR_DUMP_PASSES 3U

struct a52_ackfr_event {
	u64 seq;
	u64 monotonic_ns;
	pid_t pid;
	pid_t tgid;
	char comm[TASK_COMM_LEN];
	char message[A52_ACKFR_MESSAGE_LEN];
};

static struct a52_ackfr_event a52_ackfr_events[A52_ACKFR_CAPACITY];
static DEFINE_SPINLOCK(a52_ackfr_lock);
static atomic64_t a52_ackfr_sequence = ATOMIC64_INIT(0);
static atomic64_t a52_ackfr_dropped = ATOMIC64_INIT(0);
static unsigned int a52_ackfr_dump_pass;

extern void a52_persistent_diag_mark(const char *fmt, ...);

void a52_ackfr_record(const char *fmt, ...)
{
	struct a52_ackfr_event event;
	unsigned long flags;
	va_list args;
	u64 seq;

	seq = (u64)atomic64_inc_return(&a52_ackfr_sequence);
	if (seq > A52_ACKFR_CAPACITY) {
		atomic64_inc(&a52_ackfr_dropped);
		return;
	}

	memset(&event, 0, sizeof(event));
	event.seq = seq;
	event.monotonic_ns = ktime_get_ns();
	event.pid = current->pid;
	event.tgid = current->tgid;
	get_task_comm(event.comm, current);

	va_start(args, fmt);
	vscnprintf(event.message, sizeof(event.message), fmt, args);
	va_end(args);

	spin_lock_irqsave(&a52_ackfr_lock, flags);
	a52_ackfr_events[seq - 1] = event;
	spin_unlock_irqrestore(&a52_ackfr_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_ackfr_record);

static void a52_ackfr_dump(struct work_struct *work)
{
	struct a52_ackfr_event event;
	unsigned long flags;
	u64 stored;
	u64 dropped;
	u64 cursor;
	unsigned long delay;

	stored = (u64)atomic64_read(&a52_ackfr_sequence);
	if (stored > A52_ACKFR_CAPACITY)
		stored = A52_ACKFR_CAPACITY;
	dropped = (u64)atomic64_read(&a52_ackfr_dropped);
	a52_ackfr_dump_pass++;

	a52_persistent_diag_mark(
		"A52ACKFR BEGIN pass=%u stored=%llu dropped=%llu capacity=%u\n",
		a52_ackfr_dump_pass, (unsigned long long)stored,
		(unsigned long long)dropped, A52_ACKFR_CAPACITY);

	for (cursor = 1; cursor <= stored; cursor++) {
		spin_lock_irqsave(&a52_ackfr_lock, flags);
		event = a52_ackfr_events[cursor - 1];
		spin_unlock_irqrestore(&a52_ackfr_lock, flags);
		if (event.seq != cursor)
			continue;
		a52_persistent_diag_mark(
			"A52ACKFR %llu ns=%llu pid=%d tgid=%d comm=%s %s\n",
			(unsigned long long)event.seq,
			(unsigned long long)event.monotonic_ns,
			event.pid, event.tgid, event.comm, event.message);
	}

	a52_persistent_diag_mark("A52ACKFR END pass=%u\n", a52_ackfr_dump_pass);
	pr_info("persistent dump pass=%u stored=%llu dropped=%llu\n",
		a52_ackfr_dump_pass, (unsigned long long)stored,
		(unsigned long long)dropped);

	if (a52_ackfr_dump_pass >= A52_ACKFR_DUMP_PASSES)
		return;
	delay = a52_ackfr_dump_pass == 1 ? 33000 : 45000;
	schedule_delayed_work(to_delayed_work(work), msecs_to_jiffies(delay));
}

static DECLARE_DELAYED_WORK(a52_ackfr_dump_work, a52_ackfr_dump);

static int __init a52_ackfr_init(void)
{
	schedule_delayed_work(&a52_ackfr_dump_work, msecs_to_jiffies(12000));
	return 0;
}
late_initcall(a52_ackfr_init);
'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def mask_c(text: str) -> str:
    out = list(text)
    state = "normal"
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            if char == "/" and nxt == "/":
                out[index] = out[index + 1] = " "
                state = "line-comment"
                index += 2
                continue
            if char == "/" and nxt == "*":
                out[index] = out[index + 1] = " "
                state = "block-comment"
                index += 2
                continue
            if char == '"':
                out[index] = " "
                state = "string"
                escaped = False
            elif char == "'":
                out[index] = " "
                state = "char"
                escaped = False
        elif state == "line-comment":
            if char == "\n":
                state = "normal"
            else:
                out[index] = " "
        elif state == "block-comment":
            if char == "*" and nxt == "/":
                out[index] = out[index + 1] = " "
                state = "normal"
                index += 2
                continue
            if char != "\n":
                out[index] = " "
        else:
            quote = '"' if state == "string" else "'"
            if char == "\n":
                escaped = False
            else:
                out[index] = " "
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    state = "normal"
        index += 1
    return "".join(out)


def top_level_before(masked: str, position: int) -> bool:
    depth = 0
    for char in masked[:position]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
    return depth == 0


def function_definition(text: str, name: str) -> tuple[int, int, int, str] | None:
    masked = mask_c(text)
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", masked):
        if not top_level_before(masked, match.start()):
            continue
        paren = match.end() - 1
        depth = 0
        close_paren = -1
        for index in range(paren, len(masked)):
            char = masked[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close_paren = index
                    break
        if close_paren < 0:
            continue
        tail = masked[close_paren + 1 : close_paren + 512]
        brace_rel = tail.find("{")
        semi_rel = tail.find(";")
        if brace_rel < 0 or (semi_rel >= 0 and semi_rel < brace_rel):
            continue
        opening = close_paren + 1 + brace_rel
        brace_depth = 0
        for index in range(opening, len(masked)):
            if masked[index] == "{":
                brace_depth += 1
            elif masked[index] == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    signature_start = line_start
                    while signature_start > 0:
                        previous_start = text.rfind("\n", 0, signature_start - 1) + 1
                        previous = text[previous_start:signature_start].strip()
                        if not previous or previous.startswith(("/*", "*", "//", "#")):
                            break
                        if previous.endswith((";", "}", ":")):
                            break
                        signature_start = previous_start
                    return signature_start, opening, index, text[signature_start : opening + 1]
    return None


def identifier(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def entry_expression(domain: str, name: str, signature: str) -> str:
    message = f"{domain} enter fn={name}"
    args: list[str] = []
    if name in {"ion_ioctl", "qseecom_ioctl"}:
        if re.search(r"\bcmd\b", signature):
            message += " cmd=0x%x"
            args.append("cmd")
        if re.search(r"\barg\b", signature):
            message += " arg=0x%lx"
            args.append("arg")
    elif name == "ion_alloc":
        if re.search(r"\blen\b", signature):
            message += " len=%zu"
            args.append("len")
        if re.search(r"\bheap_id_mask\b", signature):
            message += " heap_mask=0x%x"
            args.append("heap_id_mask")
        if re.search(r"\bflags\b", signature):
            message += " flags=0x%x"
            args.append("flags")
    elif re.search(r"\bapp_name\b", signature):
        message += " app=%s"
        args.append('app_name ? app_name : "<null>"')
    suffix = ", " + ", ".join(args) if args else ""
    return f'a52_ackfr_record("{message}"{suffix})'


def inject_entry(text: str, domain: str, name: str) -> tuple[str, bool, str]:
    found = function_definition(text, name)
    if found is None:
        return text, False, "missing"
    _, opening, _, signature = found
    marker = f"{domain} enter fn={name}"
    if marker in text[opening : opening + 1024]:
        return text, False, "already-present"
    variable = f"__a52_ackfr_entry_{identifier(name)}"
    declaration = (
        f"\n\tint {variable} __maybe_unused = "
        f"({entry_expression(domain, name, signature)}, 0);"
    )
    return text[: opening + 1] + declaration + text[opening + 1 :], True, "inserted"


def return_message(domain: str, name: str, expression: str) -> str:
    if name in {"ion_ioctl", "qseecom_ioctl"}:
        return (
            f'a52_ackfr_record("{domain} exit fn={name} cmd=0x%x ret=%ld", '
            f"cmd, (long)({expression}))"
        )
    return (
        f'a52_ackfr_record("{domain} exit fn={name} ret=%ld", '
        f"(long)({expression}))"
    )


def traced_return(indent: str, domain: str, name: str, expression: str) -> str:
    return (
        f"{indent}do {{\n"
        f"{indent}\t{return_message(domain, name, expression)};\n"
        f"{indent}\treturn {expression};\n"
        f"{indent}}} while (0);"
    )


def inject_simple_returns(text: str, domain: str, name: str) -> tuple[str, int]:
    found = function_definition(text, name)
    if found is None:
        return text, 0
    _, opening, closing, _ = found
    body = text[opening + 1 : closing]
    expression = (
        r"(?:ret|rc|retval|fd|result|0|-[A-Z][A-Z0-9_]*|"
        r"PTR_ERR\([A-Za-z_][A-Za-z0-9_]*\))"
    )
    pattern = re.compile(
        rf"(?m)^(?P<indent>[ \t]*)return[ \t]+(?P<expr>{expression})[ \t]*;[ \t]*$"
    )
    matches = list(pattern.finditer(body))
    for match in reversed(matches):
        absolute = opening + 1 + match.start()
        replacement = traced_return(
            match.group("indent"), domain, name, match.group("expr")
        )
        text = text[:absolute] + replacement + text[opening + 1 + match.end() :]
    return text, len(matches)


def strip_noisy_breadcrumb_calls(root: Path) -> dict[str, object]:
    changed_files: list[str] = []
    removed = 0
    for path in sorted(root.rglob("*.[ch]")):
        if not path.is_file():
            continue
        text = read(path)
        needle = "a52_persistent_diag_mark("
        positions: list[tuple[int, int]] = []
        cursor = 0
        while True:
            start = text.find(needle, cursor)
            if start < 0:
                break
            paren = start + len(needle) - 1
            depth = 0
            close = -1
            state = "normal"
            escaped = False
            index = paren
            while index < len(text):
                char = text[index]
                nxt = text[index + 1] if index + 1 < len(text) else ""
                if state == "normal":
                    if char == "/" and nxt == "/":
                        state = "line-comment"
                        index += 2
                        continue
                    if char == "/" and nxt == "*":
                        state = "block-comment"
                        index += 2
                        continue
                    if char == '"':
                        state = "string"
                        escaped = False
                    elif char == "'":
                        state = "char"
                        escaped = False
                    elif char == "(":
                        depth += 1
                    elif char == ")":
                        depth -= 1
                        if depth == 0:
                            close = index
                            break
                elif state == "line-comment":
                    if char == "\n":
                        state = "normal"
                elif state == "block-comment":
                    if char == "*" and nxt == "/":
                        state = "normal"
                        index += 2
                        continue
                else:
                    quote = '"' if state == "string" else "'"
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == quote:
                        state = "normal"
                index += 1
            if close < 0:
                cursor = start + len(needle)
                continue
            inside = text[paren + 1 : close]
            semicolon = close + 1
            while semicolon < len(text) and text[semicolon] in " \t":
                semicolon += 1
            if (
                semicolon < len(text)
                and text[semicolon] == ";"
                and re.search(r'"A52', inside)
            ):
                positions.append((start, semicolon))
                cursor = semicolon + 1
            else:
                cursor = close + 1
        if positions:
            for start, end in reversed(positions):
                text = text[:start] + "do { } while (0)" + text[end:]
            write(path, text)
            changed_files.append(str(path.relative_to(root)))
            removed += len(positions)
    remaining = 0
    for path in root.rglob("*.[ch]"):
        if path.is_file():
            remaining += len(
                re.findall(
                    r'a52_persistent_diag_mark\s*\(\s*"A52', read(path)
                )
            )
    if remaining:
        raise SystemExit(f"noisy breadcrumb cleanup incomplete: {remaining} calls remain")
    return {"removed_calls": removed, "changed_files": changed_files, "remaining": remaining}


def stage_recorder(root: Path) -> dict[str, object]:
    write(root / HEADER_REL, HEADER)
    write(root / SOURCE_REL, SOURCE)
    makefile = root / SECURE_MAKEFILE_REL
    text = read(makefile)
    addition = f"\n# {MARKER}\nobj-y += a52_ack_secure_flight_recorder.o\n"
    changed = MARKER not in text
    if changed:
        write(makefile, text.rstrip() + addition)
    return {
        "header": str(HEADER_REL),
        "source": str(SOURCE_REL),
        "makefile_changed": changed,
        "capacity": 512,
        "dump_delays_ms": [12000, 45000, 90000],
        "dump_prefix": "A52ACKFR",
    }


def patch_ion(root: Path) -> dict[str, object]:
    path = root / ION_REL
    text = read(path)
    include = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    anchor = '#include <linux/uaccess.h>\n'
    if include not in text:
        if anchor not in text:
            raise SystemExit("ION include anchor missing")
        text = text.replace(anchor, anchor + include, 1)
    staged: list[dict[str, object]] = []
    for name in ("ion_ioctl", "ion_alloc"):
        text, _, state = inject_entry(text, "ION", name)
        exits = 0
        if name == "ion_ioctl" and state != "missing":
            text, exits = inject_simple_returns(text, "ION", name)
        staged.append({"name": name, "entry": state, "exit_count": exits})
    present = {item["name"] for item in staged if item["entry"] != "missing"}
    required = {"ion_ioctl", "ion_alloc"}
    if present != required:
        raise SystemExit(
            f"required ION trace functions missing: {sorted(required - present)}"
        )
    ion_ioctl = next(item for item in staged if item["name"] == "ion_ioctl")
    if not ion_ioctl["exit_count"]:
        raise SystemExit("ION ioctl return tracing missing")
    write(path, text)
    return {"source": str(ION_REL), "functions": staged}


def patch_qsee(root: Path) -> dict[str, object]:
    path = root / QSEE_REL
    text = read(path)
    include = '#include <linux/a52_ack_secure_flight_recorder.h>\n'
    anchor = '#include <linux/module.h>\n'
    if include not in text:
        if anchor in text:
            text = text.replace(anchor, anchor + include, 1)
        else:
            text = include + text
    names = (
        "qseecom_open",
        "qseecom_release",
        "qseecom_ioctl",
        "qseecom_start_app",
        "qseecom_shutdown_app",
        "qseecom_load_app",
        "qseecom_unload_app",
        "__qseecom_load_fw",
        "qseecom_send_command",
        "qseecom_send_modfd_cmd",
        "__qseecom_send_cmd",
    )
    staged: list[dict[str, object]] = []
    for name in names:
        text, _, state = inject_entry(text, "QSEE", name)
        exits = 0
        if state != "missing":
            text, exits = inject_simple_returns(text, "QSEE", name)
        staged.append({"name": name, "entry": state, "exit_count": exits})
    present = {item["name"] for item in staged if item["entry"] != "missing"}
    required = (
        {"qseecom_open"},
        {"qseecom_release"},
        {"qseecom_ioctl"},
        {"qseecom_start_app", "qseecom_load_app", "__qseecom_load_fw"},
        {"qseecom_send_command", "qseecom_send_modfd_cmd", "__qseecom_send_cmd"},
    )
    missing = [sorted(group) for group in required if not group.intersection(present)]
    if missing:
        raise SystemExit("required QSEECOM trace groups missing: " + repr(missing))
    qsee_ioctl = next(item for item in staged if item["name"] == "qseecom_ioctl")
    send = [item for item in staged if item["name"] == "__qseecom_send_cmd"]
    if not qsee_ioctl["exit_count"]:
        raise SystemExit("QSEECOM ioctl return tracing missing")
    if send and not send[0]["exit_count"]:
        raise SystemExit("__qseecom_send_cmd return tracing missing")
    write(path, text)
    return {"source": str(QSEE_REL), "functions": staged}


def self_test() -> None:
    sample = """static long qseecom_ioctl(struct file *f, unsigned int cmd, unsigned long arg)\n{\n\tint ret = 0;\n\tif (cmd)\n\t\treturn -EINVAL;\n\treturn ret;\n}\n"""
    patched, _, state = inject_entry(sample, "QSEE", "qseecom_ioctl")
    if state != "inserted" or "cmd=0x%x" not in patched:
        raise SystemExit("entry-injection self-test failed")
    patched, count = inject_simple_returns(patched, "QSEE", "qseecom_ioctl")
    if count != 2 or "if (cmd)\n\t\tdo {" not in patched:
        raise SystemExit("return-wrapping self-test failed")
    noisy = 'void f(void) {\n\ta52_persistent_diag_mark("A52UFS x=%d\\n", 1);\n}\n'
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.c"
        path.write_text(noisy)
        result = strip_noisy_breadcrumb_calls(Path(tmp))
        if result["removed_calls"] != 1 or "A52UFS" in path.read_text():
            raise SystemExit("breadcrumb-cleanup self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()
    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    required = [ION_REL, QSEE_REL, SECURE_MAKEFILE_REL, Path("fs/pstore/ram.c")]
    missing = [str(item) for item in required if not (root / item).is_file()]
    if missing:
        raise SystemExit("missing staged ACK files: " + ", ".join(missing))
    cleanup = strip_noisy_breadcrumb_calls(root)
    report = {
        "status": "ack-secure-first-events-flight-recorder-staged",
        "hardware_validated": False,
        "marker": MARKER,
        "recorder": stage_recorder(root),
        "ion": patch_ion(root),
        "qsee": patch_qsee(root),
        "breadcrumb_cleanup": cleanup,
        "reference_observation": {
            "working_ion_free": "0xc0044901 returned -ENOTTY",
            "working_ion_alloc": "0xc0184900 fd-returning allocation",
            "working_qsee_send_cmd": "0xc0209703 returned 0",
        },
        "scope": (
            "first 512 ACK ION/QSEECOM events preserved and dumped into the existing "
            "persistent console at 12, 45 and 90 seconds; obsolete bring-up breadcrumbs removed"
        ),
    }
    (output / "phase16-ack-secure-flight-recorder-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "phase16-ion-qsee-runtime-trace-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
