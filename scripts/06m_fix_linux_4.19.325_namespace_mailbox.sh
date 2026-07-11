#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/namespace-mailbox-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before namespace/mailbox repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))


namespace = root / "fs/namespace.c"

# Linux 4.19.325 moved has_locked_children() before clone_private_mount().
# Keep that ordering, but preserve Samsung's CONFIG_KDP_NS mount layout.
replace_once(
    namespace,
    "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
    "{\n"
    "\tstruct mount *child;\n"
    "\n"
    "\tlist_for_each_entry(child, &mnt->mnt_mounts, mnt_child) {\n"
    "\t\tif (!is_subdir(child->mnt_mountpoint, dentry))\n"
    "\t\t\tcontinue;\n"
    "\n"
    "\t\tif (child->mnt.mnt_flags & MNT_LOCKED)\n"
    "\t\t\treturn true;\n"
    "\t}\n"
    "\treturn false;\n"
    "}\n",
    "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
    "{\n"
    "\tstruct mount *child;\n"
    "\n"
    "\tlist_for_each_entry(child, &mnt->mnt_mounts, mnt_child) {\n"
    "\t\tif (!is_subdir(child->mnt_mountpoint, dentry))\n"
    "\t\t\tcontinue;\n"
    "\n"
    "#ifdef CONFIG_KDP_NS\n"
    "\t\tif (child->mnt->mnt_flags & MNT_LOCKED)\n"
    "#else\n"
    "\t\tif (child->mnt.mnt_flags & MNT_LOCKED)\n"
    "#endif\n"
    "\t\t\treturn true;\n"
    "\t}\n"
    "\treturn false;\n"
    "}\n",
    "KDP-aware locked-child helper",
)

# The direct merge retained the new gotos but lost the upstream cleanup label.
replace_once(
    namespace,
    "#ifdef CONFIG_KDP_NS\n"
    "\treturn new_mnt->mnt;\n"
    "#else\n"
    "\treturn &new_mnt->mnt;\n"
    "#endif\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(clone_private_mount);\n",
    "#ifdef CONFIG_KDP_NS\n"
    "\treturn new_mnt->mnt;\n"
    "#else\n"
    "\treturn &new_mnt->mnt;\n"
    "#endif\n"
    "\n"
    "invalid:\n"
    "\tup_read(&namespace_sem);\n"
    "\treturn ERR_PTR(-EINVAL);\n"
    "}\n"
    "EXPORT_SYMBOL_GPL(clone_private_mount);\n",
    "clone_private_mount invalid cleanup",
)

# Remove the obsolete second copy that remained in the Samsung source location.
replace_once(
    namespace,
    "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
    "{\n"
    "\tstruct mount *child;\n"
    "\tlist_for_each_entry(child, &mnt->mnt_mounts, mnt_child) {\n"
    "\t\tif (!is_subdir(child->mnt_mountpoint, dentry))\n"
    "\t\t\tcontinue;\n"
    "\n"
    "#ifdef CONFIG_KDP_NS\n"
    "\t\tif (child->mnt->mnt_flags & MNT_LOCKED)\n"
    "#else\n"
    "\t\tif (child->mnt.mnt_flags & MNT_LOCKED)\n"
    "#endif\n"
    "\t\t\treturn true;\n"
    "\t}\n"
    "\treturn false;\n"
    "}\n"
    "\n"
    "/*\n"
    " * do loopback mount.\n"
    " */\n",
    "/*\n"
    " * do loopback mount.\n"
    " */\n",
    "duplicate locked-child helper",
)

ns_text = namespace.read_text()
if ns_text.count("static bool has_locked_children(") != 1:
    raise SystemExit("namespace repair did not leave exactly one has_locked_children helper")
clone_start = ns_text.index("struct vfsmount *clone_private_mount")
clone_end = ns_text.index("EXPORT_SYMBOL_GPL(clone_private_mount);", clone_start)
if "invalid:\n\tup_read(&namespace_sem);\n\treturn ERR_PTR(-EINVAL);" not in ns_text[clone_start:clone_end]:
    raise SystemExit("clone_private_mount cleanup label is missing after repair")

mailbox = root / "drivers/mailbox/mailbox.c"

# The vendor tree split the locked queue operation into __msg_submit(), while
# 4.19.325 also needs a separate irqsave variable around poll_hrt_lock here.
replace_once(
    mailbox,
    "static void msg_submit(struct mbox_chan *chan)\n"
    "{\n"
    "\tint err = 0;\n",
    "static void msg_submit(struct mbox_chan *chan)\n"
    "{\n"
    "\tunsigned long flags;\n"
    "\tint err = 0;\n",
    "mailbox poll timer irq flags",
)

mailbox_text = mailbox.read_text()
submit_start = mailbox_text.index("static void msg_submit(struct mbox_chan *chan)")
submit_end = mailbox_text.index("static void tx_tick", submit_start)
submit = mailbox_text[submit_start:submit_end]
if "unsigned long flags;" not in submit:
    raise SystemExit("msg_submit flags declaration is missing after repair")
if "spin_lock_irqsave(&chan->mbox->poll_hrt_lock, flags);" not in submit:
    raise SystemExit("msg_submit poll timer lock is missing after repair")
PY

grep -Fq 'invalid:' "$KERNEL_DIR/fs/namespace.c" \
  || fail "clone_private_mount invalid label is missing"
test "$(grep -c '^static bool has_locked_children' "$KERNEL_DIR/fs/namespace.c")" = 1 \
  || fail "fs/namespace.c must contain exactly one has_locked_children definition"
grep -A4 -F 'static void msg_submit(struct mbox_chan *chan)' "$KERNEL_DIR/drivers/mailbox/mailbox.c" \
  | grep -Fq 'unsigned long flags;' \
  || fail "Mailbox irq flags declaration is missing"
git -C "$KERNEL_DIR" diff --check

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'namespace=restored-invalid-cleanup-and-single-kdp-aware-helper\n'
  printf 'mailbox=declared-poll-hrtimer-irqsave-flags\n'
  printf 'result=linux-4.19.325-namespace-mailbox-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION namespace and mailbox compatibility repaired"
