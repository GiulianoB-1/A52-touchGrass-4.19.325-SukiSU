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

    stat = kernel / "fs/stat.c"
    replace_once(
        stat,
        """#ifdef CONFIG_KSU
extern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);
extern void ksu_handle_newfstat_ret(unsigned int *fd, struct stat __user **statbuf_ptr);
#if defined(__ARCH_WANT_STAT64) || defined(__ARCH_WANT_COMPAT_STAT64)
extern void ksu_handle_fstat64_ret(unsigned long *fd, struct stat64 __user **statbuf_ptr);
#endif
#endif
""",
        """#ifdef CONFIG_KSU
extern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);
#endif
#ifdef CONFIG_KSU_SUSFS
extern void ksu_handle_vfs_fstat(int fd, loff_t *kstat_size_ptr);
#endif
""",
        "ReSukiSU SUSFS fstat declaration",
    )
    replace_once(
        stat,
        """SYSCALL_DEFINE2(newfstat, unsigned int, fd, struct stat __user *, statbuf)
{
	struct kstat stat;
	int error = vfs_fstat(fd, &stat);

	if (!error)
		error = cp_new_stat(&stat, statbuf);
#ifdef CONFIG_KSU
	if (!error)
		ksu_handle_newfstat_ret(&fd, &statbuf);
#endif

	return error;
}
""",
        """SYSCALL_DEFINE2(newfstat, unsigned int, fd, struct stat __user *, statbuf)
{
	struct kstat stat;
	int error = vfs_fstat(fd, &stat);

#ifdef CONFIG_KSU_SUSFS
	if (!error)
		ksu_handle_vfs_fstat((int)fd, &stat.size);
#endif
	if (!error)
		error = cp_new_stat(&stat, statbuf);

	return error;
}
""",
        "ReSukiSU SUSFS newfstat hook",
    )
    replace_once(
        stat,
        """SYSCALL_DEFINE2(fstat64, unsigned long, fd, struct stat64 __user *, statbuf)
{
	struct kstat stat;
	int error = vfs_fstat(fd, &stat);

	if (!error)
		error = cp_new_stat64(&stat, statbuf);
#ifdef CONFIG_KSU
	if (!error)
		ksu_handle_fstat64_ret(&fd, &statbuf);
#endif

	return error;
}
""",
        """SYSCALL_DEFINE2(fstat64, unsigned long, fd, struct stat64 __user *, statbuf)
{
	struct kstat stat;
	int error = vfs_fstat(fd, &stat);

#ifdef CONFIG_KSU_SUSFS
	if (!error)
		ksu_handle_vfs_fstat((int)fd, &stat.size);
#endif
	if (!error)
		error = cp_new_stat64(&stat, statbuf);

	return error;
}
""",
        "ReSukiSU SUSFS fstat64 hook",
    )

    fdinfo = kernel / "fs/notify/fdinfo.c"
    text = fdinfo.read_text()
    newer_inotify_helper = "inotify_mark_user_mask(mark)"
    if text.count(newer_inotify_helper) != 2:
        raise SystemExit(
            "SUSFS fdinfo inotify helper: expected two matches, "
            f"found {text.count(newer_inotify_helper)}"
        )
    text = text.replace(newer_inotify_helper, "(mark->mask & IN_ALL_EVENTS)")
    label = "out_seq_printf:\n#endif"
    if text.count(label) != 1:
        raise SystemExit(
            f"SUSFS fdinfo label statement: expected one match, found {text.count(label)}"
        )
    fdinfo.write_text(text.replace(label, "out_seq_printf:\n\t;\n#endif", 1))

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
        stat: [
            "extern void ksu_handle_vfs_fstat(int fd, loff_t *kstat_size_ptr);",
            "ksu_handle_vfs_fstat((int)fd, &stat.size);",
        ],
        fdinfo: ["out_seq_printf:\n\t;", "(mark->mask & IN_ALL_EVENTS)"],
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

    stat_text = stat.read_text()
    for forbidden in ("ksu_handle_newfstat_ret", "ksu_handle_fstat64_ret"):
        if forbidden in stat_text:
            raise SystemExit(f"obsolete manual-hook symbol remains in fs/stat.c: {forbidden}")
    if "inotify_mark_user_mask" in fdinfo.read_text():
        raise SystemExit("newer inotify helper remains in Linux 4.19 fdinfo.c")


if __name__ == "__main__":
    main()
