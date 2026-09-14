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
text = namespace.read_text()

# 4.19.325 moved has_locked_children() before clone_private_mount() and made
# clone_private_mount() reject unbindable, detached, or locked-child mounts
# while holding namespace_sem. Samsung's CONFIG_KDP_NS changes struct mount's
# vfsmount storage, so preserve that access pattern while adopting the stable
# ordering and validation semantics.

def extract_function(text: str, signature: str):
    start = text.find(signature)
    if start < 0:
        return None
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"malformed function for {signature}")
    depth = 0
    i = brace
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                return start, end, text[start:end]
        i += 1
    raise SystemExit(f"unterminated function for {signature}")


helper_sig = "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)"
helper_template = (
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
    "}\n"
)

# Remove every merged/vendor copy, then insert one canonical KDP-aware helper
# immediately before clone_private_mount().
removed = 0
while True:
    fn = extract_function(text, helper_sig)
    if fn is None:
        break
    start, end, _ = fn
    text = text[:start] + text[end:]
    removed += 1

clone_sig = "struct vfsmount *clone_private_mount(const struct path *path)"
clone_pos = text.find(clone_sig)
if clone_pos < 0:
    raise SystemExit("clone_private_mount is missing after stable merge")
text = text[:clone_pos] + helper_template + "\n" + text[clone_pos:]
repairs = [f"fs/namespace.c=canonicalized-kdp-locked-child-helper-from-{removed}-copies"]

# Replace clone_private_mount() as a complete unit. This avoids depending on
# whether diff3 preserved Samsung's old body, upstream's new body, or a clean
# mixture of both.
fn = extract_function(text, clone_sig)
if fn is None:
    raise SystemExit("unable to isolate clone_private_mount")
clone_start, clone_end, _ = fn
clone_template = (
    "struct vfsmount *clone_private_mount(const struct path *path)\n"
    "{\n"
    "\tstruct mount *old_mnt = real_mount(path->mnt);\n"
    "\tstruct mount *new_mnt;\n"
    "\n"
    "\tdown_read(&namespace_sem);\n"
    "\tif (IS_MNT_UNBINDABLE(old_mnt))\n"
    "\t\tgoto invalid;\n"
    "\n"
    "\tif (!check_mnt(old_mnt))\n"
    "\t\tgoto invalid;\n"
    "\n"
    "\tif (has_locked_children(old_mnt, path->dentry))\n"
    "\t\tgoto invalid;\n"
    "\n"
    "\tnew_mnt = clone_mnt(old_mnt, path->dentry, CL_PRIVATE);\n"
    "\tup_read(&namespace_sem);\n"
    "\n"
    "\tif (IS_ERR(new_mnt))\n"
    "\t\treturn ERR_CAST(new_mnt);\n"
    "\n"
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
)
text = text[:clone_start] + clone_template + text[clone_end:]
repairs.append("fs/namespace.c=adopted-4.19.325-clone-validation-with-kdp-return")

namespace.write_text(text)

ns_text = namespace.read_text()
if ns_text.count(helper_sig) != 1:
    raise SystemExit("namespace repair did not leave exactly one has_locked_children helper")
helper_pos = ns_text.index(helper_sig)
clone_start = ns_text.index(clone_sig)
clone_end = ns_text.index("EXPORT_SYMBOL_GPL(clone_private_mount);", clone_start)
if helper_pos > clone_start:
    raise SystemExit("has_locked_children must precede clone_private_mount")
clone_body = ns_text[clone_start:clone_end]
for fragment in (
    "down_read(&namespace_sem);",
    "if (!check_mnt(old_mnt))",
    "if (has_locked_children(old_mnt, path->dentry))",
    "invalid:\n\tup_read(&namespace_sem);\n\treturn ERR_PTR(-EINVAL);",
    "#ifdef CONFIG_KDP_NS\n\treturn new_mnt->mnt;",
):
    if fragment not in clone_body:
        raise SystemExit(f"clone_private_mount repair missing: {fragment}")
if "#ifdef CONFIG_KDP_NS\n\t\tif (child->mnt->mnt_flags & MNT_LOCKED)" not in ns_text:
    raise SystemExit("KDP-aware locked-child access is missing")

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
git -C "$KERNEL_DIR" diff --check -- fs/namespace.c drivers/mailbox/mailbox.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'namespace=4.19.325-clone-validation-plus-single-kdp-aware-helper\n'
  printf 'mailbox=declared-poll-hrtimer-irqsave-flags\n'
  printf 'result=linux-4.19.325-namespace-mailbox-compatibility-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION namespace and mailbox compatibility repaired"
