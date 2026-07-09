#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
RUNTIME_C="$SUKISU_DIR/kernel/runtime/ksud_integration.c"
SELINUX_C="$SUKISU_DIR/kernel/selinux/selinux.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-runtime-selinux.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-runtime-selinux.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before runtime/SELinux compatibility patch"
test -f "$RUNTIME_C" || fail "SukiSU ksud_integration.c is missing"
test -f "$SELINUX_C" || fail "SukiSU selinux.c is missing"
test "$(git -C "$SUKISU_DIR" rev-parse HEAD)" = "$SUKISU_COMMIT" || fail "SukiSU source is not at the pinned commit"

grep -Fq 'extern long strncpy_from_unsafe_user(char *dst, const void __user *unsafe_addr,' "$KERNEL_DIR/include/linux/uaccess.h" || fail "Legacy strncpy_from_unsafe_user helper is missing"
grep -Fq 'extern long probe_user_read(void *dst, const void __user *src, size_t size);' "$KERNEL_DIR/include/linux/uaccess.h" || fail "Legacy probe_user_read helper is missing"
grep -Fq 'extern long notrace probe_user_write(void __user *dst, const void *src, size_t size);' "$KERNEL_DIR/include/linux/uaccess.h" || fail "Legacy probe_user_write helper is missing"
! grep -RFn 'strncpy_from_user_nofault' "$KERNEL_DIR/include" "$KERNEL_DIR/mm" >/dev/null 2>&1 || fail "Kernel unexpectedly provides strncpy_from_user_nofault; review patch"
! grep -RFn 'copy_from_user_nofault' "$KERNEL_DIR/include" "$KERNEL_DIR/mm" >/dev/null 2>&1 || fail "Kernel unexpectedly provides copy_from_user_nofault; review patch"
! grep -RFn 'copy_to_user_nofault' "$KERNEL_DIR/include" "$KERNEL_DIR/mm" >/dev/null 2>&1 || fail "Kernel unexpectedly provides copy_to_user_nofault; review patch"
grep -Fq '*security;' "$KERNEL_DIR/include/linux/cred.h" || fail "Linux 4.19 cred security pointer is missing"
grep -Fq 'struct task_security_struct' "$KERNEL_DIR/security/selinux/include/objsec.h" || fail "Linux 4.19 SELinux task security structure is missing"

info "Adapting SukiSU runtime no-fault access and SELinux credential access to Linux 4.19"
python3 - "$RUNTIME_C" "$SELINUX_C" <<'PY'
from pathlib import Path
import sys

runtime_path = Path(sys.argv[1])
selinux_path = Path(sys.argv[2])


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


runtime = runtime_path.read_text()
runtime = replace_once(
    runtime,
    '    if (strncpy_from_user_nofault(buf, p, buf_len) <= 0)\n',
    '    if (strncpy_from_unsafe_user(buf, p, buf_len) <= 0)\n',
    "ksud_integration.c nofault string copy",
)
runtime = replace_once(
    runtime,
    '        if (!copy_from_user_nofault(&size, st_size_ptr, sizeof(long))) {\n',
    '        if (!probe_user_read(&size, st_size_ptr, sizeof(long))) {\n',
    "ksud_integration.c nofault user read",
)
runtime = replace_once(
    runtime,
    '            if (!copy_to_user_nofault(st_size_ptr, &new_size, sizeof(long))) {\n',
    '            if (!probe_user_write(st_size_ptr, &new_size, sizeof(long))) {\n',
    "ksud_integration.c nofault user write",
)
runtime_path.write_text(runtime)

selinux = selinux_path.read_text()
selinux = replace_once(
    selinux,
    '    tsec = selinux_cred(cred);\n',
    '    tsec = (struct task_security_struct *)cred->security; /* Linux 4.19 LSM storage. */\n',
    "selinux.c mutable credential security access",
)
selinux = replace_once(
    selinux,
    '    const struct task_security_struct *tsec = selinux_cred(cred);\n',
    '    const struct task_security_struct *tsec =\n        (const struct task_security_struct *)cred->security; /* Linux 4.19 LSM storage. */\n',
    "selinux.c const credential security access",
)
selinux_path.write_text(selinux)
PY

! grep -Fq 'strncpy_from_user_nofault' "$RUNTIME_C" || fail "Unsupported strncpy_from_user_nofault call remains"
grep -Fq 'strncpy_from_unsafe_user(buf, p, buf_len)' "$RUNTIME_C" || fail "Legacy nofault string helper was not installed"
! grep -Fq 'copy_from_user_nofault' "$RUNTIME_C" || fail "Unsupported copy_from_user_nofault call remains"
! grep -Fq 'copy_to_user_nofault' "$RUNTIME_C" || fail "Unsupported copy_to_user_nofault call remains"
grep -Fq 'probe_user_read(&size, st_size_ptr, sizeof(long))' "$RUNTIME_C" || fail "Legacy nofault user read was not installed"
grep -Fq 'probe_user_write(st_size_ptr, &new_size, sizeof(long))' "$RUNTIME_C" || fail "Legacy nofault user write was not installed"
! grep -Fq 'selinux_cred(cred)' "$SELINUX_C" || fail "Unavailable selinux_cred helper remains"
grep -Fq '(struct task_security_struct *)cred->security' "$SELINUX_C" || fail "Mutable Linux 4.19 credential security access is missing"
grep -Fq '(const struct task_security_struct *)cred->security' "$SELINUX_C" || fail "Const Linux 4.19 credential security access is missing"

git -C "$SUKISU_DIR" diff --check
git -C "$SUKISU_DIR" diff --binary -- \
  kernel/runtime/ksud_integration.c \
  kernel/selinux/selinux.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "SukiSU Linux 4.19 runtime/SELinux patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'user_string_nofault=strncpy_from_unsafe_user\n'
  printf 'user_read_nofault=probe_user_read\n'
  printf 'user_write_nofault=probe_user_write\n'
  printf 'selinux_credential_storage=cred-security-task_security_struct\n'
  printf 'runtime_nofault_call_count=3\n'
  printf 'selinux_credential_call_count=2\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "SukiSU Linux 4.19 runtime/SELinux compatibility patch applied"
