#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MARKER = "A52_TOUCHGRASS_KEYMASTER_FLIGHT_RECORDER"
HEADER_REL = Path("include/linux/a52_keymaster_flight_recorder.h")
SOURCE_REL = Path("drivers/misc/a52_keymaster_flight_recorder.c")
QSEE_REL = Path("drivers/misc/qseecom.c")
ION_REL = Path("drivers/staging/android/ion/ion.c")
ION_IOCTL_REL = Path("drivers/staging/android/ion/ion-ioctl.c")
MISC_MAKEFILE_REL = Path("drivers/misc/Makefile")

RECORDER_HEADER = r'''/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _LINUX_A52_KEYMASTER_FLIGHT_RECORDER_H
#define _LINUX_A52_KEYMASTER_FLIGHT_RECORDER_H

#include <linux/compiler.h>

void a52_kmfr_record(const char *fmt, ...) __printf(1, 2);

#endif
'''

RECORDER_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0
/*
 * A52 TouchGrass Keymaster flight recorder.
 *
 * Preserves the first secure-startup events instead of overwriting them with
 * later traffic. Read /proc/a52_keymaster_flight_recorder after Android boots.
 */
#define pr_fmt(fmt) "A52KMFR: " fmt

#include <linux/atomic.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/proc_fs.h>
#include <linux/sched.h>
#include <linux/seq_file.h>
#include <linux/spinlock.h>
#include <linux/string.h>

#include <linux/a52_keymaster_flight_recorder.h>

#define A52_KMFR_CAPACITY 8192U
#define A52_KMFR_MESSAGE_LEN 192U

struct a52_kmfr_event {
	u64 seq;
	u64 monotonic_ns;
	pid_t pid;
	pid_t tgid;
	char comm[TASK_COMM_LEN];
	char message[A52_KMFR_MESSAGE_LEN];
};

static struct a52_kmfr_event a52_kmfr_ring[A52_KMFR_CAPACITY];
static DEFINE_SPINLOCK(a52_kmfr_lock);
static atomic64_t a52_kmfr_sequence = ATOMIC64_INIT(0);
static atomic64_t a52_kmfr_dropped = ATOMIC64_INIT(0);

