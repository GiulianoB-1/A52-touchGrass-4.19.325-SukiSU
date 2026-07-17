#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: 55_apply_susfs_resukisu_compat.py KERNEL_DIR RESUKISU_DIR COMPAT_C"
        )

    kernel = Path(sys.argv[1])
    resukisu = Path(sys.argv[2])
    compat_source = Path(sys.argv[3])

    if not compat_source.is_file():
        raise SystemExit(f"compatibility source missing: {compat_source}")

    compat_target = kernel / "fs/susfs_resukisu_compat.c"
    shutil.copyfile(compat_source, compat_target)

    makefile = kernel / "fs/Makefile"
    text = makefile.read_text()
    anchor = "obj-$(CONFIG_KSU_SUSFS) += susfs.o\n"
    compat_line = "obj-$(CONFIG_KSU_SUSFS) += susfs_resukisu_compat.o\n"
    if compat_line not in text:
        if text.count(anchor) != 1:
            raise SystemExit("SUSFS fs/Makefile anchor mismatch")
        makefile.write_text(text.replace(anchor, anchor + compat_line, 1))

    susfs_def = kernel / "include/linux/susfs_def.h"
    text = susfs_def.read_text()
    final_guard = "#endif // #ifndef KSU_SUSFS_DEF_H\n"
    command_compat = """
#ifndef SUSFS_MAGIC
#define SUSFS_MAGIC 0xFAFAFAFA
#endif
#ifndef CMD_SUSFS_HIDE_SUS_MNTS_FOR_NON_SU_PROCS
#define CMD_SUSFS_HIDE_SUS_MNTS_FOR_NON_SU_PROCS 0x55561
#endif
#ifndef CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING
#define CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING 0x60010
#endif

"""
    if "SUSFS_MAGIC" not in text:
        if text.count(final_guard) != 1:
            raise SystemExit("SUSFS definition final guard mismatch")
        susfs_def.write_text(text.replace(final_guard, command_compat + final_guard, 1))

    proc_namespace = kernel / "fs/proc_namespace.c"
    replace_once(
        proc_namespace,
        "bool susfs_hide_sus_mnts_for_all_procs = true;",
        "bool susfs_hide_sus_mnts_for_all_procs = false;",
        "safe SUSFS mount visibility default",
    )

    fdinfo = kernel / "fs/notify/fdinfo.c"
    replace_once(
        fdinfo,
        "out_seq_printf:\n#endif",
        "out_seq_printf:\n\t;\n#endif",
        "SUSFS fdinfo label statement",
    )

    dispatch = resukisu / "kernel/supercall/dispatch.c"
    text = dispatch.read_text()
    include_anchor = "#include <linux/susfs_def.h>\n"
    declarations = """#include <linux/susfs_def.h>
void susfs_compat_set_hide_sus_mnts_for_non_su_procs(void __user **user_info);
void susfs_compat_add_sus_kstat(void __user **user_info, bool statically);
void susfs_compat_update_sus_kstat(void __user **user_info);
void susfs_compat_set_uname(void __user **user_info);
void susfs_compat_set_avc_log_spoofing(void __user **user_info);
void susfs_compat_get_enabled_features(void __user **user_info);
void susfs_compat_show_variant(void __user **user_info);
void susfs_compat_show_version(void __user **user_info);
"""
    if "susfs_compat_show_version" not in text:
        if text.count(include_anchor) != 1:
            raise SystemExit("ReSukiSU SUSFS include anchor mismatch")
        text = text.replace(include_anchor, declarations, 1)

    replacements = [
        (
            "        susfs_set_hide_sus_mnts_for_non_su_procs(arg);",
            "        susfs_compat_set_hide_sus_mnts_for_non_su_procs(arg);",
            "mount policy command",
        ),
        (
            "    case CMD_SUSFS_ADD_SUS_KSTAT: {\n        susfs_add_sus_kstat(arg);",
            "    case CMD_SUSFS_ADD_SUS_KSTAT: {\n        susfs_compat_add_sus_kstat(arg, false);",
            "add kstat command",
        ),
        (
            "        susfs_update_sus_kstat(arg);",
            "        susfs_compat_update_sus_kstat(arg);",
            "update kstat command",
        ),
        (
            "    case CMD_SUSFS_ADD_SUS_KSTAT_STATICALLY: {\n        susfs_add_sus_kstat(arg);",
            "    case CMD_SUSFS_ADD_SUS_KSTAT_STATICALLY: {\n        susfs_compat_add_sus_kstat(arg, true);",
            "static kstat command",
        ),
        (
            "        susfs_set_uname(arg);",
            "        susfs_compat_set_uname(arg);",
            "uname command",
        ),
        (
            "        susfs_set_avc_log_spoofing(arg);",
            "        susfs_compat_set_avc_log_spoofing(arg);",
            "AVC policy command",
        ),
        (
            "        susfs_get_enabled_features(arg);",
            "        susfs_compat_get_enabled_features(arg);",
            "feature query command",
        ),
        (
            "        susfs_show_variant(arg);",
            "        susfs_compat_show_variant(arg);",
            "variant query command",
        ),
        (
            "        susfs_show_version(arg);",
            "        susfs_compat_show_version(arg);",
            "version query command",
        ),
    ]
    for old, new, label in replacements:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"ReSukiSU {label}: expected one match, found {count}")
        text = text.replace(old, new, 1)
    dispatch.write_text(text)

    checks = {
        compat_target: ["susfs_compat_show_version", "compat_call_legacy_kstat"],
        makefile: [compat_line.strip()],
        susfs_def: [
            "SUSFS_MAGIC 0xFAFAFAFA",
            "CMD_SUSFS_HIDE_SUS_MNTS_FOR_NON_SU_PROCS",
            "CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING",
        ],
        proc_namespace: ["susfs_hide_sus_mnts_for_all_procs = false;"],
        fdinfo: ["out_seq_printf:\n\t;"],
        dispatch: [
            "susfs_compat_add_sus_kstat(arg, false);",
            "susfs_compat_add_sus_kstat(arg, true);",
            "susfs_compat_show_version(arg);",
        ],
    }
    for path, needles in checks.items():
        content = path.read_text()
        for needle in needles:
            if needle not in content:
                raise SystemExit(f"compatibility verification failed: {path}: {needle}")


if __name__ == "__main__":
    main()
