#!/usr/bin/env python3
'''Phase 257: trace the complete KGSL userspace publication pipeline.

TouchGrass creates kgsl-3d0 before ueventd starts, so an early KOBJ_ADD is
normal. Phase257 records the initial device publication, Android's later sysfs
coldboot replay, generic DEVNAME metadata, userspace mknod/mknodat results, and
unlink/unlinkat removal results. Early state is retained and re-emitted during
the existing late /dev/kgsl-3d0 open failures.

Instrumentation only: no device node is created, no return value is changed,
and no devtmpfs, SELinux, DT, ramdisk, ueventd rule, probe, major/minor, or GPU
behavior is modified.
'''
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

MARKER = "A52_PHASE257_KGSL_PUBLICATION_PIPELINE_V1"
CORE_MARKER = "A52_PHASE257_KGSL_PUBLICATION_CORE_V1"
REPLAY_MARKER = "A52_PHASE257_KGSL_COLDBOOT_REPLAY_V1"
META_MARKER = "A52_PHASE257_KGSL_UEVENT_METADATA_V1"
NAMEI_MARKER = "A52_PHASE257_KGSL_NODE_SYSCALL_V1"
OPEN_MARKER = "A52_PHASE257_KGSL_LATE_REEMIT_V1"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def locate(args: list[str]) -> Path:
    roots: list[Path] = []
    cwd = Path.cwd()
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))

    hits: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        core = root / "drivers/base/core.c"
        open_c = root / "fs/open.c"
        namei = root / "fs/namei.c"
        recorder = root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
        if not all(p.is_file() for p in (core, open_c, namei, recorder)):
            continue
        if "A52_PHASE256_KGSL_DEVNODE_UEVENT_V1" not in core.read_text(encoding="utf-8"):
            continue
        if "a52_r225_kgsl_late_snapshot(fd)" not in open_c.read_text(encoding="utf-8"):
            continue
        if 'strncmp(fmt, "F256", 4)' not in recorder.read_text(encoding="utf-8"):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(
            "expected one generated Phase256 source root, found "
            + (", ".join(map(str, hits)) or "none")
        )
    return hits[0]


