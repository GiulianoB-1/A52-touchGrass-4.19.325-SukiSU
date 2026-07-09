#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
FILE_WRAPPER_C="$SUKISU_DIR/kernel/infra/file_wrapper.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-file-wrapper.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-file-wrapper.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before file-wrapper compatibility patch"
test -f "$FILE_WRAPPER_C" || fail "SukiSU file_wrapper.c is missing"
test "$(git -C "$SUKISU_DIR" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"

grep -Fq 'CONFIG_SECURITY_SELINUX=y' "$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig" || fail "SELinux is not enabled in the device defconfig"
! grep -Fq '(*iopoll)' "$KERNEL_DIR/include/linux/fs.h" || fail "Kernel unexpectedly has file_operations.iopoll; review patch"
! grep -Fq '(*remap_file_range)' "$KERNEL_DIR/include/linux/fs.h" || fail "Kernel unexpectedly has file_operations.remap_file_range; review patch"
grep -Fq '(*copy_file_range)' "$KERNEL_DIR/include/linux/fs.h" || fail "Kernel file_operations.copy_file_range is missing"
! grep -Fq 'security_inode_init_security_anon' "$KERNEL_DIR/include/linux/security.h" || fail "Kernel unexpectedly has security_inode_init_security_anon; review patch"
grep -Fq 'i_security;' "$KERNEL_DIR/include/linux/fs.h" || fail "Kernel inode security pointer is missing"
grep -Fq 'struct inode_security_struct' "$KERNEL_DIR/security/selinux/include/objsec.h" || fail "SELinux inode security structure is missing"
grep -Fq 'LABEL_INITIALIZED' "$KERNEL_DIR/security/selinux/include/objsec.h" || fail "SELinux initialized-label state is missing"
grep -Fq '{ "file",' "$KERNEL_DIR/security/selinux/include/classmap.h" || fail "SELinux file class source mapping is missing"
grep -RFn 'ksu_file_sid' "$SUKISU_DIR/kernel/selinux" >/dev/null || fail "SukiSU file SID is missing"

info "Adapting SukiSU file wrapper to the exact Linux 4.19 VFS and SELinux layout"
python3 - "$FILE_WRAPPER_C" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


iopoll_block = '''#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 1, 0)
static int ksu_wrapper_iopoll(struct kiocb *kiocb, struct io_comp_batch *icb, unsigned int v)
{
    struct ksu_file_wrapper *data = kiocb->ki_filp->private_data;
    struct file *orig = data->orig;
    kiocb->ki_filp = orig;
    return orig->f_op->iopoll(kiocb, icb, v);
}
#else
static int ksu_wrapper_iopoll(struct kiocb *kiocb, bool spin)
{
    struct ksu_file_wrapper *data = kiocb->ki_filp->private_data;
    struct file *orig = data->orig;
    kiocb->ki_filp = orig;
    return orig->f_op->iopoll(kiocb, spin);
}
#endif

'''
replace_once(
    iopoll_block,
    '/* Linux 4.19 has no iopoll file-operation member. */\n\n',
    'file_wrapper.c iopoll block',
)

remap_block = '''// no REMAP_FILE_DEDUP: use file_in
// https://cs.android.com/android/kernel/superproject/+/common-android-mainline:common/fs/read_write.c;l=1598-1599;drc=398da7defe218d3e51b0f3bdff75147e28125b60
// https://cs.android.com/android/kernel/superproject/+/common-android-mainline:common/fs/remap_range.c;l=403-404;drc=398da7defe218d3e51b0f3bdff75147e28125b60
// REMAP_FILE_DEDUP: use file_out
// https://cs.android.com/android/kernel/superproject/+/common-android-mainline:common/fs/remap_range.c;l=483-484;drc=398da7defe218d3e51b0f3bdff75147e28125b60
static loff_t ksu_wrapper_remap_file_range(struct file *file_in, loff_t pos_in, struct file *file_out, loff_t pos_out,
                                           loff_t len, unsigned int remap_flags)
{
    if (remap_flags & REMAP_FILE_DEDUP) {
        struct ksu_file_wrapper *data = file_out->private_data;
        struct file *orig = data->orig;
        return orig->f_op->remap_file_range(file_in, pos_in, orig, pos_out, len, remap_flags);
    } else {
        struct ksu_file_wrapper *data = file_in->private_data;
        struct file *orig = data->orig;
        return orig->f_op->remap_file_range(orig, pos_in, file_out, pos_out, len, remap_flags);
    }
}

'''
replace_once(
    remap_block,
    '/* Linux 4.19 uses separate clone and dedupe operations. */\n\n',
    'file_wrapper.c remap_file_range block',
)

replace_once(
    '    p->ops.iopoll = fp->f_op->iopoll ? ksu_wrapper_iopoll : NULL;\n',
    '',
    'file_wrapper.c iopoll assignment',
)
replace_once(
    '    p->ops.remap_file_range = fp->f_op->remap_file_range ? ksu_wrapper_remap_file_range : NULL;\n',
    '',
    'file_wrapper.c remap_file_range assignment',
)

