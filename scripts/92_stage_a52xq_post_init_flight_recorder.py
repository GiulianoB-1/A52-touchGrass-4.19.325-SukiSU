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


def triplet(message: str, indent: str = "\t") -> str:
    return "".join(
        f'{indent}a52_persistent_diag_mark("A52POST copy={copy} {message}\\n");\n'
        for copy in (1, 2, 3)
    )


def mark_statement(
    text: str,
    statement: str,
    label: str,
    *,
    before: bool = True,
    after: bool = True,
    indent: str = "\t",
) -> str:
    replacement = ""
    if before:
        replacement += triplet(f"before {label}", indent)
    replacement += statement
    if after:
        replacement += triplet(f"after {label}", indent)
    return replace_once(text, statement, replacement, f"instrument {label}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extend the A52 persistent boot trace beyond do_initcalls through "
            "rootfs setup, init-memory finalization, userspace exec and panic."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    main_path = gki / "init/main.c"
    panic_path = gki / "kernel/panic.c"
    if not main_path.is_file() or not panic_path.is_file():
        raise SystemExit("expected init/main.c and kernel/panic.c")

    text = main_path.read_text(encoding="utf-8")
    panic = panic_path.read_text(encoding="utf-8")

    # Use individual statements rather than one large formatting-sensitive block.
    text = mark_statement(text, "\tdo_basic_setup();\n", "do_basic_setup")
    text = mark_statement(text, "\tkunit_run_all_tests();\n", "kunit_run_all_tests")
    text = mark_statement(text, "\tconsole_on_rootfs();\n", "console_on_rootfs")

    # Trace the initramfs/root-device decision without rewriting the conditional.
    init_access = "\tif (init_eaccess(ramdisk_execute_command) != 0) {\n"
    text = replace_once(
        text,
        init_access,
        triplet("before init_eaccess") + init_access,
        "instrument init_eaccess entry",
    )
    text = replace_once(
        text,
        "\t\tramdisk_execute_command = NULL;\n",
        triplet("init_eaccess failed", "\t\t")
        + "\t\tramdisk_execute_command = NULL;\n",
        "instrument init_eaccess failure",
    )
    text = mark_statement(
        text,
        "\t\tprepare_namespace();\n",
        "prepare_namespace",
        indent="\t\t",
    )
    text = mark_statement(text, "\tintegrity_load_keys();\n", "integrity_load_keys")

    # Trace kernel_init() finalization statement by statement. This is resilient to
    # comments and blank-line differences between Android 5.10 revisions.
    for statement, label in (
        ("\tkernel_init_freeable();\n", "kernel_init_freeable"),
        ("\tasync_synchronize_full();\n", "async_synchronize_full"),
        ("\tkprobe_free_init_mem();\n", "kprobe_free_init_mem"),
        ("\tftrace_free_init_mem();\n", "ftrace_free_init_mem"),
        ("\tkgdb_free_init_mem();\n", "kgdb_free_init_mem"),
        ("\tfree_initmem();\n", "free_initmem"),
        ("\tmark_readonly();\n", "mark_readonly"),
        ("\tpti_finalize();\n", "pti_finalize"),
        ("\trcu_end_inkernel_boot();\n", "rcu_end_inkernel_boot"),
        ("\tdo_sysctl_args();\n", "do_sysctl_args"),
    ):
        text = mark_statement(text, statement, label)

    # numa_default_policy() appears in more than one function in this kernel.
    # Instrument only the instance paired with the SYSTEM_RUNNING transition.
    running_block = (
        "\tsystem_state = SYSTEM_RUNNING;\n"
        "\tnuma_default_policy();\n"
    )
    running_replacement = (
        triplet("before system_state_running")
        + "\tsystem_state = SYSTEM_RUNNING;\n"
        + triplet("after system_state_running")
        + triplet("before numa_default_policy")
        + "\tnuma_default_policy();\n"
        + triplet("after numa_default_policy")
    )
    text = replace_once(
        text,
        running_block,
        running_replacement,
        "instrument SYSTEM_RUNNING and NUMA policy transition",
    )

    # Capture every userspace executable attempt and the exact return value when
    # exec fails and control returns to the kernel.
    function_head = (
        "static int run_init_process(const char *init_filename)\n"
        "{\n"
        "\tconst char *const *p;\n\n"
        "\targv_init[0] = init_filename;\n"
    )
    function_replacement = (
        "static int run_init_process(const char *init_filename)\n"
        "{\n"
        "\tconst char *const *p;\n"
        "\tint a52_exec_ret;\n\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=1 exec begin path=%s\\n\", init_filename);\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=2 exec begin path=%s\\n\", init_filename);\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=3 exec begin path=%s\\n\", init_filename);\n"
        "\targv_init[0] = init_filename;\n"
    )
    text = replace_once(
        text,
        function_head,
        function_replacement,
        "instrument run_init_process entry",
    )

    exec_return = "\treturn kernel_execve(init_filename, argv_init, envp_init);\n}\n"
    exec_replacement = (
        "\ta52_exec_ret = kernel_execve(init_filename, argv_init, envp_init);\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=1 exec returned path=%s ret=%d\\n\", init_filename, a52_exec_ret);\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=2 exec returned path=%s ret=%d\\n\", init_filename, a52_exec_ret);\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=3 exec returned path=%s ret=%d\\n\", init_filename, a52_exec_ret);\n"
        "\treturn a52_exec_ret;\n"
        "}\n"
    )
    text = replace_once(
        text,
        exec_return,
        exec_replacement,
        "instrument kernel_execve return",
    )

    # Panic trace. Keep all local declarations at the beginning of panic() to
    # satisfy the kernel's C90 declaration-order warning policy.
    declaration = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    if declaration not in panic:
        panic = replace_once(
            panic,
            "#include <linux/debug_locks.h>\n",
            "#include <linux/debug_locks.h>\n" + declaration,
            "declare persistent helper in panic.c",
        )

    panic_marker_anchor = (
        "\tbool _crash_kexec_post_notifiers = crash_kexec_post_notifiers;\n\n"
    )
    panic = replace_once(
        panic,
        panic_marker_anchor,
        panic_marker_anchor + triplet("panic entered"),
        "instrument panic after local declarations",
    )

    checks = {
        "post_basic_setup": "A52POST copy=1 after do_basic_setup" in text,
        "console_rootfs": "A52POST copy=1 after console_on_rootfs" in text,
        "namespace_branch": (
            "A52POST copy=1 before init_eaccess" in text
            and "A52POST copy=1 before prepare_namespace" in text
        ),
        "kernel_init_freeable_boundary": (
            "A52POST copy=1 before kernel_init_freeable" in text
            and "A52POST copy=1 after kernel_init_freeable" in text
        ),
        "initmem_boundaries": (
            "A52POST copy=1 before free_initmem" in text
            and "A52POST copy=1 after mark_readonly" in text
        ),
        "running_transition": (
            "A52POST copy=1 after system_state_running" in text
            and "A52POST copy=1 after numa_default_policy" in text
        ),
        "userspace_exec": (
            "A52POST copy=1 exec begin path=%s" in text
            and "A52POST copy=1 exec returned path=%s ret=%d" in text
        ),
        "panic_helper_declared": declaration in panic,
        "panic_triplet": panic.count("panic entered") == 3,
        "triple_redundancy": (
            text.count("A52POST copy=1")
            == text.count("A52POST copy=2")
            == text.count("A52POST copy=3")
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit(
            "post-init flight recorder staging audit failed: " + ", ".join(failed)
        )

    main_path.write_text(text, encoding="utf-8")
    panic_path.write_text(panic, encoding="utf-8")
    (output / "patched-init-main.c").write_text(text, encoding="utf-8")
    (output / "patched-kernel-panic.c").write_text(panic, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "trace_scope": "post-do_initcalls through userspace exec and panic",
                "redundancy": 3,
                "staging_strategy": "statement-by-statement resilient anchors",
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