def patch_recorder(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    fmt = 'if (strncmp(fmt, "F256", 4) &&\n'
    text = replace_once(
        text,
        fmt,
        f'''/* {MARKER}
 * Retain the Phase257 KGSL publication/coldboot/node-creation stream.
 */
if (strncmp(fmt, "F257", 4) &&
    strncmp(fmt, "F256", 4) &&
''',
        f"{path}: F257 admission",
    )
    critical = 'return !strncmp(message, "F256 ", 5) ||\n'
    text = replace_once(
        text,
        critical,
        'return !strncmp(message, "F257 ", 5) ||\n       !strncmp(message, "F256 ", 5) ||\n',
        f"{path}: F257 retention",
    )
    path.write_text(text, encoding="utf-8")


def _find_function(text: str, signature: str, label: str) -> tuple[int, int, str]:
    start = text.find(signature)
    if start < 0:
        raise RuntimeError(f"{label}: function signature not found")
    return _function_from_start(text, start, label)


def _find_function_re(text: str, pattern: str, label: str) -> tuple[int, int, str]:
    match = re.search(pattern, text, re.M)
    if not match:
        raise RuntimeError(f"{label}: function signature regex not found")
    return _function_from_start(text, match.start(), label)


def _function_from_start(text: str, start: int, label: str) -> tuple[int, int, str]:
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace not found")
    depth = 0
    end = -1
    for i in range(brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise RuntimeError(f"{label}: closing brace not found")
    return start, end, text[start:end]


def patch_core(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if CORE_MARKER in text:
        return

    if "#include <linux/ktime.h>" not in text:
        include_hits = list(re.finditer(r"(?m)^#include <linux/[^>]+>\s*$", text))
        if not include_hits:
            raise RuntimeError(f"{path}: cannot find linux include anchor")
        pos = include_hits[-1].end()
        text = text[:pos] + "\n#include <linux/ktime.h>" + text[pos:]
    if "#include <linux/sched.h>" not in text:
        include_hits = list(re.finditer(r"(?m)^#include <linux/[^>]+>\s*$", text))
        pos = include_hits[-1].end()
        text = text[:pos] + "\n#include <linux/sched.h>" + text[pos:]

    state_anchor = "static ssize_t uevent_store(struct device *dev, struct device_attribute *attr,\n"
    if text.count(state_anchor) != 1:
        raise RuntimeError(f"{path}: uevent_store state anchor drifted")
    state = '''/* A52_PHASE257_KGSL_PUBLICATION_CORE_V1 */
static atomic_t a52_r257_add_count = ATOMIC_INIT(0);
static atomic_t a52_r257_replay_count = ATOMIC_INIT(0);
static atomic_t a52_r257_meta_count = ATOMIC_INIT(0);
static atomic_t a52_r257_replay_meta_count = ATOMIC_INIT(0);
static atomic_t a52_r257_snapshot_count = ATOMIC_INIT(0);
static u64 a52_r257_add_ns;
static u64 a52_r257_replay_first_ns;
static u64 a52_r257_replay_last_ns;
static int a52_r257_add_rc = -ENODATA;
static int a52_r257_add_major;
static int a52_r257_add_minor;
static int a52_r257_add_sysfs;
static int a52_r257_replay_rc = -ENODATA;
static int a52_r257_replay_major;
static int a52_r257_replay_minor;
static int a52_r257_replay_sysfs;
static int a52_r257_replay_pid;
static int a52_r257_replay_tgid;
static int a52_r257_replay_is_add;
static int a52_r257_replay_active;
static int a52_r257_devname_seen;
static char a52_r257_replay_comm[16];
static char a52_r257_devname[32];

void a52_r257_kgsl_pub_snapshot(int open_rc)
{
	unsigned int n = atomic_inc_return(&a52_r257_snapshot_count);

	if (n > 12)
		return;
	a52_ackfr_record("F257 s1 n=%u o=%d ac=%d ar=%d M=%d m=%d s=%d at=%llu",
		n, open_rc, atomic_read(&a52_r257_add_count),
		READ_ONCE(a52_r257_add_rc), READ_ONCE(a52_r257_add_major),
		READ_ONCE(a52_r257_add_minor), READ_ONCE(a52_r257_add_sysfs),
		(unsigned long long)(READ_ONCE(a52_r257_add_ns) / 1000000ULL));
	a52_ackfr_record("F257 s2 wc=%d wr=%d a=%d p=%d g=%d M=%d m=%d s=%d ft=%llu",
		atomic_read(&a52_r257_replay_count), READ_ONCE(a52_r257_replay_rc),
		READ_ONCE(a52_r257_replay_is_add), READ_ONCE(a52_r257_replay_pid),
		READ_ONCE(a52_r257_replay_tgid), READ_ONCE(a52_r257_replay_major),
		READ_ONCE(a52_r257_replay_minor), READ_ONCE(a52_r257_replay_sysfs),
		(unsigned long long)(READ_ONCE(a52_r257_replay_first_ns) / 1000000ULL));
	a52_ackfr_record("F257 s3 mc=%d mr=%d dn=%d c=%.15s d=%.31s lt=%llu",
		atomic_read(&a52_r257_meta_count), atomic_read(&a52_r257_replay_meta_count),
		READ_ONCE(a52_r257_devname_seen), a52_r257_replay_comm,
		a52_r257_devname,
		(unsigned long long)(READ_ONCE(a52_r257_replay_last_ns) / 1000000ULL));
}
EXPORT_SYMBOL_GPL(a52_r257_kgsl_pub_snapshot);

'''
    text = text.replace(state_anchor, state + state_anchor, 1)

    sig = "static ssize_t uevent_store(struct device *dev, struct device_attribute *attr,"
    start, end, fn = _find_function(text, sig, f"{path}: uevent_store")
    synth = "\trc = kobject_synth_uevent(&dev->kobj, buf, count);\n"
    if fn.count(synth) != 1:
        raise RuntimeError(f"{path}: uevent_store synth anchor drifted")
    replay = '''	/* A52_PHASE257_KGSL_COLDBOOT_REPLAY_V1 */
	if (!strcmp(dev_name(dev), "kgsl-3d0")) {
		unsigned int a52_r257_n = atomic_inc_return(&a52_r257_replay_count);
		u64 a52_r257_now = ktime_get_ns();

		if (a52_r257_n == 1)
			WRITE_ONCE(a52_r257_replay_first_ns, a52_r257_now);
		WRITE_ONCE(a52_r257_replay_last_ns, a52_r257_now);
		WRITE_ONCE(a52_r257_replay_major, MAJOR(dev->devt));
		WRITE_ONCE(a52_r257_replay_minor, MINOR(dev->devt));
		WRITE_ONCE(a52_r257_replay_sysfs, dev->kobj.state_in_sysfs ? 1 : 0);
		WRITE_ONCE(a52_r257_replay_pid, current->pid);
		WRITE_ONCE(a52_r257_replay_tgid, current->tgid);
		WRITE_ONCE(a52_r257_replay_is_add,
			count >= 3 && !strncmp(buf, "add", 3));
		strscpy(a52_r257_replay_comm, current->comm,
			sizeof(a52_r257_replay_comm));
		WRITE_ONCE(a52_r257_replay_active, 1);
		a52_ackfr_record("F257 wr n=%u a=%.4s p=%d g=%d M=%u m=%u s=%d",
			a52_r257_n, buf, current->pid, current->tgid,
			MAJOR(dev->devt), MINOR(dev->devt),
			dev->kobj.state_in_sysfs ? 1 : 0);
		rc = kobject_synth_uevent(&dev->kobj, buf, count);
		WRITE_ONCE(a52_r257_replay_active, 0);
		WRITE_ONCE(a52_r257_replay_rc, rc);
		a52_ackfr_record("F257 wrx n=%u rc=%d dn=%d mc=%d",
			a52_r257_n, rc, READ_ONCE(a52_r257_devname_seen),
			atomic_read(&a52_r257_replay_meta_count));
	} else {
		rc = kobject_synth_uevent(&dev->kobj, buf, count);
	}
'''
    fn = fn.replace(synth, replay, 1)
    text = text[:start] + fn + text[end:]

    start, end, fn = _find_function(text, "static int dev_uevent(", f"{path}: dev_uevent")
    name_pat = re.compile(r"(?m)^(\s*)name = device_get_devnode\(([^\n]+)\);\n")
    matches = list(name_pat.finditer(fn))
    if len(matches) != 1:
        raise RuntimeError(f"{path}: dev_uevent device_get_devnode anchor count {len(matches)}")
    m = matches[0]
    indent = m.group(1)
    metadata = m.group(0) + indent + '''/* A52_PHASE257_KGSL_UEVENT_METADATA_V1 */
''' + indent + '''if (!strcmp(dev_name(dev), "kgsl-3d0")) {
''' + indent + '''	int a52_r257_replay = READ_ONCE(a52_r257_replay_active);
''' + indent + '''	atomic_inc(&a52_r257_meta_count);
''' + indent + '''	if (a52_r257_replay)
''' + indent + '''		atomic_inc(&a52_r257_replay_meta_count);
''' + indent + '''	WRITE_ONCE(a52_r257_devname_seen, name ? 1 : 0);
''' + indent + '''	strscpy(a52_r257_devname, name ? name : "-",
''' + indent + '''		sizeof(a52_r257_devname));
''' + indent + '''	a52_ackfr_record("F257 md r=%d dn=%.31s M=%u m=%u",
''' + indent + '''		a52_r257_replay, name ? name : "-",
''' + indent + '''		MAJOR(dev->devt), MINOR(dev->devt));
''' + indent + '''}
'''
    fn = fn[:m.start()] + metadata + fn[m.end():]
    text = text[:start] + fn + text[end:]

    old = '''	/* A52_PHASE256_KGSL_DEVNODE_UEVENT_V1 */
	if (!strcmp(dev_name(dev), "kgsl-3d0")) {
		int a52_r256_uevent_rc;

		a52_ackfr_record("F256 da n=%.16s M=%u m=%u",
			dev_name(dev), MAJOR(dev->devt), MINOR(dev->devt));
		a52_r256_uevent_rc = kobject_uevent(&dev->kobj, KOBJ_ADD);
		a52_ackfr_record("F256 ue n=%.16s rc=%d",
			dev_name(dev), a52_r256_uevent_rc);
	} else {
		kobject_uevent(&dev->kobj, KOBJ_ADD);
	}
'''
    new = '''	/* A52_PHASE256_KGSL_DEVNODE_UEVENT_V1 */
	if (!strcmp(dev_name(dev), "kgsl-3d0")) {
		int a52_r256_uevent_rc;
		unsigned int a52_r257_n = atomic_inc_return(&a52_r257_add_count);

		WRITE_ONCE(a52_r257_add_ns, ktime_get_ns());
		WRITE_ONCE(a52_r257_add_major, MAJOR(dev->devt));
		WRITE_ONCE(a52_r257_add_minor, MINOR(dev->devt));
		WRITE_ONCE(a52_r257_add_sysfs, dev->kobj.state_in_sysfs ? 1 : 0);
		a52_ackfr_record("F256 da n=%.16s M=%u m=%u",
			dev_name(dev), MAJOR(dev->devt), MINOR(dev->devt));
		a52_ackfr_record("F257 add n=%u M=%u m=%u s=%d",
			a52_r257_n, MAJOR(dev->devt), MINOR(dev->devt),
			dev->kobj.state_in_sysfs ? 1 : 0);
		a52_r256_uevent_rc = kobject_uevent(&dev->kobj, KOBJ_ADD);
		WRITE_ONCE(a52_r257_add_rc, a52_r256_uevent_rc);
		a52_ackfr_record("F256 ue n=%.16s rc=%d",
			dev_name(dev), a52_r256_uevent_rc);
		a52_ackfr_record("F257 addx n=%u rc=%d dn=%d mc=%d",
			a52_r257_n, a52_r256_uevent_rc,
			READ_ONCE(a52_r257_devname_seen),
			atomic_read(&a52_r257_meta_count));
	} else {
		kobject_uevent(&dev->kobj, KOBJ_ADD);
	}
'''
    text = replace_once(text, old, new, f"{path}: Phase256 KOBJ_ADD expansion")
    path.write_text(text, encoding="utf-8")


def patch_namei(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if NAMEI_MARKER in text:
        return

    for header in (
        "#include <linux/atomic.h>",
        "#include <linux/ktime.h>",
        "#include <linux/sched.h>",
        "#include <linux/a52_ack_forensic.h>",
    ):
        if header not in text:
            include_hits = list(re.finditer(r"(?m)^#include <linux/[^>]+>\s*$", text))
            if not include_hits:
                raise RuntimeError(f"{path}: cannot find linux include anchor for {header}")
            pos = include_hits[-1].end()
            text = text[:pos] + "\n" + header + text[pos:]

    anchor = "static int may_mknod(umode_t mode)\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: may_mknod anchor drifted")
    state = '''/* A52_PHASE257_KGSL_NODE_SYSCALL_V1 */
static atomic_t a52_r257_mknod_count = ATOMIC_INIT(0);
static atomic_t a52_r257_unlink_count = ATOMIC_INIT(0);
static atomic_t a52_r257_node_snapshot_count = ATOMIC_INIT(0);
static int a52_r257_mknod_rc = -ENODATA;
static int a52_r257_mknod_pid;
static int a52_r257_mknod_tgid;
static unsigned int a52_r257_mknod_mode;
static unsigned int a52_r257_mknod_major;
static unsigned int a52_r257_mknod_minor;
static u64 a52_r257_mknod_ns;
static int a52_r257_unlink_rc = -ENODATA;
static int a52_r257_unlink_pid;
static int a52_r257_unlink_tgid;
static u64 a52_r257_unlink_ns;
static char a52_r257_mknod_comm[16];
static char a52_r257_unlink_comm[16];

static bool a52_r257_is_kgsl_name(const struct filename *name)
{
	const char *base;

	if (!name || !name->name)
		return false;
	base = strrchr(name->name, '/');
	base = base ? base + 1 : name->name;
	return !strcmp(base, "kgsl-3d0");
}

static void a52_r257_kgsl_node_event(int op, const struct filename *name,
		int rc, umode_t mode, dev_t dev)
{
	unsigned int n;
	u64 now;

	if (!a52_r257_is_kgsl_name(name))
		return;
	now = ktime_get_ns();
	if (op == 1) {
		n = atomic_inc_return(&a52_r257_mknod_count);
		WRITE_ONCE(a52_r257_mknod_rc, rc);
		WRITE_ONCE(a52_r257_mknod_pid, current->pid);
		WRITE_ONCE(a52_r257_mknod_tgid, current->tgid);
		WRITE_ONCE(a52_r257_mknod_mode, mode);
		WRITE_ONCE(a52_r257_mknod_major, MAJOR(dev));
		WRITE_ONCE(a52_r257_mknod_minor, MINOR(dev));
		WRITE_ONCE(a52_r257_mknod_ns, now);
		strscpy(a52_r257_mknod_comm, current->comm,
			sizeof(a52_r257_mknod_comm));
		a52_ackfr_record("F257 mk n=%u rc=%d p=%d g=%d mo=%o M=%u m=%u c=%.15s",
			n, rc, current->pid, current->tgid, mode,
			MAJOR(dev), MINOR(dev), current->comm);
	} else if (op == 2) {
		n = atomic_inc_return(&a52_r257_unlink_count);
		WRITE_ONCE(a52_r257_unlink_rc, rc);
		WRITE_ONCE(a52_r257_unlink_pid, current->pid);
		WRITE_ONCE(a52_r257_unlink_tgid, current->tgid);
		WRITE_ONCE(a52_r257_unlink_ns, now);
		strscpy(a52_r257_unlink_comm, current->comm,
			sizeof(a52_r257_unlink_comm));
		a52_ackfr_record("F257 ul n=%u rc=%d p=%d g=%d c=%.15s",
			n, rc, current->pid, current->tgid, current->comm);
	}
}

void a52_r257_kgsl_node_snapshot(void)
{
	unsigned int n = atomic_inc_return(&a52_r257_node_snapshot_count);

	if (n > 12)
		return;
	a52_ackfr_record("F257 s4 kc=%d kr=%d p=%d g=%d mo=%o M=%u m=%u kt=%llu",
		atomic_read(&a52_r257_mknod_count), READ_ONCE(a52_r257_mknod_rc),
		READ_ONCE(a52_r257_mknod_pid), READ_ONCE(a52_r257_mknod_tgid),
		READ_ONCE(a52_r257_mknod_mode), READ_ONCE(a52_r257_mknod_major),
		READ_ONCE(a52_r257_mknod_minor),
		(unsigned long long)(READ_ONCE(a52_r257_mknod_ns) / 1000000ULL));
	a52_ackfr_record("F257 s5 uc=%d ur=%d p=%d g=%d kc=%.15s uc=%.15s ut=%llu",
		atomic_read(&a52_r257_unlink_count), READ_ONCE(a52_r257_unlink_rc),
		READ_ONCE(a52_r257_unlink_pid), READ_ONCE(a52_r257_unlink_tgid),
		a52_r257_mknod_comm, a52_r257_unlink_comm,
		(unsigned long long)(READ_ONCE(a52_r257_unlink_ns) / 1000000ULL));
}
EXPORT_SYMBOL_GPL(a52_r257_kgsl_node_snapshot);

'''
    text = text.replace(anchor, state + anchor, 1)

    start, end, fn = _find_function_re(
        text, r"(?m)^(?:static\s+)?(?:int|long)\s+do_mknodat\s*\(",
        f"{path}: do_mknodat"
    )
    if "struct filename *name" not in fn:
        raise RuntimeError(f"{path}: do_mknodat no longer uses struct filename *name")
    tail = "\tputname(name);\n\treturn error;"
    if fn.count(tail) != 1:
        raise RuntimeError(f"{path}: do_mknodat final putname/return anchor count {fn.count(tail)}")
    mknod_event = '''	{
		dev_t a52_r257_dev = 0;

		if (S_ISCHR(mode) || S_ISBLK(mode))
			a52_r257_dev = new_decode_dev(dev);
		a52_r257_kgsl_node_event(1, name, error, mode, a52_r257_dev);
	}
	putname(name);
	return error;'''
    fn = fn.replace(tail, mknod_event, 1)
    text = text[:start] + fn + text[end:]

    start, end, fn = _find_function_re(
        text, r"(?m)^(?:static\s+)?(?:int|long)\s+do_unlinkat\s*\(",
        f"{path}: do_unlinkat"
    )
    if "struct filename *name" not in fn:
        raise RuntimeError(f"{path}: do_unlinkat no longer uses struct filename *name")
    tail = "\tputname(name);\n\treturn error;"
    if fn.count(tail) != 1:
        raise RuntimeError(f"{path}: do_unlinkat final putname/return anchor count {fn.count(tail)}")
    unlink_event = '''	a52_r257_kgsl_node_event(2, name, error, 0, 0);
	putname(name);
	return error;'''
    fn = fn.replace(tail, unlink_event, 1)
    text = text[:start] + fn + text[end:]
    path.write_text(text, encoding="utf-8")


def patch_open(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if OPEN_MARKER in text:
        return
    decl_anchor = "extern void a52_r225_kgsl_late_snapshot(int open_rc);\n"
    text = replace_once(
        text,
        decl_anchor,
        decl_anchor
        + f"/* {OPEN_MARKER} */\n"
        + "extern void a52_r257_kgsl_pub_snapshot(int open_rc);\n"
        + "extern void a52_r257_kgsl_node_snapshot(void);\n",
        f"{path}: Phase257 declarations",
    )
    call_anchor = '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0"))
\t\ta52_r225_kgsl_late_snapshot(fd);
'''
    call_new = '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0")) {
\t\ta52_r225_kgsl_late_snapshot(fd);
\t\ta52_r257_kgsl_pub_snapshot(fd);
\t\ta52_r257_kgsl_node_snapshot();
\t}
'''
    text = replace_once(text, call_anchor, call_new, f"{path}: Phase257 late re-emission")
    path.write_text(text, encoding="utf-8")


def verify(root: Path) -> None:
    checks = {
        "drivers/a52_secure/a52_ack_secure_flight_recorder.c": (
            MARKER, 'strncmp(fmt, "F257", 4)', '!strncmp(message, "F257 ", 5)'),
        "drivers/base/core.c": (
            CORE_MARKER, REPLAY_MARKER, META_MARKER,
            "a52_r257_kgsl_pub_snapshot", "F257 add", "F257 wr", "F257 md",
            "F257 s1", "F257 s2", "F257 s3", "kobject_synth_uevent"),
        "fs/namei.c": (
            NAMEI_MARKER, "a52_r257_kgsl_node_event", "F257 mk", "F257 ul",
            "F257 s4", "F257 s5", "do_mknodat", "do_unlinkat"),
        "fs/open.c": (
            OPEN_MARKER, "a52_r257_kgsl_pub_snapshot(fd)",
            "a52_r257_kgsl_node_snapshot()", '"/dev/kgsl-3d0"'),
    }
    for rel, tokens in checks.items():
        text = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                raise RuntimeError(f"Phase257 verification {rel}: missing {token!r}")

    core = (root / "drivers/base/core.c").read_text(encoding="utf-8")
    if core.count("A52_PHASE256_KGSL_DEVNODE_UEVENT_V1") != 1:
        raise RuntimeError("Phase257 changed Phase256 KOBJ_ADD marker cardinality")
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        "devtmpfs_" + "create_node(",
        "ksys_" + "mknod(",
        "selinux_" + "enforcing=0",
        "CONFIG_DEVTMPFS" + "=y",
    ):
        if token in source:
            raise RuntimeError(f"Phase257 overlay contains forbidden behavior token {token!r}")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "drivers/base").mkdir(parents=True)
        (root / "drivers/a52_secure").mkdir(parents=True)
        (root / "fs").mkdir(parents=True)
        (root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c").write_text(
            '''if (strncmp(fmt, "F256", 4) &&
    strncmp(fmt, "K255VIS", 7)) return;
return !strncmp(message, "F256 ", 5) ||
       !strncmp(message, "K255VIS ", 8);
''', encoding="utf-8")
        (root / "fs/open.c").write_text(
            '''/* A52_PHASE225_KGSL_LATE_STATE */
extern void a52_r225_kgsl_late_snapshot(int open_rc);

'''
            '''if (trace)
\ta52_ackfr_record("DRMPOST 212 path-ret n=%u fd=%d", trace_id, fd);
'''
            '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0"))
\t\ta52_r225_kgsl_late_snapshot(fd);
''', encoding="utf-8")
        (root / "drivers/base/core.c").write_text(
            '''#include <linux/device.h>
#include <linux/a52_ack_forensic.h>

'''
            '''static int dev_uevent(struct kset *kset, struct kobject *kobj, struct kobj_uevent_env *env)
{
'''
            '''\tstruct device *dev = kobj_to_dev(kobj);
\tconst char *tmp;
\tconst char *name;
\tumode_t mode = 0;
\tkuid_t uid = GLOBAL_ROOT_UID;
\tkgid_t gid = GLOBAL_ROOT_GID;
'''
            '''\tname = device_get_devnode(dev, &mode, &uid, &gid, &tmp);
\tif (name) add_uevent_var(env, "DEVNAME=%s", name);
\treturn 0;
}

'''
            '''static ssize_t uevent_store(struct device *dev, struct device_attribute *attr,
'''
            '''\t\t\t   const char *buf, size_t count)
{
\tint rc;

'''
            '''\trc = kobject_synth_uevent(&dev->kobj, buf, count);

\tif (rc) return rc;
\treturn count;
}

'''
            '''int device_add(struct device *dev)
{
'''
            '''\t/* A52_PHASE256_KGSL_DEVNODE_UEVENT_V1 */
\tif (!strcmp(dev_name(dev), "kgsl-3d0")) {
\t\tint a52_r256_uevent_rc;

'''
            '''\t\ta52_ackfr_record("F256 da n=%.16s M=%u m=%u",
\t\t\tdev_name(dev), MAJOR(dev->devt), MINOR(dev->devt));
'''
            '''\t\ta52_r256_uevent_rc = kobject_uevent(&dev->kobj, KOBJ_ADD);
'''
            '''\t\ta52_ackfr_record("F256 ue n=%.16s rc=%d",
\t\t\tdev_name(dev), a52_r256_uevent_rc);
'''
            '''\t} else {
\t\tkobject_uevent(&dev->kobj, KOBJ_ADD);
\t}
\treturn 0;
}
''', encoding="utf-8")
        (root / "fs/namei.c").write_text(
            '''#include <linux/fs.h>
#include <linux/namei.h>

'''
            '''static int may_mknod(umode_t mode)
{
\treturn 0;
}

'''
            '''static int do_mknodat(int dfd, struct filename *name, umode_t mode,
\t\tunsigned int dev)
{
\tint error = 0;
\tputname(name);
\treturn error;
}

'''
            '''static int do_unlinkat(int dfd, struct filename *name)
{
\tint error = 0;
\tputname(name);
\treturn error;
}
''', encoding="utf-8")
        patch_recorder(root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c")
        patch_core(root / "drivers/base/core.c")
        patch_namei(root / "fs/namei.c")
        patch_open(root / "fs/open.c")
        verify(root)
    print("Phase 257 KGSL publication-pipeline overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    patch_recorder(root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c")
    patch_core(root / "drivers/base/core.c")
    patch_namei(root / "fs/namei.c")
    patch_open(root / "fs/open.c")
    verify(root)
    print(
        f"{MARKER}: KGSL initial publication + coldboot + mknod/unlink recorder applied",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