replace_once(
    '    const struct qstr qname = QSTR_INIT(name, strlen(name));\n    int error;\n\n',
    '',
    'file_wrapper.c unused new-security declarations',
)

new_security_call = '''    inode->i_flags &= ~S_PRIVATE;
    error = security_inode_init_security_anon(inode, &qname, context_inode);
    if (error) {
        iput(inode);
        return ERR_PTR(error);
    }
'''
old_security_init = '''    inode->i_flags &= ~S_PRIVATE;
    {
        struct inode_security_struct *isec =
            (struct inode_security_struct *)inode->i_security;

        if (!isec) {
            iput(inode);
            return ERR_PTR(-EACCES);
        }

        /* The inode is not published yet, but use its lock for consistency. */
        spin_lock(&isec->lock);
        isec->sid = ksu_file_sid;
        isec->sclass = SECCLASS_FILE;
        isec->initialized = LABEL_INITIALIZED;
        spin_unlock(&isec->lock);
    }
'''
replace_once(
    new_security_call,
    old_security_init,
    'file_wrapper.c anonymous inode SELinux initialization',
)

old_sid_block = '''    struct inode_security_struct *wrapper_sec = selinux_inode(wrapper_inode);
    // Use ksu_file_sid to bypass SELinux check.
    // When we call `su` from terminal app, this is useful.
    if (wrapper_sec) {
        wrapper_sec->sid = ksu_file_sid;
    }
'''
new_sid_block = '''    struct inode_security_struct *wrapper_sec =
        (struct inode_security_struct *)wrapper_inode->i_security;
    // Use ksu_file_sid to bypass SELinux check.
    // When we call `su` from terminal app, this is useful.
    if (wrapper_sec) {
        spin_lock(&wrapper_sec->lock);
        wrapper_sec->sid = ksu_file_sid;
        wrapper_sec->sclass = SECCLASS_FILE;
        wrapper_sec->initialized = LABEL_INITIALIZED;
        spin_unlock(&wrapper_sec->lock);
    }
'''
replace_once(old_sid_block, new_sid_block, 'file_wrapper.c direct old-SELinux SID assignment')

path.write_text(text)
PY

! grep -Fq 'ksu_wrapper_iopoll' "$FILE_WRAPPER_C" || fail "Unsupported iopoll wrapper remains"
! grep -Fq '->iopoll' "$FILE_WRAPPER_C" || fail "Unsupported iopoll dereference remains"
! grep -Fq '.iopoll =' "$FILE_WRAPPER_C" || fail "Unsupported iopoll assignment remains"
! grep -Fq 'ksu_wrapper_remap_file_range' "$FILE_WRAPPER_C" || fail "Unsupported remap wrapper remains"
! grep -Fq '->remap_file_range' "$FILE_WRAPPER_C" || fail "Unsupported remap dereference remains"
! grep -Fq '.remap_file_range =' "$FILE_WRAPPER_C" || fail "Unsupported remap assignment remains"
! grep -Fq 'REMAP_FILE_DEDUP' "$FILE_WRAPPER_C" || fail "Unsupported remap flag remains"
! grep -Fq 'security_inode_init_security_anon' "$FILE_WRAPPER_C" || fail "Unsupported anonymous-inode security hook remains"
! grep -Fq 'selinux_inode(' "$FILE_WRAPPER_C" || fail "Unavailable selinux_inode helper remains"
grep -Fq '(struct inode_security_struct *)inode->i_security' "$FILE_WRAPPER_C" || fail "Direct anonymous-inode security blob access is missing"
grep -Fq '(struct inode_security_struct *)wrapper_inode->i_security' "$FILE_WRAPPER_C" || fail "Direct wrapper-inode security blob access is missing"
test "$(grep -Fc 'initialized = LABEL_INITIALIZED;' "$FILE_WRAPPER_C")" -eq 2 || fail "Expected two initialized SELinux label assignments"
test "$(grep -Fc 'sclass = SECCLASS_FILE;' "$FILE_WRAPPER_C")" -eq 2 || fail "Expected two SELinux file-class assignments"

git -C "$SUKISU_DIR" diff --check
git -C "$SUKISU_DIR" diff --binary -- kernel/infra/file_wrapper.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "SukiSU Linux 4.19 file-wrapper patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'file_operations_iopoll=omitted-not-present-in-4.19\n'
  printf 'file_operations_remap_file_range=omitted-not-present-in-4.19\n'
  printf 'copy_file_range=preserved\n'
  printf 'old_selinux_inode_blob=direct-locked-initialization\n'
  printf 'wrapper_sid=ksu_file_sid\n'
  printf 'wrapper_sclass=SECCLASS_FILE\n'
  printf 'wrapper_label_state=LABEL_INITIALIZED\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "SukiSU Linux 4.19 file-wrapper compatibility patch applied"
