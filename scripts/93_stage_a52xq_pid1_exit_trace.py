#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def triplet(fmt: str, args: str, indent: str, prefix: str = "A52EXIT") -> str:
    return "".join(
        f'{indent}a52_persistent_diag_mark("{prefix} copy={copy} {fmt}\\n", {args});\n'
        for copy in (1, 2, 3)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Trace Android PID 1 exit status and mirror its warning/error /dev/kmsg "
            "messages into the persistent A52 ramoops console."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    exit_path = gki / "kernel/exit.c"
    printk_path = gki / "kernel/printk/printk.c"
    if not exit_path.is_file() or not printk_path.is_file():
        raise SystemExit("kernel/exit.c or kernel/printk/printk.c is missing")

    text = exit_path.read_text(encoding="utf-8")
    printk = printk_path.read_text(encoding="utf-8")
    declaration = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"

    if declaration not in text:
        anchors = (
            "#include <linux/mm.h>\n",
            "#include <linux/ptrace.h>\n",
            "#include <linux/sched/task.h>\n",
        )
        for anchor in anchors:
            if anchor in text:
                text = replace_once(
                    text,
                    anchor,
                    anchor + declaration,
                    "declare persistent diagnostic helper in exit.c",
                )
                break
        else:
            raise SystemExit("no verified include anchor found in kernel/exit.c")

    # Record PID 1 before profile_task_exit(). This statement occurs after all
    # do_exit() local declarations in Android 5.10.
    entry_anchor = "\tprofile_task_exit(tsk);\n"
    entry_markers = triplet(
        "ENTRY pid=%d tgid=%d comm=%s code=0x%08lx group=0x%08x flags=0x%lx",
        "task_pid_nr(tsk), task_tgid_nr(tsk), tsk->comm, code, "
        "(unsigned int)tsk->signal->group_exit_code, tsk->flags",
        "\t\t",
    )
    entry_replacement = (
        "\tif (unlikely(is_global_init(tsk))) {\n"
        + entry_markers
        + "\t}\n\n"
        + entry_anchor
    )
    text = replace_once(
        text,
        entry_anchor,
        entry_replacement,
        "instrument PID 1 do_exit entry",
    )

    # Capture explicit exit_group() and fatal handling before group state changes.
    group_anchor = "\tBUG_ON(exit_code & 0x80); /* core dumps don't get here */\n"
    group_markers = triplet(
        "GROUP pid=%d tgid=%d comm=%s requested=0x%08x existing=0x%08x sigflags=0x%x",
        "task_pid_nr(current), task_tgid_nr(current), current->comm, "
        "(unsigned int)exit_code, "
        "(unsigned int)current->signal->group_exit_code, "
        "(unsigned int)current->signal->flags",
        "\t\t",
    )
    group_replacement = (
        "\tif (unlikely(is_global_init(current))) {\n"
        + group_markers
        + "\t}\n\n"
        + group_anchor
    )
    text = replace_once(
        text,
        group_anchor,
        group_replacement,
        "instrument PID 1 do_group_exit",
    )

    # Decode the final Linux wait status immediately before the global-init panic.
    panic_pattern = re.compile(
        r"(?P<ifindent>\t\t)if \(unlikely\(is_global_init\(tsk\)\)\)\n"
        r"(?P<panicindent>\t\t\t)panic\(\"Attempted to kill init! ?exitcode=0x%08x\\n\",\n"
        r"(?P<argindent>\t\t\t\t)tsk->signal->group_exit_code \?: \(int\)code\);\n"
    )
    matches = list(panic_pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(
            "instrument PID 1 final exit: expected one supported panic block, "
            f"found {len(matches)}"
        )

    effective = "(tsk->signal->group_exit_code ?: (int)code)"
    final_markers = triplet(
        "FINAL pid=%d tgid=%d comm=%s code=0x%08lx group=0x%08x effective=0x%08x status=%u signal=%u core=%u",
        "task_pid_nr(tsk), task_tgid_nr(tsk), tsk->comm, code, "
        "(unsigned int)tsk->signal->group_exit_code, "
        f"(unsigned int){effective}, "
        f"(unsigned int)(({effective} >> 8) & 0xff), "
        f"(unsigned int)({effective} & 0x7f), "
        f"(unsigned int)!!({effective} & 0x80)",
        "\t\t\t",
    )
    final_replacement = (
        "\t\tif (unlikely(is_global_init(tsk))) {\n"
        + final_markers
        + "\t\t\tpanic(\"Attempted to kill init! exitcode=0x%08x\\n\",\n"
        + "\t\t\t\ttsk->signal->group_exit_code ?: (int)code);\n"
        + "\t\t}\n"
    )
    text, substitutions = panic_pattern.subn(
        lambda _match: final_replacement,
        text,
        count=1,
    )
    if substitutions != 1:
        raise SystemExit(
            f"instrument PID 1 final exit: expected one substitution, got {substitutions}"
        )

    # Mirror PID 1 warning/error messages written to /dev/kmsg. Android first-stage
    # init directs its logging there, including LOG(FATAL) explanations. The cap
    # prevents excessive INFO traffic or a loop from evicting the final exit record.
    if declaration not in printk:
        printk = replace_once(
            printk,
            "#include <linux/kernel.h>\n",
            "#include <linux/kernel.h>\n" + declaration,
            "declare persistent diagnostic helper in printk.c",
        )

    counter_anchor = (
        "atomic_t ignore_console_lock_warning __read_mostly = ATOMIC_INIT(0);\n"
    )
    counter_declaration = "static unsigned int a52_pid1_kmsg_count;\n\n"
    if counter_declaration not in printk:
        printk = replace_once(
            printk,
            counter_anchor,
            counter_declaration + counter_anchor,
            "declare PID 1 kmsg mirror counter",
        )

    emit_anchor = "\tdevkmsg_emit(facility, level, \"%s\", line);\n"
    kmsg_markers = triplet(
        "seq=%u pid=%d tgid=%d comm=%s level=%d facility=%d msg=%s",
        "a52_pid1_kmsg_count, current->pid, current->tgid, current->comm, "
        "level, facility, line",
        "\t\t",
        prefix="A52KMSG",
    )
    emit_replacement = (
        "\t/* Android init uses /dev/kmsg for first-stage fatal diagnostics. */\n"
        "\tif (unlikely(current->tgid == 1 && level <= 4 &&\n"
        "\t\t     a52_pid1_kmsg_count < 32)) {\n"
        "\t\ta52_pid1_kmsg_count++;\n"
        + kmsg_markers
        + "\t}\n\n"
        + emit_anchor
    )
    printk = replace_once(
        printk,
        emit_anchor,
        emit_replacement,
        "mirror PID 1 /dev/kmsg diagnostics",
    )

    checks = {
        "exit_helper_declared": declaration in text,
        "entry_triplet": all(text.count(f"A52EXIT copy={copy} ENTRY") == 1 for copy in (1, 2, 3)),
        "group_triplet": all(text.count(f"A52EXIT copy={copy} GROUP") == 1 for copy in (1, 2, 3)),
        "final_triplet": all(text.count(f"A52EXIT copy={copy} FINAL") == 1 for copy in (1, 2, 3)),
        "original_pid1_panic_preserved": "Attempted to kill init! exitcode=0x%08x" in text,
        "wait_status_fields": "status=%u signal=%u core=%u" in text,
        "literal_newlines_preserved": 'core=%u\\n",' in text
        and 'kill init! exitcode=0x%08x\\n",' in text,
        "printk_helper_declared": declaration in printk,
        "kmsg_counter": counter_declaration in printk,
        "kmsg_triplet": all(printk.count(f"A52KMSG copy={copy}") == 1 for copy in (1, 2, 3)),
        "kmsg_pid1_filter": "current->tgid == 1 && level <= 4" in printk,
        "kmsg_cap": "a52_pid1_kmsg_count < 32" in printk,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("PID 1 exit/kmsg staging audit failed: " + ", ".join(failed))

    exit_path.write_text(text, encoding="utf-8")
    printk_path.write_text(printk, encoding="utf-8")
    (output / "patched-kernel-exit.c").write_text(text, encoding="utf-8")
    (output / "patched-kernel-printk.c").write_text(printk, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "purpose": "decode PID 1 exit 127 and preserve its fatal /dev/kmsg explanation",
                "trace_points": [
                    "PID 1 /dev/kmsg warnings and errors",
                    "do_exit before profile_task_exit",
                    "do_group_exit before state mutation",
                    "pre-panic final status",
                ],
                "kmsg_level_max": 4,
                "kmsg_message_cap": 32,
                "redundancy": 3,
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
