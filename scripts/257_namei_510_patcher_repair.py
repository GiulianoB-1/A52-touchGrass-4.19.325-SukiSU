#!/usr/bin/env python3
"""Repair Phase257 namei instrumentation for the pinned Android 5.10 shape.

The original Phase257 overlay assumed do_mknodat() owns a struct filename and
putname() lifecycle. The pinned android12-5.10 tree instead keeps the userspace
pathname at the syscall boundary. Instrument the syscall wrappers so the
recorder sees the final return code even when pathname resolution fails before
a dentry exists. No syscall return value or kernel behavior is changed.
"""
from __future__ import annotations

import sys
from pathlib import Path

TARGET = Path(__file__).resolve().with_name(
    "257_phase256_kgsl_publication_pipeline_overlay.py"
)
MARKER = "A52_PHASE257_NAMEI_ANDROID510_SYSCALL_REPAIR_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def repair(text: str) -> str:
    if MARKER in text:
        validate(text)
        return text

    header_old = '''        "#include <linux/sched.h>",
        "#include <linux/a52_ack_forensic.h>",
'''
    header_new = '''        "#include <linux/sched.h>",
        "#include <linux/uaccess.h>",
        "#include <linux/a52_ack_forensic.h>",
'''
    text = replace_once(text, header_old, header_new, "uaccess include")

    checker_old = '''static bool a52_r257_is_kgsl_name(const struct filename *name)
{
\tconst char *base;

\tif (!name || !name->name)
\t\treturn false;
\tbase = strrchr(name->name, '/');
\tbase = base ? base + 1 : name->name;
\treturn !strcmp(base, "kgsl-3d0");
}

static void a52_r257_kgsl_node_event(int op, const struct filename *name,
\t\tint rc, umode_t mode, dev_t dev)
'''
    checker_new = '''static bool a52_r257_is_kgsl_name(const char *name)
{
\tconst char *base;

\tif (!name)
\t\treturn false;
\tbase = strrchr(name, '/');
\tbase = base ? base + 1 : name;
\treturn !strcmp(base, "kgsl-3d0");
}

static void a52_r257_kgsl_node_event(int op, const char *name,
\t\tint rc, umode_t mode, dev_t dev)
'''
    text = replace_once(text, checker_old, checker_new, "kernel-name helper")

    user_anchor = '''void a52_r257_kgsl_node_snapshot(void)
'''
    user_helper = '''/* A52_PHASE257_NAMEI_ANDROID510_SYSCALL_REPAIR_V1 */
static void a52_r257_kgsl_user_node_event(int op, const char __user *name,
\t\tint rc, umode_t mode, dev_t dev)
{
\tchar path[64];
\tlong copied;

\tif (!name)
\t\treturn;
\tcopied = strncpy_from_user(path, name, sizeof(path) - 1);
\tif (copied < 0)
\t\treturn;
\tif (copied < (long)sizeof(path) - 1)
\t\tpath[copied] = '\\0';
\telse
\t\tpath[sizeof(path) - 1] = '\\0';
\ta52_r257_kgsl_node_event(op, path, rc, mode, dev);
}

void a52_r257_kgsl_node_snapshot(void)
'''
    text = replace_once(text, user_anchor, user_helper, "userspace-name helper")

    block_start_token = '''    start, end, fn = _find_function_re(
        text, r"(?m)^(?:static\\s+)?(?:int|long)\\s+do_mknodat\\s*\\(",
        f"{path}: do_mknodat"
    )
'''
    start = text.find(block_start_token)
    if start < 0:
        raise RuntimeError("Phase257 do_mknodat instrumentation block start not found")
    end_token = '    path.write_text(text, encoding="utf-8")\n'
    end = text.find(end_token, start)
    if end < 0:
        raise RuntimeError("Phase257 namei instrumentation block end not found")
    end += len(end_token)

    replacement = r'''    # Android 5.10 keeps the userspace pathname at the syscall boundary.
    # Instrument there so even pre-dentry failures are retained. The older
    # struct-filename path remains as a fixture/backport fallback.
    if "SYSCALL_DEFINE4(mknodat" in text and "SYSCALL_DEFINE3(mknod" in text:
        for pattern, label, user_arg in (
            (r"(?m)^SYSCALL_DEFINE4\(mknodat,", "mknodat", "filename"),
            (r"(?m)^SYSCALL_DEFINE3\(mknod,", "mknod", "filename"),
        ):
            start, end, fn = _find_function_re(text, pattern, f"{path}: {label} syscall")
            call = re.search(r"(?m)^(\s*)return (do_mknodat\([^;\n]+\));$", fn)
            if not call:
                raise RuntimeError(f"{path}: {label} do_mknodat return anchor missing")
            indent = call.group(1)
            expr = call.group(2)
            body = (
                indent + "{\n"
                + indent + "\tlong a52_r257_rc = " + expr + ";\n"
                + indent + "\tdev_t a52_r257_dev = 0;\n\n"
                + indent + "\tif (S_ISCHR(mode) || S_ISBLK(mode))\n"
                + indent + "\t\ta52_r257_dev = new_decode_dev(dev);\n"
                + indent + f"\ta52_r257_kgsl_user_node_event(1, {user_arg}, "
                  "a52_r257_rc, mode, a52_r257_dev);\n"
                + indent + "\treturn a52_r257_rc;\n"
                + indent + "}"
            )
            fn = fn[:call.start()] + body + fn[call.end():]
            text = text[:start] + fn + text[end:]
    else:
        start, end, fn = _find_function_re(
            text, r"(?m)^(?:static\s+)?(?:int|long)\s+do_mknodat\s*\(",
            f"{path}: do_mknodat fallback"
        )
        if "struct filename *name" not in fn:
            raise RuntimeError(f"{path}: unsupported do_mknodat pathname shape")
        tail = "\tputname(name);\n\treturn error;"
        if fn.count(tail) != 1:
            raise RuntimeError(f"{path}: fallback do_mknodat tail count {fn.count(tail)}")
        mknod_event = '''\t{
\t\tdev_t a52_r257_dev = 0;

\t\tif (S_ISCHR(mode) || S_ISBLK(mode))
\t\t\ta52_r257_dev = new_decode_dev(dev);
\t\ta52_r257_kgsl_node_event(1, name->name, error, mode, a52_r257_dev);
\t}
\tputname(name);
\treturn error;'''
        fn = fn.replace(tail, mknod_event, 1)
        text = text[:start] + fn + text[end:]

    if "SYSCALL_DEFINE3(unlinkat" in text and "SYSCALL_DEFINE1(unlink" in text:
        for pattern, label, user_arg in (
            (r"(?m)^SYSCALL_DEFINE3\(unlinkat,", "unlinkat", "pathname"),
            (r"(?m)^SYSCALL_DEFINE1\(unlink,", "unlink", "pathname"),
        ):
            start, end, fn = _find_function_re(text, pattern, f"{path}: {label} syscall")
            call = re.search(r"(?m)^(\s*)return (do_unlinkat\([^;\n]+\));$", fn)
            if not call:
                raise RuntimeError(f"{path}: {label} do_unlinkat return anchor missing")
            indent = call.group(1)
            expr = call.group(2)
            body = (
                indent + "{\n"
                + indent + "\tlong a52_r257_rc = " + expr + ";\n\n"
                + indent + f"\ta52_r257_kgsl_user_node_event(2, {user_arg}, "
                  "a52_r257_rc, 0, 0);\n"
                + indent + "\treturn a52_r257_rc;\n"
                + indent + "}"
            )
            fn = fn[:call.start()] + body + fn[call.end():]
            text = text[:start] + fn + text[end:]
    else:
        start, end, fn = _find_function_re(
            text, r"(?m)^(?:static\s+)?(?:int|long)\s+do_unlinkat\s*\(",
            f"{path}: do_unlinkat fallback"
        )
        if "struct filename *name" not in fn:
            raise RuntimeError(f"{path}: unsupported do_unlinkat pathname shape")
        tail = "\tputname(name);\n\treturn error;"
        if fn.count(tail) != 1:
            raise RuntimeError(f"{path}: fallback do_unlinkat tail count {fn.count(tail)}")
        unlink_event = '''\ta52_r257_kgsl_node_event(2, name->name, error, 0, 0);
\tputname(name);
\treturn error;'''
        fn = fn.replace(tail, unlink_event, 1)
        text = text[:start] + fn + text[end:]
    path.write_text(text, encoding="utf-8")
'''

    text = text[:start] + replacement + text[end:]
    validate(text)
    return text


def validate(text: str) -> None:
    required = (
        MARKER,
        '"#include <linux/uaccess.h>"',
        "static bool a52_r257_is_kgsl_name(const char *name)",
        "static void a52_r257_kgsl_user_node_event",
        "strncpy_from_user(path, name, sizeof(path) - 1)",
        "SYSCALL_DEFINE4(mknodat",
        "a52_r257_kgsl_user_node_event(1",
        "SYSCALL_DEFINE3(unlinkat",
        "a52_r257_kgsl_user_node_event(2",
        "name->name",
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"repaired Phase257 overlay missing {token!r}")
    compile(text, str(TARGET), "exec")


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    repaired = repair(text)
    TARGET.write_text(repaired, encoding="utf-8")
    print(f"{MARKER}: Phase257 namei recorder moved to syscall boundary", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase257 Android 5.10 namei repair failed: {exc}", file=sys.stderr)
        raise
