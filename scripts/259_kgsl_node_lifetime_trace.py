#!/usr/bin/env python3
"""Phase 259: KGSL /dev node lifetime and mount-namespace trace.

Phase258 proved that removing the old global namei userspace-path probe restores
normal Phase256 boot progression once CONFIG_CHR_DEV_SG parity is repaired.
Hardware still shows ueventd coldboot publication succeeding while the first
SurfaceFlinger open of /dev/kgsl-3d0 returns -ENOENT.

Phase259 traces only the kernel-resolved kgsl-3d0 dentry at VFS mknod/unlink/
rename boundaries, retains that state, and compares the /dev filesystem and mount
namespace at the later failed open. It never reads a userspace pathname and does
not create, remove, rename, remount, or otherwise change any node.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "258_phase257_no_namei_ab.py"
MARKER = "A52_PHASE259_KGSL_NODE_LIFETIME_V1"
REC_MARKER = "A52_PHASE259_RECORDER_V1"
NAMEI_MARKER = "A52_PHASE259_KERNEL_DENTRY_VFS_V1"
OPEN_MARKER = "A52_PHASE259_DEV_MOUNT_SNAPSHOT_V1"


def load_base():
    if not BASE.is_file():
        raise RuntimeError(f"missing Phase258 base overlay: {BASE}")
    spec = importlib.util.spec_from_file_location("a52_phase258_base_for_259", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Phase258 base overlay")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


p258 = load_base()
p257 = p258.p257


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def _function_from_start(text: str, start: int, label: str) -> tuple[int, int, str]:
    brace = text.find("{", start)
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace not found")
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1, text[start:i + 1]
    raise RuntimeError(f"{label}: closing brace not found")


def _find_function_re(text: str, pattern: str, label: str) -> tuple[int, int, str]:
    m = re.search(pattern, text, re.M)
    if not m:
        raise RuntimeError(f"{label}: signature not found")
    return _function_from_start(text, m.start(), label)


def patch_recorder(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if REC_MARKER in text:
        return
    fmt = 'if (strncmp(fmt, "F257", 4) &&\n'
    text = replace_once(
        text,
        fmt,
        f'''/* {REC_MARKER}\n * Retain the Phase259 target-only KGSL node lifetime stream.\n */\nif (strncmp(fmt, "F259", 4) &&\n    strncmp(fmt, "F257", 4) &&\n''',
        f"{path}: F259 admission",
    )
    critical = 'return !strncmp(message, "F257 ", 5) ||\n'
    text = replace_once(
        text,
        critical,
        'return !strncmp(message, "F259 ", 5) ||\n       !strncmp(message, "F257 ", 5) ||\n',
        f"{path}: F259 retention",
    )
    path.write_text(text, encoding="utf-8")


STATE_BLOCK = r'''/* A52_PHASE259_KERNEL_DENTRY_VFS_V1
 * Target-only VFS observation. All names below are kernel-resolved dentries;
 * no userspace pathname is copied or dereferenced by this instrumentation.
 */
extern void a52_ackfr_record(const char *fmt, ...);

static atomic_t a52_r259_mk_count = ATOMIC_INIT(0);
static atomic_t a52_r259_ul_count = ATOMIC_INIT(0);
static atomic_t a52_r259_rn_count = ATOMIC_INIT(0);
static int a52_r259_mk_rc = -ENODATA;
static int a52_r259_mk_major;
static int a52_r259_mk_minor;
static unsigned int a52_r259_mk_sb_major;
static unsigned int a52_r259_mk_sb_minor;
static unsigned long a52_r259_mk_dir_ino;
static unsigned long a52_r259_mk_ns;
static u64 a52_r259_mk_ns_time;
static int a52_r259_ul_rc = -ENODATA;
static unsigned long a52_r259_ul_ns;
static u64 a52_r259_ul_ns_time;
static int a52_r259_rn_rc = -ENODATA;
static int a52_r259_rn_old;
static int a52_r259_rn_new;
static unsigned long a52_r259_rn_ns;
static u64 a52_r259_rn_ns_time;

static bool a52_r259_is_kgsl_dentry(const struct dentry *dentry)
{
	return dentry && dentry->d_name.len == 8 &&
		!memcmp(dentry->d_name.name, "kgsl-3d0", 8);
}

static unsigned long a52_r259_current_mnt_ns(void)
{
	return (unsigned long)(current->nsproxy ? current->nsproxy->mnt_ns : NULL);
}

static void a52_r259_mknod_begin(struct inode *dir, struct dentry *dentry,
		umode_t mode, dev_t dev)
{
	unsigned int n;

	if (!a52_r259_is_kgsl_dentry(dentry))
		return;
	n = atomic_inc_return(&a52_r259_mk_count);
	WRITE_ONCE(a52_r259_mk_rc, -EINPROGRESS);
	WRITE_ONCE(a52_r259_mk_major, MAJOR(dev));
	WRITE_ONCE(a52_r259_mk_minor, MINOR(dev));
	WRITE_ONCE(a52_r259_mk_sb_major, MAJOR(dir->i_sb->s_dev));
	WRITE_ONCE(a52_r259_mk_sb_minor, MINOR(dir->i_sb->s_dev));
	WRITE_ONCE(a52_r259_mk_dir_ino, dir->i_ino);
	WRITE_ONCE(a52_r259_mk_ns, a52_r259_current_mnt_ns());
	WRITE_ONCE(a52_r259_mk_ns_time, ktime_get_ns());
	a52_ackfr_record("F259 mkb n=%u p=%d c=%.15s mo=%o M=%u m=%u sM=%u sm=%u",
		n, current->pid, current->comm, mode, MAJOR(dev), MINOR(dev),
		MAJOR(dir->i_sb->s_dev), MINOR(dir->i_sb->s_dev));
}

static void a52_r259_mknod_end(struct inode *dir, struct dentry *dentry,
		int rc, umode_t mode, dev_t dev)
{
	if (!a52_r259_is_kgsl_dentry(dentry))
		return;
	WRITE_ONCE(a52_r259_mk_rc, rc);
	WRITE_ONCE(a52_r259_mk_major, MAJOR(dev));
	WRITE_ONCE(a52_r259_mk_minor, MINOR(dev));
	WRITE_ONCE(a52_r259_mk_sb_major, MAJOR(dir->i_sb->s_dev));
	WRITE_ONCE(a52_r259_mk_sb_minor, MINOR(dir->i_sb->s_dev));
	WRITE_ONCE(a52_r259_mk_dir_ino, dir->i_ino);
	WRITE_ONCE(a52_r259_mk_ns, a52_r259_current_mnt_ns());
	WRITE_ONCE(a52_r259_mk_ns_time, ktime_get_ns());
	a52_ackfr_record("F259 mkx rc=%d p=%d c=%.15s M=%u m=%u ino=%lu ns=%lx",
		rc, current->pid, current->comm, MAJOR(dev), MINOR(dev),
		dir->i_ino, a52_r259_current_mnt_ns());
}

static void a52_r259_unlink_begin(struct inode *dir, struct dentry *dentry,
		int initial_rc)
{
	unsigned int n;

	if (!a52_r259_is_kgsl_dentry(dentry))
		return;
	n = atomic_inc_return(&a52_r259_ul_count);
	WRITE_ONCE(a52_r259_ul_rc, initial_rc ? initial_rc : -EINPROGRESS);
	WRITE_ONCE(a52_r259_ul_ns, a52_r259_current_mnt_ns());
	WRITE_ONCE(a52_r259_ul_ns_time, ktime_get_ns());
	a52_ackfr_record("F259 ulb n=%u e=%d p=%d c=%.15s ino=%lu ns=%lx",
		n, initial_rc, current->pid, current->comm, dir->i_ino,
		a52_r259_current_mnt_ns());
}

static void a52_r259_unlink_end(struct dentry *dentry, int rc)
{
	if (!a52_r259_is_kgsl_dentry(dentry))
		return;
	WRITE_ONCE(a52_r259_ul_rc, rc);
	WRITE_ONCE(a52_r259_ul_ns, a52_r259_current_mnt_ns());
	WRITE_ONCE(a52_r259_ul_ns_time, ktime_get_ns());
	a52_ackfr_record("F259 ulx rc=%d p=%d c=%.15s ns=%lx",
		rc, current->pid, current->comm, a52_r259_current_mnt_ns());
}

static void a52_r259_rename_begin(struct dentry *old_dentry,
		struct dentry *new_dentry)
{
	int old_hit = a52_r259_is_kgsl_dentry(old_dentry) ? 1 : 0;
	int new_hit = a52_r259_is_kgsl_dentry(new_dentry) ? 1 : 0;
	unsigned int n;

	if (!old_hit && !new_hit)
		return;
	n = atomic_inc_return(&a52_r259_rn_count);
	WRITE_ONCE(a52_r259_rn_rc, -EINPROGRESS);
	WRITE_ONCE(a52_r259_rn_old, old_hit);
	WRITE_ONCE(a52_r259_rn_new, new_hit);
	WRITE_ONCE(a52_r259_rn_ns, a52_r259_current_mnt_ns());
	WRITE_ONCE(a52_r259_rn_ns_time, ktime_get_ns());
	a52_ackfr_record("F259 rnb n=%u o=%d d=%d p=%d c=%.15s ns=%lx",
		n, old_hit, new_hit, current->pid, current->comm,
		a52_r259_current_mnt_ns());
}

static void a52_r259_rename_end(struct dentry *old_dentry,
		struct dentry *new_dentry, int rc)
{
	if (!a52_r259_is_kgsl_dentry(old_dentry) &&
	    !a52_r259_is_kgsl_dentry(new_dentry))
		return;
	WRITE_ONCE(a52_r259_rn_rc, rc);
	WRITE_ONCE(a52_r259_rn_ns, a52_r259_current_mnt_ns());
	WRITE_ONCE(a52_r259_rn_ns_time, ktime_get_ns());
	a52_ackfr_record("F259 rnx rc=%d o=%d d=%d p=%d c=%.15s ns=%lx",
		rc, a52_r259_is_kgsl_dentry(old_dentry) ? 1 : 0,
		a52_r259_is_kgsl_dentry(new_dentry) ? 1 : 0,
		current->pid, current->comm, a52_r259_current_mnt_ns());
}

void a52_r259_kgsl_vfs_snapshot(void)
{
	a52_ackfr_record("F259 v1 kc=%d kr=%d M=%d m=%d sM=%u sm=%u ino=%lu",
		atomic_read(&a52_r259_mk_count), READ_ONCE(a52_r259_mk_rc),
		READ_ONCE(a52_r259_mk_major), READ_ONCE(a52_r259_mk_minor),
		READ_ONCE(a52_r259_mk_sb_major), READ_ONCE(a52_r259_mk_sb_minor),
		READ_ONCE(a52_r259_mk_dir_ino));
	a52_ackfr_record("F259 v2 kns=%lx kt=%llu uc=%d ur=%d uns=%lx ut=%llu",
		READ_ONCE(a52_r259_mk_ns),
		(unsigned long long)(READ_ONCE(a52_r259_mk_ns_time) / 1000000ULL),
		atomic_read(&a52_r259_ul_count), READ_ONCE(a52_r259_ul_rc),
		READ_ONCE(a52_r259_ul_ns),
		(unsigned long long)(READ_ONCE(a52_r259_ul_ns_time) / 1000000ULL));
	a52_ackfr_record("F259 v3 rc=%d rr=%d ro=%d rn=%d rns=%lx rt=%llu",
		atomic_read(&a52_r259_rn_count), READ_ONCE(a52_r259_rn_rc),
		READ_ONCE(a52_r259_rn_old), READ_ONCE(a52_r259_rn_new),
		READ_ONCE(a52_r259_rn_ns),
		(unsigned long long)(READ_ONCE(a52_r259_rn_ns_time) / 1000000ULL));
}

'''


def patch_namei(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if NAMEI_MARKER in text:
        return
    if "strncpy_from_user(path, name" in text or "a52_r257_kgsl_user_node_event" in text:
        raise RuntimeError(f"{path}: old live Phase257 userspace pathname probe returned")

    for inc in ("#include <linux/ktime.h>", "#include <linux/nsproxy.h>"):
        if inc not in text:
            anchor = '#include "internal.h"\n'
            if text.count(anchor) != 1:
                raise RuntimeError(f"{path}: internal.h include anchor drifted")
            text = text.replace(anchor, inc + "\n\n" + anchor, 1)

    anchor = "static int may_mknod(umode_t mode)\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: may_mknod anchor count {text.count(anchor)}")
    text = text.replace(anchor, STATE_BLOCK + anchor, 1)

    # Android 5.10 vfs_mknod: record resolved dentry before validation and the
    # final result after the filesystem mknod path. No user pointer is touched.
    start, end, fn = _find_function_re(
        text,
        r"(?m)^int\s+vfs_mknod\s*\(struct inode \*dir, struct dentry \*dentry,\s*umode_t mode, dev_t dev\)",
        f"{path}: vfs_mknod",
    )
    # Insert only after the local declaration/initialization so we do not
    # create a declaration-after-statement warning/error in kernel C.
    init = "\tint error = may_create(dir, dentry);\n"
    if fn.count(init) != 1:
        raise RuntimeError(f"{path}: vfs_mknod may_create anchor count {fn.count(init)}")
    fn = fn.replace(init, init + "\ta52_r259_mknod_begin(dir, dentry, mode, dev);\n", 1)
    tail = "\tif (!error)\n\t\tfsnotify_create(dir, dentry);\n\treturn error;"
    if fn.count(tail) != 1:
        raise RuntimeError(f"{path}: vfs_mknod final tail count {fn.count(tail)}")
    fn = fn.replace(
        tail,
        "\tif (!error)\n\t\tfsnotify_create(dir, dentry);\n"
        "\ta52_r259_mknod_end(dir, dentry, error, mode, dev);\n"
        "\treturn error;",
        1,
    )
    text = text[:start] + fn + text[end:]

    # Android 5.10 vfs_unlink: a successful removal necessarily reaches the
    # common tail, so begin+tail observation is sufficient to prove deletion.
    start, end, fn = _find_function_re(
        text,
        r"(?m)^int\s+vfs_unlink\s*\(struct inode \*dir, struct dentry \*dentry, struct inode \*\*delegated_inode\)",
        f"{path}: vfs_unlink",
    )
    init = "\tint error = may_delete(dir, dentry, 0);\n"
    if fn.count(init) != 1:
        raise RuntimeError(f"{path}: vfs_unlink may_delete anchor count {fn.count(init)}")
    fn = fn.replace(init, init + "\ta52_r259_unlink_begin(dir, dentry, error);\n", 1)
    tail_matches = list(re.finditer(r"(?m)^\treturn error;\n}$", fn))
    if len(tail_matches) != 1:
        raise RuntimeError(f"{path}: vfs_unlink common return count {len(tail_matches)}")
    m = tail_matches[0]
    fn = fn[:m.start()] + "\ta52_r259_unlink_end(dentry, error);\n\treturn error;\n}" + fn[m.end():]
    text = text[:start] + fn + text[end:]

    # Android 5.10 vfs_rename has resolved old/new dentries. Record only if
    # either side is kgsl-3d0; successful moves reach the common tail.
    start, end, fn = _find_function_re(
        text,
        r"(?m)^int\s+vfs_rename\s*\(struct inode \*old_dir, struct dentry \*old_dentry,\s*\n\s*struct inode \*new_dir, struct dentry \*new_dentry,",
        f"{path}: vfs_rename",
    )
    # On Android 5.10 the first executable statement follows the declaration
    # block with this same-object fast path. Insert immediately before it.
    first_stmt = "\tif (source == target)\n"
    if fn.count(first_stmt) != 1:
        raise RuntimeError(f"{path}: vfs_rename first-statement anchor count {fn.count(first_stmt)}")
    fn = fn.replace(first_stmt, "\ta52_r259_rename_begin(old_dentry, new_dentry);\n" + first_stmt, 1)
    tail_matches = list(re.finditer(r"(?m)^\treturn error;\n}$", fn))
    if len(tail_matches) != 1:
        raise RuntimeError(f"{path}: vfs_rename common return count {len(tail_matches)}")
    m = tail_matches[0]
    fn = fn[:m.start()] + "\ta52_r259_rename_end(old_dentry, new_dentry, error);\n\treturn error;\n}" + fn[m.end():]
    text = text[:start] + fn + text[end:]

    path.write_text(text, encoding="utf-8")


OPEN_HELPER = r'''/* A52_PHASE259_DEV_MOUNT_SNAPSHOT_V1 */
extern void a52_r259_kgsl_vfs_snapshot(void);

static void a52_r259_kgsl_open_mount_snapshot(int open_rc)
{
	struct path dev_path;
	struct path node_path;
	int dev_rc;
	int node_rc;
	unsigned int sb_major = 0;
	unsigned int sb_minor = 0;
	unsigned long dev_ino = 0;
	unsigned long ns = (unsigned long)(current->nsproxy ?
		current->nsproxy->mnt_ns : NULL);

	dev_rc = kern_path("/dev", LOOKUP_FOLLOW, &dev_path);
	if (!dev_rc) {
		sb_major = MAJOR(dev_path.dentry->d_sb->s_dev);
		sb_minor = MINOR(dev_path.dentry->d_sb->s_dev);
		if (d_inode(dev_path.dentry))
			dev_ino = d_inode(dev_path.dentry)->i_ino;
	}
	node_rc = kern_path("/dev/kgsl-3d0", LOOKUP_FOLLOW, &node_path);
	a52_ackfr_record("F259 op o=%d dr=%d nr=%d sM=%u sm=%u ino=%lu ns=%lx",
		open_rc, dev_rc, node_rc, sb_major, sb_minor, dev_ino, ns);
	if (!node_rc) {
		struct inode *inode = d_inode(node_path.dentry);
		a52_ackfr_record("F259 on mo=%o M=%u m=%u ino=%lu sM=%u sm=%u",
			inode ? inode->i_mode : 0,
			inode ? MAJOR(inode->i_rdev) : 0,
			inode ? MINOR(inode->i_rdev) : 0,
			inode ? inode->i_ino : 0,
			MAJOR(node_path.dentry->d_sb->s_dev),
			MINOR(node_path.dentry->d_sb->s_dev));
		path_put(&node_path);
	}
	if (!dev_rc)
		path_put(&dev_path);
}

'''


def patch_open(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if OPEN_MARKER in text:
        return
    if "#include <linux/nsproxy.h>" not in text:
        include_hits = list(re.finditer(r"(?m)^#include <linux/[^>]+>\s*$", text))
        if not include_hits:
            raise RuntimeError(f"{path}: linux include anchor missing")
        pos = include_hits[-1].end()
        text = text[:pos] + "\n#include <linux/nsproxy.h>" + text[pos:]

    decl = "extern void a52_r257_kgsl_pub_snapshot(int open_rc);\n"
    if text.count(decl) != 1:
        raise RuntimeError(f"{path}: Phase258 publication declaration count {text.count(decl)}")
    text = text.replace(decl, decl + OPEN_HELPER, 1)

    old = '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0")) {
\t\ta52_r225_kgsl_late_snapshot(fd);
\t\ta52_r257_kgsl_pub_snapshot(fd);
\t}
'''
    new = '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0")) {
\t\ta52_r225_kgsl_late_snapshot(fd);
\t\ta52_r257_kgsl_pub_snapshot(fd);
\t\ta52_r259_kgsl_vfs_snapshot();
\t\ta52_r259_kgsl_open_mount_snapshot(fd);
\t}
'''
    text = replace_once(text, old, new, f"{path}: Phase259 late snapshot call")
    path.write_text(text, encoding="utf-8")


def verify(root: Path) -> None:
    rec = (root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c").read_text(encoding="utf-8")
    namei = (root / "fs/namei.c").read_text(encoding="utf-8")
    openc = (root / "fs/open.c").read_text(encoding="utf-8")

    for token in (REC_MARKER, 'strncmp(fmt, "F259", 4)', '!strncmp(message, "F259 ", 5)'):
        if token not in rec:
            raise RuntimeError(f"Phase259 recorder missing {token!r}")
    for token in (
        NAMEI_MARKER,
        "a52_r259_mknod_begin",
        "a52_r259_mknod_end",
        "a52_r259_unlink_begin",
        "a52_r259_unlink_end",
        "a52_r259_rename_begin",
        "a52_r259_rename_end",
        "a52_r259_kgsl_vfs_snapshot",
        "F259 mkb",
        "F259 mkx",
        "F259 ulb",
        "F259 ulx",
        "F259 rnb",
        "F259 rnx",
        "F259 v1",
        "F259 v2",
        "F259 v3",
    ):
        if token not in namei:
            raise RuntimeError(f"Phase259 namei missing {token!r}")
    for token in (OPEN_MARKER, "a52_r259_kgsl_vfs_snapshot()", "F259 op", "F259 on"):
        if token not in openc:
            raise RuntimeError(f"Phase259 open missing {token!r}")

    # The entire point of Phase259 is to avoid the perturbing Phase257 pattern.
    for forbidden in (
        "a52_r257_kgsl_user_node_event",
        "strncpy_from_user(path, name",
    ):
        if forbidden in namei:
            raise RuntimeError(f"Phase259 reintroduced forbidden userspace pathname read: {forbidden}")
    if "a52_r257_kgsl_node_snapshot" in openc:
        raise RuntimeError("Phase259 reintroduced removed Phase257 node snapshot")


def self_test() -> None:
    # Preserve Phase258's own no-namei + SG parity fixture test first.
    p258.self_test()

    # Structural source audit: the Phase259 implementation itself must never
    # contain a userspace pathname copy helper or reference strncpy_from_user.
    source = Path(__file__).read_text(encoding="utf-8")
    required = (
        MARKER,
        NAMEI_MARKER,
        OPEN_MARKER,
        "vfs_mknod",
        "vfs_unlink",
        "vfs_rename",
        "a52_r259_kgsl_vfs_snapshot",
        "CONFIG_CHR_DEV_SG",
    )
    # CONFIG_CHR_DEV_SG lives in the Phase258 base invoked below; check the
    # imported base source rather than duplicating config mutation here.
    for token in required[:-1]:
        if token not in source:
            raise AssertionError(f"Phase259 source missing {token}")
    base_source = BASE.read_text(encoding="utf-8")
    if "CONFIG_CHR_DEV_SG=y" not in base_source:
        raise AssertionError("Phase259 base lost corrected SG parity restore")
    print("Phase 259 KGSL target-only VFS lifetime trace self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    root = p257.locate(sys.argv[1:])

    # Materialize the exact corrected Phase258 control first.
    p258.restore_phase221_sg_config()
    p257.patch_recorder(root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c")
    p257.patch_core(root / "drivers/base/core.c")
    p258.patch_namei_inert_audit(root / "fs/namei.c")
    p258.patch_open_no_namei(root / "fs/open.c")
    p258.verify(root)

    patch_recorder(root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c")
    patch_namei(root / "fs/namei.c")
    patch_open(root / "fs/open.c")
    verify(root)

    print(
        f"{MARKER}: corrected Phase258 baseline retained; target-only kernel-dentry "
        "mknod/unlink/rename lifetime + /dev mount-namespace snapshots enabled; "
        "no userspace pathname reread",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
