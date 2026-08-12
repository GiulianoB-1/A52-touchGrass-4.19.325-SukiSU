#!/usr/bin/env python3
"""Phase 257: trace the KGSL device publication and ueventd coldboot boundary.

TouchGrass creates the kgsl-3d0 class device before ueventd starts, so an early
KOBJ_ADD is not itself a failure. Phase257 records whether Android's later
sysfs coldboot replay reaches kgsl-3d0 and whether the kernel synthesizes the
replayed uevent with valid device-node metadata. Early state is retained in
static scalars and re-emitted during the existing late /dev/kgsl-3d0 opens.

Instrumentation only: no device node is created, no return value is changed,
and no devtmpfs, SELinux, DT, ramdisk, ueventd rule, probe, major/minor, or GPU
behavior is modified.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

MARKER = "A52_PHASE257_KGSL_PUBLICATION_PIPELINE_V1"
CORE_MARKER = "A52_PHASE257_KGSL_PUBLICATION_CORE_V1"
REPLAY_MARKER = "A52_PHASE257_KGSL_COLDBOOT_REPLAY_V1"
META_MARKER = "A52_PHASE257_KGSL_UEVENT_METADATA_V1"
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
        recorder = root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
        if not all(p.is_file() for p in (core, open_c, recorder)):
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
        f'''/* {MARKER}\n * Retain the narrow Phase257 KGSL publication/coldboot stream.\n */\nif (strncmp(fmt, "F257", 4) &&\n    strncmp(fmt, "F256", 4) &&\n''',
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

    # Current/task and monotonic boot-time timestamp helpers are diagnostic only.
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
    state = r'''/* A52_PHASE257_KGSL_PUBLICATION_CORE_V1 */
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

    # Observe userspace coldboot writes without changing kobject_synth_uevent semantics.
    sig = "static ssize_t uevent_store(struct device *dev, struct device_attribute *attr,"
    start, end, fn = _find_function(text, sig, f"{path}: uevent_store")
    synth = "\trc = kobject_synth_uevent(&dev->kobj, buf, count);\n"
    if fn.count(synth) != 1:
        raise RuntimeError(f"{path}: uevent_store synth anchor drifted")
    replay = r'''	/* A52_PHASE257_KGSL_COLDBOOT_REPLAY_V1 */
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

    # Record the DEVNAME that the generic device uevent builder actually produces.
    start, end, fn = _find_function(text, "static int dev_uevent(", f"{path}: dev_uevent")
    name_pat = re.compile(r"(?m)^(\s*)name = device_get_devnode\(([^\n]+)\);\n")
    matches = list(name_pat.finditer(fn))
    if len(matches) != 1:
        raise RuntimeError(f"{path}: dev_uevent device_get_devnode anchor count {len(matches)}")
    m = matches[0]
    indent = m.group(1)
    metadata = m.group(0) + indent + r'''/* A52_PHASE257_KGSL_UEVENT_METADATA_V1 */
''' + indent + r'''if (!strcmp(dev_name(dev), "kgsl-3d0")) {
''' + indent + r'''	int a52_r257_replay = READ_ONCE(a52_r257_replay_active);
''' + indent + r'''	atomic_inc(&a52_r257_meta_count);
''' + indent + r'''	if (a52_r257_replay)
''' + indent + r'''		atomic_inc(&a52_r257_replay_meta_count);
''' + indent + r'''	WRITE_ONCE(a52_r257_devname_seen, name ? 1 : 0);
''' + indent + r'''	strscpy(a52_r257_devname, name ? name : "-",
''' + indent + r'''		sizeof(a52_r257_devname));
''' + indent + r'''	a52_ackfr_record("F257 md r=%d dn=%.31s M=%u m=%u",
''' + indent + r'''		a52_r257_replay, name ? name : "-",
''' + indent + r'''		MAJOR(dev->devt), MINOR(dev->devt));
''' + indent + r'''}
'''
    fn = fn[:m.start()] + metadata + fn[m.end():]
    text = text[:start] + fn + text[end:]

    # Upgrade the existing Phase256 initial KOBJ_ADD observer with retained state.
    old = r'''	/* A52_PHASE256_KGSL_DEVNODE_UEVENT_V1 */
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
    new = r'''	/* A52_PHASE256_KGSL_DEVNODE_UEVENT_V1 */
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


def patch_open(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if OPEN_MARKER in text:
        return
    decl_anchor = "extern void a52_r225_kgsl_late_snapshot(int open_rc);\n"
    text = replace_once(
        text,
        decl_anchor,
        decl_anchor + f"/* {OPEN_MARKER} */\nextern void a52_r257_kgsl_pub_snapshot(int open_rc);\n",
        f"{path}: Phase257 declaration",
    )
    call_anchor = '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0"))\n\t\ta52_r225_kgsl_late_snapshot(fd);\n'''
    call_new = '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0")) {\n\t\ta52_r225_kgsl_late_snapshot(fd);\n\t\ta52_r257_kgsl_pub_snapshot(fd);\n\t}\n'''
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
        "fs/open.c": (OPEN_MARKER, "a52_r257_kgsl_pub_snapshot(fd)", '"/dev/kgsl-3d0"'),
    }
    for rel, tokens in checks.items():
        text = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                raise RuntimeError(f"Phase257 verification {rel}: missing {token!r}")

    core = (root / "drivers/base/core.c").read_text(encoding="utf-8")
    if core.count("A52_PHASE256_KGSL_DEVNODE_UEVENT_V1") != 1:
        raise RuntimeError("Phase257 changed Phase256 KOBJ_ADD marker cardinality")
    forbidden = (
        "devtmpfs_" + "create_node(", "ksys_" + "mknod(", "do_" + "mknodat(",
        "security_inode_" + "mknod(", "CONFIG_DEVTMPFS" + "=y",
        "selinux_" + "enforcing=0",
    )
    source = Path(__file__).read_text(encoding="utf-8")
    for token in forbidden:
        if token in source:
            raise RuntimeError(f"Phase257 overlay contains forbidden behavior token {token!r}")


def self_test() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "drivers/base").mkdir(parents=True)
        (root / "drivers/a52_secure").mkdir(parents=True)
        (root / "fs").mkdir(parents=True)
        (root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c").write_text(
            '''if (strncmp(fmt, "F256", 4) &&\n    strncmp(fmt, "K255VIS", 7)) return;\nreturn !strncmp(message, "F256 ", 5) ||\n       !strncmp(message, "K255VIS ", 8);\n''', encoding="utf-8")
        (root / "fs/open.c").write_text(
            '''/* A52_PHASE225_KGSL_LATE_STATE */\nextern void a52_r225_kgsl_late_snapshot(int open_rc);\n\n'''
            '''if (trace)\n\ta52_ackfr_record("DRMPOST 212 path-ret n=%u fd=%d", trace_id, fd);\n'''
            '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0"))\n\t\ta52_r225_kgsl_late_snapshot(fd);\n''', encoding="utf-8")
        (root / "drivers/base/core.c").write_text(
            '''#include <linux/device.h>\n#include <linux/a52_ack_forensic.h>\n\n'''
            '''static int dev_uevent(struct kset *kset, struct kobject *kobj, struct kobj_uevent_env *env)\n{\n'''
            '''\tstruct device *dev = kobj_to_dev(kobj);\n\tconst char *tmp;\n\tconst char *name;\n\tumode_t mode = 0;\n\tkuid_t uid = GLOBAL_ROOT_UID;\n\tkgid_t gid = GLOBAL_ROOT_GID;\n'''
            '''\tname = device_get_devnode(dev, &mode, &uid, &gid, &tmp);\n\tif (name) add_uevent_var(env, "DEVNAME=%s", name);\n\treturn 0;\n}\n\n'''
            '''static ssize_t uevent_store(struct device *dev, struct device_attribute *attr,\n'''
            '''\t\t\t   const char *buf, size_t count)\n{\n\tint rc;\n\n'''
            '''\trc = kobject_synth_uevent(&dev->kobj, buf, count);\n\n\tif (rc) return rc;\n\treturn count;\n}\n\n'''
            '''int device_add(struct device *dev)\n{\n'''
            '''\t/* A52_PHASE256_KGSL_DEVNODE_UEVENT_V1 */\n\tif (!strcmp(dev_name(dev), "kgsl-3d0")) {\n\t\tint a52_r256_uevent_rc;\n\n'''
            '''\t\ta52_ackfr_record("F256 da n=%.16s M=%u m=%u",\n\t\t\tdev_name(dev), MAJOR(dev->devt), MINOR(dev->devt));\n'''
            '''\t\ta52_r256_uevent_rc = kobject_uevent(&dev->kobj, KOBJ_ADD);\n'''
            '''\t\ta52_ackfr_record("F256 ue n=%.16s rc=%d",\n\t\t\tdev_name(dev), a52_r256_uevent_rc);\n'''
            '''\t} else {\n\t\tkobject_uevent(&dev->kobj, KOBJ_ADD);\n\t}\n\treturn 0;\n}\n''', encoding="utf-8")
        patch_recorder(root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c")
        patch_core(root / "drivers/base/core.c")
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
    patch_open(root / "fs/open.c")
    verify(root)
    print(f"{MARKER}: KGSL initial publication + coldboot replay recorder applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
