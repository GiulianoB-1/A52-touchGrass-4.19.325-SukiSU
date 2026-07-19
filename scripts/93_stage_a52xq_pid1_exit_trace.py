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


def triplet(fmt: str, args: str, indent: str) -> str:
    return "".join(
        f'{indent}a52_persistent_diag_mark("A52EXIT copy={copy} {fmt}\\n", {args});\n'
        for copy in (1, 2, 3)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Trace the exact exit status or fatal signal that kills Android PID 1 "
            "after /init is successfully executed."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    path = gki / "kernel/exit.c"
    if not path.is_file():
        raise SystemExit("kernel/exit.c is missing")
    text = path.read_text(encoding="utf-8")

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

    # Record the first entry into do_exit() for global PID 1 before exit_signals()
    # mutates the task state. No new local declarations are introduced.
    entry_anchor = "\tint group_dead;\n\n\tprofile_task_exit(tsk);\n"
    entry_markers = triplet(
        "ENTRY pid=%d tgid=%d comm=%s code=0x%08lx group=0x%08x flags=0x%lx",
        "task_pid_nr(tsk), task_tgid_nr(tsk), tsk->comm, code, "
        "tsk->signal->group_exit_code, tsk->flags",
        "\t",
    )
    entry_replacement = (
        "\tint group_dead;\n\n"
        "\tif (unlikely(is_global_init(tsk))) {\n"
        + entry_markers.replace("\t", "\t\t", 3)
        + "\t}\n\n"
        "\tprofile_task_exit(tsk);\n"
    )
    text = replace_once(
        text,
        entry_anchor,
        entry_replacement,
        "instrument PID 1 do_exit entry",
    )

    # Capture explicit exit_group() and fatal-signal paths before do_exit().
    group_anchor = (
        "void\n"
        "do_group_exit(int exit_code)\n"
        "{\n"
        "\tstruct signal_struct *sig = current->signal;\n\n"
        "\tBUG_ON(exit_code & 0x80); /* core dumps don't get here */\n"
    )
    group_markers = triplet(
        "GROUP pid=%d tgid=%d comm=%s requested=0x%08x existing=0x%08x sigflags=0x%x",
        "task_pid_nr(current), task_tgid_nr(current), current->comm, exit_code, "
        "sig->group_exit_code, sig->flags",
        "\t\t",
    )
    group_replacement = (
        "void\n"
        "do_group_exit(int exit_code)\n"
        "{\n"
        "\tstruct signal_struct *sig = current->signal;\n\n"
        "\tif (unlikely(is_global_init(current))) {\n"
        + group_markers
        + "\t}\n\n"
        "\tBUG_ON(exit_code & 0x80); /* core dumps don't get here */\n"
    )
    text = replace_once(
        text,
        group_anchor,
        group_replacement,
        "instrument PID 1 do_group_exit",
    )

    # The Workflow 92 capture reached panic() immediately after successful /init
    # exec. Record Linux wait-status decoding immediately before that panic.
    panic_variants = (
        (
            "\t\tif (unlikely(is_global_init(tsk)))\n"
            "\t\t\tpanic(\"Attempted to kill init! exitcode=0x%08x\\n\",\n"
            "\t\t\t\ttsk->signal->group_exit_code ?: (int)code);\n"
        ),
        (
            "\t\tif (unlikely(is_global_init(tsk)))\n"
            "\t\t\tpanic(\"Attempted to kill init!exitcode=0x%08x\\n\",\n"
            "\t\t\t\ttsk->signal->group_exit_code ?: (int)code);\n"
        ),
    )
    matches = [variant for variant in panic_variants if variant in text]
    if len(matches) != 1:
        raise SystemExit(
            f"instrument PID 1 final exit: expected one supported panic block, found {len(matches)}"
        )
    panic_anchor = matches[0]
    effective = "(tsk->signal->group_exit_code ?: (int)code)"
    final_markers = triplet(
        "FINAL pid=%d tgid=%d comm=%s code=0x%08lx group=0x%08x effective=0x%08x status=%u signal=%u core=%u",
        "task_pid_nr(tsk), task_tgid_nr(tsk), tsk->comm, code, "
        "tsk->signal->group_exit_code, "
        f"{effective}, ({effective} >> 8) & 0xff, {effective} & 0x7f, "
        f"!!({effective} & 0x80)",
        "\t\t\t",
    )
    final_replacement = (
        "\t\tif (unlikely(is_global_init(tsk))) {\n"
        + final_markers
        + "\t\t\tpanic(\"Attempted to kill init! exitcode=0x%08x\\n\",\n"
        "\t\t\t\ttsk->signal->group_exit_code ?: (int)code);\n"
        "\t\t}\n"
    )
    text = replace_once(
        text,
        panic_anchor,
        final_replacement,
        "instrument PID 1 final exit status",
    )

    checks = {
        "helper_declared": declaration in text,
        "entry_triplet": text.count("A52EXIT copy=1 ENTRY") == 1
        and text.count("A52EXIT copy=2 ENTRY") == 1
        and text.count("A52EXIT copy=3 ENTRY") == 1,
        "group_triplet": text.count("A52EXIT copy=1 GROUP") == 1
        and text.count("A52EXIT copy=2 GROUP") == 1
        and text.count("A52EXIT copy=3 GROUP") == 1,
        "final_triplet": text.count("A52EXIT copy=1 FINAL") == 1
        and text.count("A52EXIT copy=2 FINAL") == 1
        and text.count("A52EXIT copy=3 FINAL") == 1,
        "original_pid1_panic_preserved": "Attempted to kill init! exitcode=0x%08x" in text,
        "wait_status_fields": "status=%u signal=%u core=%u" in text,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("PID 1 exit trace staging audit failed: " + ", ".join(failed))

    path.write_text(text, encoding="utf-8")
    (output / "patched-kernel-exit.c").write_text(text, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "purpose": "decode Android PID 1 exit status or fatal signal",
                "trace_points": ["do_exit entry", "do_group_exit entry", "pre-panic final status"],
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