void a52_kmfr_record(const char *fmt, ...)
{
	struct a52_kmfr_event event;
	unsigned long irq_flags;
	va_list args;
	u64 seq;

	seq = (u64)atomic64_inc_return(&a52_kmfr_sequence);
	if (seq > A52_KMFR_CAPACITY) {
		atomic64_inc(&a52_kmfr_dropped);
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

	spin_lock_irqsave(&a52_kmfr_lock, irq_flags);
	a52_kmfr_ring[seq - 1] = event;
	spin_unlock_irqrestore(&a52_kmfr_lock, irq_flags);

	pr_info_ratelimited("seq=%llu pid=%d tgid=%d comm=%s %s\n",
		(unsigned long long)event.seq, event.pid, event.tgid,
		event.comm, event.message);
}
EXPORT_SYMBOL_GPL(a52_kmfr_record);

static int a52_kmfr_show(struct seq_file *m, void *unused)
{
	struct a52_kmfr_event event;
	unsigned long irq_flags;
	u64 end;
	u64 cursor;
	u64 dropped;

	end = (u64)atomic64_read(&a52_kmfr_sequence);
	if (end > A52_KMFR_CAPACITY)
		end = A52_KMFR_CAPACITY;
	dropped = (u64)atomic64_read(&a52_kmfr_dropped);

	seq_printf(m, "# %s mode=first-events capacity=%u stored=%llu dropped=%llu\n",
		"A52 TouchGrass Keymaster flight recorder",
		A52_KMFR_CAPACITY, (unsigned long long)end,
		(unsigned long long)dropped);

	for (cursor = 1; cursor <= end; cursor++) {
		spin_lock_irqsave(&a52_kmfr_lock, irq_flags);
		event = a52_kmfr_ring[cursor - 1];
		spin_unlock_irqrestore(&a52_kmfr_lock, irq_flags);

		if (event.seq != cursor)
			continue;

		seq_printf(m, "%llu %llu pid=%d tgid=%d comm=%s %s\n",
			(unsigned long long)event.seq,
			(unsigned long long)event.monotonic_ns,
			event.pid, event.tgid, event.comm, event.message);
	}

	return 0;
}

static int a52_kmfr_open(struct inode *inode, struct file *file)
{
	return single_open(file, a52_kmfr_show, NULL);
}

static const struct file_operations a52_kmfr_fops = {
	.owner = THIS_MODULE,
	.open = a52_kmfr_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init a52_kmfr_init(void)
{
	if (!proc_create("a52_keymaster_flight_recorder", 0444, NULL,
			 &a52_kmfr_fops)) {
		pr_err("failed to create proc endpoint\n");
		return -ENOMEM;
	}

	a52_kmfr_record("RECORDER online capacity=%u mode=first-events",
		A52_KMFR_CAPACITY);
	return 0;
}
late_initcall(a52_kmfr_init);
'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_once(text: str, marker: str, addition: str) -> tuple[str, bool]:
    if marker in text:
        return text, False
    return text.rstrip() + "\n\n" + addition.rstrip() + "\n", True


def mask_c(text: str) -> str:
    """Mask comments and string/character literals while preserving indices."""
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
        elif state in {"string", "char"}:
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
    """Return signature start, opening brace, closing brace and signature."""
    masked = mask_c(text)
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\(")
    for match in pattern.finditer(masked):
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
        closing = -1
        for index in range(opening, len(masked)):
            char = masked[index]
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    closing = index
                    break
        if closing < 0:
            continue

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
        signature = text[signature_start : opening + 1]
        return signature_start, opening, closing, signature
    return None


def identifier(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def entry_expression(domain: str, name: str, signature: str) -> str:
    message = f"{domain} enter fn={name}"
    args: list[str] = []

    if name in {"qseecom_ioctl", "ion_ioctl"}:
        if re.search(r"\bcmd\b", signature) and re.search(r"\barg\b", signature):
            message += " cmd=0x%x arg=0x%lx"
            args.extend(["cmd", "arg"])
    elif name in {"ion_alloc", "ion_alloc_dmabuf", "ion_alloc_fd"}:
        if re.search(r"\blen\b", signature):
            message += " len=%zu"
            args.append("len")
        if re.search(r"\bheap_id_mask\b", signature):
            message += " heap_mask=0x%x"
            args.append("heap_id_mask")
        elif re.search(r"\bheap_mask\b", signature):
            message += " heap_mask=0x%x"
            args.append("heap_mask")
        if re.search(r"\bflags\b", signature):
            message += " flags=0x%x"
            args.append("flags")
    elif re.search(r"\bapp_name\b", signature):
        message += " app=%s"
        args.append('app_name ? app_name : "<null>"')

    call_args = ", " + ", ".join(args) if args else ""
    return f'a52_kmfr_record("{message}"{call_args})'


def inject_entry(text: str, domain: str, name: str) -> tuple[str, bool, str]:
    found = function_definition(text, name)
    if found is None:
        return text, False, "missing"
    _, opening, _, signature = found
    marker = f"{domain} enter fn={name}"
    if marker in text[opening : opening + 768]:
        return text, False, "already-present"

    variable = f"__a52_kmfr_entry_{identifier(name)}"
    expression = entry_expression(domain, name, signature)
    declaration = f"\n\tint {variable} __maybe_unused = ({expression}, 0);"
    return text[: opening + 1] + declaration + text[opening + 1 :], True, "inserted"


def traced_return(indent: str, domain: str, name: str, expression: str) -> str:
    """Return one compound statement so unbraced if/else control flow is preserved."""
    return (
        f"{indent}do {{\n"
        f'{indent}\ta52_kmfr_record("{domain} exit fn={name} ret=%ld", '
        f"(long)({expression}));\n"
        f"{indent}\treturn {expression};\n"
        f"{indent}}} while (0);"
    )


def inject_returns(text: str, domain: str, name: str) -> tuple[str, int]:
    found = function_definition(text, name)
    if found is None:
        return text, 0
    _, opening, closing, _ = found
    body = text[opening + 1 : closing]
    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)return[ \t]+"
        r"(?P<expr>ret|rc|retval|fd|0|-[A-Z][A-Z0-9_]*)[ \t]*;[ \t]*$"
    )
    matches = list(pattern.finditer(body))
    for match in reversed(matches):
        absolute = opening + 1 + match.start()
        replacement = traced_return(
            match.group("indent"), domain, name, match.group("expr")
        )
        text = text[:absolute] + replacement + text[opening + 1 + match.end() :]
    return text, len(matches)


def audit_safe_exit_blocks(text: str, domain: str) -> int:
    lines = text.splitlines()
    count = 0
    for index, line in enumerate(lines):
        if f'a52_kmfr_record("{domain} exit fn=' not in line:
            continue
        count += 1
        previous = lines[index - 1].strip() if index else ""
        if previous != "do {":
            raise SystemExit(
                f"unsafe {domain} exit instrumentation near line {index + 1}: "
                "trace is not inside a single do/while statement"
            )
    return count


def patch_qsee(root: Path) -> dict[str, object]:
    path = root / QSEE_REL
    text = read(path)
    if MARKER in text:
        return {"already_staged": True, "functions": []}

    include_anchor = '#include <soc/qcom/qtee_shmbridge.h>\n'
    include = '#include <linux/a52_keymaster_flight_recorder.h>\n'
    if include not in text:
        if include_anchor not in text:
            raise SystemExit("qseecom include anchor missing")
        text = text.replace(include_anchor, include_anchor + include, 1)
    text = text.replace(include, include + f"/* {MARKER} */\n", 1)

    names = (
        "qseecom_probe",
        "qseecom_open",
        "qseecom_release",
        "qseecom_ioctl",
        "qseecom_start_app",
        "qseecom_shutdown_app",
        "qseecom_load_app",
        "qseecom_unload_app",
        "__qseecom_unload_app",
        "qseecom_send_command",
        "qseecom_send_modfd_cmd",
        "__qseecom_send_modfd_cmd",
        "__qseecom_send_cmd",
    )
    staged: list[dict[str, object]] = []
    for name in names:
        text, _, state = inject_entry(text, "QSEE", name)
        exit_count = 0
        if state != "missing":
            text, exit_count = inject_returns(text, "QSEE", name)
        staged.append({"name": name, "entry": state, "exit_count": exit_count})

    required_groups = (
        {"qseecom_open"},
        {"qseecom_release"},
        {"qseecom_ioctl"},
        {"qseecom_start_app", "qseecom_load_app"},
        {"qseecom_send_command", "__qseecom_send_cmd"},
    )
    present = {item["name"] for item in staged if item["entry"] != "missing"}
    missing = [sorted(group) for group in required_groups if not group.intersection(present)]
    if missing:
        raise SystemExit("required QSEECOM groups missing: " + repr(missing))

    safe_exit_count = audit_safe_exit_blocks(text, "QSEE")
    write(path, text)
    return {
        "already_staged": False,
        "functions": staged,
        "safe_exit_count": safe_exit_count,
    }


def patch_ion(root: Path) -> dict[str, object]:
    core_path = root / ION_REL
    ioctl_path = root / ION_IOCTL_REL
    core = read(core_path)
    ioctl = read(ioctl_path)

    include = '#include <linux/a52_keymaster_flight_recorder.h>\n'
    core_anchor = '#include <soc/qcom/secure_buffer.h>\n'
    if include not in core:
        if core_anchor not in core:
            raise SystemExit("ION core include anchor missing")
        core = core.replace(core_anchor, core_anchor + include, 1)

    ioctl_anchor = '#include <linux/uaccess.h>\n'
    if include not in ioctl:
        if ioctl_anchor not in ioctl:
            raise SystemExit("ION ioctl include anchor missing")
        ioctl = ioctl.replace(ioctl_anchor, ioctl_anchor + include, 1)

    staged: list[dict[str, object]] = []
    for name, target in (
        ("ion_ioctl", "ioctl"),
        ("ion_alloc_dmabuf", "core"),
        ("ion_alloc", "core"),
        ("ion_alloc_fd", "core"),
        ("ion_buffer_create", "core"),
    ):
        source = ioctl if target == "ioctl" else core
        source, _, state = inject_entry(source, "ION", name)
        exit_count = 0
        if state != "missing":
            source, exit_count = inject_returns(source, "ION", name)
        if target == "ioctl":
            ioctl = source
        else:
            core = source
        staged.append({"name": name, "entry": state, "exit_count": exit_count})

    present = {item["name"] for item in staged if item["entry"] != "missing"}
    if "ion_ioctl" not in present:
        raise SystemExit("ion_ioctl trace anchor missing")
    if not {"ion_alloc_dmabuf", "ion_alloc", "ion_alloc_fd", "ion_buffer_create"}.intersection(present):
        raise SystemExit("ION allocation trace anchor missing")

    safe_exit_count = audit_safe_exit_blocks(core + "\n" + ioctl, "ION")
    write(core_path, core)
    write(ioctl_path, ioctl)
    return {
        "functions": staged,
        "ioctl_file": str(ION_IOCTL_REL),
        "core_file": str(ION_REL),
        "safe_exit_count": safe_exit_count,
    }


def stage_recorder(root: Path) -> dict[str, object]:
    header = root / HEADER_REL
    source = root / SOURCE_REL
    makefile = root / MISC_MAKEFILE_REL

    write(header, RECORDER_HEADER)
    write(source, RECORDER_SOURCE)

    make_text = read(makefile)
    addition = f"# {MARKER}\nobj-y += a52_keymaster_flight_recorder.o"
    make_text, changed = append_once(make_text, MARKER, addition)
    write(makefile, make_text)

    return {
        "header": str(HEADER_REL),
        "source": str(SOURCE_REL),
        "makefile_changed": changed,
        "capacity": 8192,
        "capture_mode": "first-events-preserved",
        "proc_path": "/proc/a52_keymaster_flight_recorder",
    }


def self_test() -> None:
    sample = """static int demo(int value)\n{\n\tif (value)\n\t\treturn -EINVAL;\n\treturn 0;\n}\n"""
    patched, count = inject_returns(sample, "TEST", "demo")
    if count != 2:
        raise SystemExit("return rewriter self-test did not trace both returns")
    if "if (value)\n\t\tdo {" not in patched:
        raise SystemExit("return rewriter self-test broke unbraced if control flow")
    if audit_safe_exit_blocks(patched, "TEST") != 2:
        raise SystemExit("return rewriter self-test audit failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    self_test()

    root = args.kernel.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    required = [QSEE_REL, ION_REL, ION_IOCTL_REL, MISC_MAKEFILE_REL]
    missing = [str(path) for path in required if not (root / path).is_file()]
    if missing:
        raise SystemExit("missing TouchGrass source files: " + ", ".join(missing))

    report = {
        "status": "touchgrass-keymaster-flight-recorder-staged",
        "hardware_validated": False,
        "marker": MARKER,
        "control_flow_safe_return_wrapping": True,
        "recorder": stage_recorder(root),
        "qseecom": patch_qsee(root),
        "ion": patch_ion(root),
        "markers": ["A52KMFR", "QSEE enter", "ION enter"],
        "scope": (
            "first 8192 selected secure-startup events preserved in an in-kernel "
            "recorder plus a rate-limited printk mirror"
        ),
    }
    (output / "touchgrass-keymaster-flight-recorder-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
