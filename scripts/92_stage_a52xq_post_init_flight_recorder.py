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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extend the A52 persistent boot trace beyond do_initcalls through "
            "rootfs setup, init-memory finalization, and userspace exec."
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

    # do_basic_setup() is already fully traced by Workflow 83. Add the first
    # marker immediately after it and continue through the remaining
    # kernel_init_freeable() stages.
    old = "\tdo_basic_setup();\n\n\tkunit_run_all_tests();\n\n\tconsole_on_rootfs();\n"
    new = (
        "\tdo_basic_setup();\n"
        + triplet("after do_basic_setup")
        + "\n"
        + triplet("before kunit_run_all_tests")
        + "\tkunit_run_all_tests();\n"
        + triplet("after kunit_run_all_tests")
        + "\n"
        + triplet("before console_on_rootfs")
        + "\tconsole_on_rootfs();\n"
        + triplet("after console_on_rootfs")
    )
    text = replace_once(text, old, new, "instrument post-do_basic_setup stages")

    old = (
        "\tif (init_eaccess(ramdisk_execute_command) != 0) {\n"
        "\t\tramdisk_execute_command = NULL;\n"
        "\t\tprepare_namespace();\n"
        "\t}\n"
    )
    new = (
        triplet("before init_eaccess")
        + "\tif (init_eaccess(ramdisk_execute_command) != 0) {\n"
        + triplet("init_eaccess failed", "\t\t")
        + "\t\tramdisk_execute_command = NULL;\n"
        + triplet("before prepare_namespace", "\t\t")
        + "\t\tprepare_namespace();\n"
        + triplet("after prepare_namespace", "\t\t")
        + "\t} else {\n"
        + triplet("init_eaccess succeeded", "\t\t")
        + "\t}\n"
        + triplet("after init_eaccess branch")
    )
    text = replace_once(text, old, new, "instrument initramfs and namespace decision")

    old = "\tintegrity_load_keys();\n}\n\nstatic int __ref kernel_init(void *unused)\n"
    new = (
        triplet("before integrity_load_keys")
        + "\tintegrity_load_keys();\n"
        + triplet("after integrity_load_keys")
        + "}\n\nstatic int __ref kernel_init(void *unused)\n"
    )
    text = replace_once(text, old, new, "instrument integrity key loading")

    old = (
        "\tkernel_init_freeable();\n"
        "\t/* need to finish all async __init code before freeing the memory */\n"
        "\tasync_synchronize_full();\n"
        "\tkprobe_free_init_mem();\n"
        "\tftrace_free_init_mem();\n"
        "\tkgdb_free_init_mem();\n"
        "\tfree_initmem();\n"
        "\tmark_readonly();\n"
    )
    new = (
        triplet("before kernel_init_freeable")
        + "\tkernel_init_freeable();\n"
        + triplet("after kernel_init_freeable")
        + "\t/* need to finish all async __init code before freeing the memory */\n"
        + triplet("before async_synchronize_full")
        + "\tasync_synchronize_full();\n"
        + triplet("after async_synchronize_full")
        + triplet("before kprobe_free_init_mem")
        + "\tkprobe_free_init_mem();\n"
        + triplet("after kprobe_free_init_mem")
        + triplet("before ftrace_free_init_mem")
        + "\tftrace_free_init_mem();\n"
        + triplet("after ftrace_free_init_mem")
        + triplet("before kgdb_free_init_mem")
        + "\tkgdb_free_init_mem();\n"
        + triplet("after kgdb_free_init_mem")
        + triplet("before free_initmem")
        + "\tfree_initmem();\n"
        + triplet("after free_initmem")
        + triplet("before mark_readonly")
        + "\tmark_readonly();\n"
        + triplet("after mark_readonly")
    )
    text = replace_once(text, old, new, "instrument init-memory finalization")

    old = (
        "\tpti_finalize();\n\n"
        "\tsystem_state = SYSTEM_RUNNING;\n"
        "\tnuma_default_policy();\n\n"
        "\trcu_end_inkernel_boot();\n\n"
        "\tdo_sysctl_args();\n"
    )
    new = (
        triplet("before pti_finalize")
        + "\tpti_finalize();\n"
        + triplet("after pti_finalize")
        + "\n"
        + triplet("before system_state_running")
        + "\tsystem_state = SYSTEM_RUNNING;\n"
        + triplet("after system_state_running")
        + "\tnuma_default_policy();\n"
        + triplet("after numa_default_policy")
        + "\n"
        + "\trcu_end_inkernel_boot();\n"
        + triplet("after rcu_end_inkernel_boot")
        + "\n"
        + triplet("before do_sysctl_args")
        + "\tdo_sysctl_args();\n"
        + triplet("after do_sysctl_args")
    )
    text = replace_once(text, old, new, "instrument final kernel transition")

    # Capture every attempted userspace executable centrally. Preserve the
    # existing argument/environment debug output and report kernel_execve's
    # return value if control comes back to the kernel.
    old = (
        "static int run_init_process(const char *init_filename)\n"
        "{\n"
        "\tconst char *const *p;\n\n"
        "\targv_init[0] = init_filename;\n"
    )
    new = (
        "static int run_init_process(const char *init_filename)\n"
        "{\n"
        "\tconst char *const *p;\n"
        "\tint a52_exec_ret;\n\n"
        + "\ta52_persistent_diag_mark(\"A52POST copy=1 exec begin path=%s\\n\", init_filename);\n"
        + "\ta52_persistent_diag_mark(\"A52POST copy=2 exec begin path=%s\\n\", init_filename);\n"
        + "\ta52_persistent_diag_mark(\"A52POST copy=3 exec begin path=%s\\n\", init_filename);\n"
        + "\targv_init[0] = init_filename;\n"
    )
    text = replace_once(text, old, new, "declare userspace exec result and entry markers")

    old = "\treturn kernel_execve(init_filename, argv_init, envp_init);\n}\n"
    new = (
        "\ta52_exec_ret = kernel_execve(init_filename, argv_init, envp_init);\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=1 exec returned path=%s ret=%d\\n\", init_filename, a52_exec_ret);\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=2 exec returned path=%s ret=%d\\n\", init_filename, a52_exec_ret);\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=3 exec returned path=%s ret=%d\\n\", init_filename, a52_exec_ret);\n"
        "\treturn a52_exec_ret;\n"
        "}\n"
    )
    text = replace_once(text, old, new, "instrument kernel_execve return")

    # A fixed panic breadcrumb is intentionally format-independent and safe even
    # when panic formatting itself is what failed. The persistent helper is a
    # no-op before its ring has been initialized.
    include_anchor = "#include <linux/panic_notifier.h>\n"
    if include_anchor in panic:
        panic = replace_once(
            panic,
            include_anchor,
            include_anchor + "extern void a52_persistent_diag_mark(const char *fmt, ...);\n",
            "declare persistent helper in panic.c",
        )
    else:
        first_include = "#include <linux/kernel.h>\n"
        panic = replace_once(
            panic,
            first_include,
            first_include + "extern void a52_persistent_diag_mark(const char *fmt, ...);\n",
            "declare persistent helper in panic.c fallback",
        )

    panic_anchor = "void panic(const char *fmt, ...)\n{\n"
    panic_replacement = (
        "void panic(const char *fmt, ...)\n"
        "{\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=1 panic entered\\n\");\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=2 panic entered\\n\");\n"
        "\ta52_persistent_diag_mark(\"A52POST copy=3 panic entered\\n\");\n"
    )
    panic = replace_once(panic, panic_anchor, panic_replacement, "instrument panic entry")

    checks = {
        "post_basic_setup": "A52POST copy=1 after do_basic_setup" in text,
        "console_rootfs": "A52POST copy=1 after console_on_rootfs" in text,
        "namespace_branch": (
            "A52POST copy=1 before prepare_namespace" in text
            and "A52POST copy=1 init_eaccess succeeded" in text
        ),
        "kernel_init_freeable_boundary": (
            "A52POST copy=1 before kernel_init_freeable" in text
            and "A52POST copy=1 after kernel_init_freeable" in text
        ),
        "initmem_boundaries": (
            "A52POST copy=1 before free_initmem" in text
            and "A52POST copy=1 after mark_readonly" in text
        ),
        "userspace_exec": (
            "A52POST copy=1 exec begin path=%s" in text
            and "A52POST copy=1 exec returned path=%s ret=%d" in text
        ),
        "panic_triplet": panic.count("panic entered") == 3,
        "triple_redundancy": text.count("A52POST copy=1") == text.count("A52POST copy=2") == text.count("A52POST copy=3"),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("post-init flight recorder staging audit failed: " + ", ".join(failed))

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
