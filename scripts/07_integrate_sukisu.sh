#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
REPORT="$ARTIFACTS_DIR/sukisu-integration.txt"
MAIN_PATCH="$ARTIFACTS_DIR/sukisu-host-integration.patch"
SAFETY_PATCH="$ARTIFACTS_DIR/sukisu-unmount-safety.patch"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before SukiSU integration"
test -f "$ARTIFACTS_DIR/bpf-verifier-repair.txt" || fail "BPF repair checkpoint is missing"
test -x "$KERNEL_DIR/scripts/config" || fail "Kernel scripts/config is missing or not executable"
test -f "$DEFCONFIG" || fail "a52xq defconfig is missing"

info "Removing stale KernelSU integration directories"
rm -rf "$SUKISU_DIR"
rm -rf "$KERNEL_DIR/drivers/kernelsu"

info "Fetching exact pinned SukiSU Ultra source"
mkdir -p "$SUKISU_DIR"
git -C "$SUKISU_DIR" init
git -C "$SUKISU_DIR" remote add origin "$SUKISU_REPO"
git -C "$SUKISU_DIR" fetch --depth=1 origin "$SUKISU_COMMIT"
git -C "$SUKISU_DIR" checkout --detach FETCH_HEAD
actual_sukisu_commit=$(git -C "$SUKISU_DIR" rev-parse HEAD)
test "$actual_sukisu_commit" = "$SUKISU_COMMIT" || fail "SukiSU commit mismatch: $actual_sukisu_commit"

info "Removing obsolete touchGrass KernelSU-Next manual hook calls"
python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])

replacements = {
    "drivers/input/input.c": [
        (
            "\n#ifdef CONFIG_KSU\nextern bool ksu_input_hook __read_mostly;\nextern int ksu_handle_input_handle_event(unsigned int *type, unsigned int *code, int *value);\n#endif\n",
            "\n",
            "legacy input hook declarations",
        ),
        (
            "\n#ifdef CONFIG_KSU\n\tif (unlikely(ksu_input_hook))\n\t\tksu_handle_input_handle_event(&type, &code, &value);\n#endif\n",
            "\n",
            "legacy input hook call",
        ),
    ],
    "fs/exec.c": [
        (
            "\n#ifdef CONFIG_KSU\nextern bool ksu_execveat_hook __read_mostly;\nextern int ksu_handle_execveat(int *fd, struct filename **filename_ptr, void *argv,\n\t\t\tvoid *envp, int *flags);\nextern int ksu_handle_execveat_sucompat(int *fd, struct filename **filename_ptr,\n\t\t\t\t void *argv, void *envp, int *flags);\n#endif\n",
            "\n",
            "legacy exec hook declarations",
        ),
        (
            "\n#ifdef CONFIG_KSU\n\tif (unlikely(ksu_execveat_hook))\n\t\tksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0);\n\telse\n\t\tksu_handle_execveat_sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL);\n#endif\n",
            "\n",
            "legacy native exec hook call",
        ),
        (
            "\n#ifdef CONFIG_KSU\n\tif (!ksu_execveat_hook)\n\t\tksu_handle_execveat_sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL); /* 32-bit su */\n#endif\n",
            "\n",
            "legacy compat exec hook call",
        ),
    ],
    "fs/open.c": [
        (
            "\n#ifdef CONFIG_KSU\nextern int ksu_handle_faccessat(int *dfd, const char __user **filename_user, int *mode,\n\t\t\t                    int *flags);\n#endif\n",
            "\n",
            "legacy faccessat declaration",
        ),
        (
            "\n#ifdef CONFIG_KSU\n\tksu_handle_faccessat(&dfd, &filename, &mode, NULL);\n#endif\n",
            "\n",
            "legacy faccessat call",
        ),
    ],
    "fs/read_write.c": [
        (
            "\n#ifdef CONFIG_KSU\nextern bool ksu_vfs_read_hook __read_mostly;\nextern int ksu_handle_sys_read(unsigned int fd, char __user **buf_ptr,\n\t\t\tsize_t *count_ptr);\n#endif\n",
            "\n",
            "legacy read hook declarations",
        ),
        (
            "\n#ifdef CONFIG_KSU\n\tif (unlikely(ksu_vfs_read_hook)) \n\t\tksu_handle_sys_read(fd, &buf, &count);\n#endif\n",
            "\n",
            "legacy read hook call",
        ),
    ],
    "fs/stat.c": [
        (
            "\n#ifdef CONFIG_KSU\nextern int ksu_handle_stat(int *dfd, const char __user **filename_user, int *flags);\n#endif\n",
            "\n",
            "legacy stat declaration",
        ),
        (
            "\n#ifdef CONFIG_KSU\n\tksu_handle_stat(&dfd, &filename, &flag);\n#endif\n",
            "\n",
            "legacy native stat call",
        ),
        (
            "\n#ifdef CONFIG_KSU\n\tksu_handle_stat(&dfd, &filename, &flag); /* 32-bit su support */\n#endif\n",
            "\n",
            "legacy compat stat call",
        ),
    ],
}

for rel, edits in replacements.items():
    path = root / rel
    text = path.read_text()
    for old, new, label in edits:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{rel}: {label}: expected exactly one match, found {count}")
        text = text.replace(old, new, 1)
    path.write_text(text)
PY

info "Connecting SukiSU to the kernel build"
ln -s ../KernelSU/kernel "$KERNEL_DIR/drivers/kernelsu"

python3 - "$KERNEL_DIR/drivers/Makefile" "$KERNEL_DIR/drivers/Kconfig" <<'PY'
from pathlib import Path
import sys

makefile = Path(sys.argv[1])
kconfig = Path(sys.argv[2])

make_text = makefile.read_text()
make_line = "obj-$(CONFIG_KSU) += kernelsu/"
make_text = "\n".join(line for line in make_text.splitlines() if "CONFIG_KSU" not in line or "kernelsu" not in line)
make_text = make_text.rstrip() + "\n\n" + make_line + "\n"
makefile.write_text(make_text)

kconfig_text = kconfig.read_text()
source_line = 'source "drivers/kernelsu/Kconfig"'
kconfig_text = "\n".join(line for line in kconfig_text.splitlines() if line.strip() != source_line)
marker = "\nendmenu"
pos = kconfig_text.rfind(marker)
if pos >= 0:
    kconfig_text = kconfig_text[:pos] + "\n" + source_line + kconfig_text[pos:]
else:
    kconfig_text = kconfig_text.rstrip() + "\n" + source_line + "\n"
kconfig.write_text(kconfig_text)
PY

info "Hard-disabling module unmount in SukiSU policy and feature control"
python3 - "$SUKISU_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
allowlist = root / "kernel/policy/allowlist.c"
umount = root / "kernel/feature/kernel_umount.c"

allow_text = allowlist.read_text()
old = "    // This means that we will umount modules by default!\n    default_non_root_profile.umount_modules = true;"
new = "    // Project safety policy: never unmount modules by default.\n    default_non_root_profile.umount_modules = false;"
if allow_text.count(old) != 1:
    raise SystemExit("allowlist default-unmount pattern did not match exactly once")
allowlist.write_text(allow_text.replace(old, new, 1))

umount_text = umount.read_text()
old = "static bool ksu_kernel_umount_enabled = true;"
new = "/* Project safety policy: kernel-level module unmount is permanently disabled. */\nstatic bool ksu_kernel_umount_enabled = false;"
if umount_text.count(old) != 1:
    raise SystemExit("kernel_umount default pattern did not match exactly once")
umount_text = umount_text.replace(old, new, 1)

old_setter = """static int kernel_umount_feature_set(u64 value)
{
    bool enable = value != 0;
    ksu_kernel_umount_enabled = enable;
    pr_info(\"kernel_umount: set to %d\\n\", enable);
    return 0;
}
"""
new_setter = """static int kernel_umount_feature_set(u64 value)
{
    /* Keep the UAPI compatible, but never permit this feature to activate. */
    ksu_kernel_umount_enabled = false;
    if (value)
        pr_warn(\"kernel_umount: enable request ignored by project safety policy\\n\");
    return 0;
}
"""
if umount_text.count(old_setter) != 1:
    raise SystemExit("kernel_umount setter pattern did not match exactly once")
umount.write_text(umount_text.replace(old_setter, new_setter, 1))
PY

info "Configuring SukiSU-only feature set"
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" \
  -e KPROBES \
  -e KSU \
  -e KSU_MANUAL_SU \
  -d KSU_DEBUG \
  -d KPM \
  -d KSU_DISABLE_MANAGER \
  -d KSU_DISABLE_POLICY \
  -d KSU_WITH_KPROBES \
  -d KSU_SUSFS

# Source-level safety and compatibility checks.
grep -Fq 'CONFIG_KPROBES=y' "$DEFCONFIG" || fail "KPROBES was not enabled"
grep -Fq 'CONFIG_KSU=y' "$DEFCONFIG" || fail "KSU was not enabled"
grep -Fq 'CONFIG_KSU_MANUAL_SU=y' "$DEFCONFIG" || fail "KSU_MANUAL_SU was not enabled"
grep -Fq '# CONFIG_KPM is not set' "$DEFCONFIG" || fail "KPM was not disabled"
grep -Fq 'CONFIG_EXT4_FS=y' "$DEFCONFIG" || fail "SukiSU requires built-in EXT4_FS"

grep -Fq 'default_non_root_profile.umount_modules = false;' "$SUKISU_DIR/kernel/policy/allowlist.c" || fail "Default module unmount remains enabled"
grep -Fq 'static bool ksu_kernel_umount_enabled = false;' "$SUKISU_DIR/kernel/feature/kernel_umount.c" || fail "Kernel unmount does not start disabled"
grep -Fq 'enable request ignored by project safety policy' "$SUKISU_DIR/kernel/feature/kernel_umount.c" || fail "Kernel unmount setter was not locked"
! grep -Fq 'ksu_kernel_umount_enabled = enable;' "$SUKISU_DIR/kernel/feature/kernel_umount.c" || fail "Kernel unmount can still be enabled"

grep -Fq '#define FILE_FORMAT_VERSION 4' "$SUKISU_DIR/kernel/policy/allowlist.c" || fail "Unexpected allowlist file format"
grep -Fq '#define KSU_APP_PROFILE_VER 4' "$SUKISU_DIR/uapi/app_profile.h" || fail "Unexpected app-profile ABI"

for file in drivers/input/input.c fs/exec.c fs/open.c fs/read_write.c fs/stat.c; do
  ! grep -Eq 'ksu_(input_hook|execveat_hook|vfs_read_hook|handle_execveat|handle_execveat_sucompat|handle_faccessat|handle_sys_read|handle_stat)' "$KERNEL_DIR/$file" || \
    fail "Legacy KernelSU-Next hook remains in $file"
done

test -L "$KERNEL_DIR/drivers/kernelsu" || fail "drivers/kernelsu symlink is missing"
test "$(readlink "$KERNEL_DIR/drivers/kernelsu")" = '../KernelSU/kernel' || fail "Unexpected drivers/kernelsu symlink target"
grep -Fq 'obj-$(CONFIG_KSU) += kernelsu/' "$KERNEL_DIR/drivers/Makefile" || fail "drivers Makefile entry is missing"
grep -Fq 'source "drivers/kernelsu/Kconfig"' "$KERNEL_DIR/drivers/Kconfig" || fail "drivers Kconfig entry is missing"

! grep -RFn 'config KSU_SUSFS' "$SUKISU_DIR/kernel" || fail "SUSFS unexpectedly exists in the selected SukiSU source"

git -C "$KERNEL_DIR" diff --check
git -C "$SUKISU_DIR" diff --check

git -C "$KERNEL_DIR" diff --binary -- \
  arch/arm64/configs/a52xq_defconfig \
  drivers/Makefile drivers/Kconfig \
  drivers/input/input.c fs/exec.c fs/open.c fs/read_write.c fs/stat.c > "$MAIN_PATCH"
git -C "$SUKISU_DIR" diff --binary -- kernel/policy/allowlist.c kernel/feature/kernel_umount.c > "$SAFETY_PATCH"
test -s "$MAIN_PATCH" || fail "Host integration patch is empty"
test -s "$SAFETY_PATCH" || fail "SukiSU safety patch is empty"
sha256sum "$MAIN_PATCH" > "$MAIN_PATCH.sha256"
sha256sum "$SAFETY_PATCH" > "$SAFETY_PATCH.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_repository=%s\n' "$SUKISU_REPO"
  printf 'sukisu_commit=%s\n' "$actual_sukisu_commit"
  printf 'hook_mode=sukisu-kprobes-and-syscall-hooks\n'
  printf 'legacy_ksun_manual_hooks=removed\n'
  printf 'allowlist_file_format=4\n'
  printf 'app_profile_abi=4\n'
  printf 'kernel_unmount_default=disabled\n'
  printf 'kernel_unmount_runtime_enable=blocked\n'
  printf 'kpm=disabled\n'
  printf 'susfs=not-integrated\n'
  printf 'manager_policy=enabled\n'
  printf 'manual_su=enabled\n'
  printf 'host_patch_sha256=%s\n' "$(cut -d' ' -f1 "$MAIN_PATCH.sha256")"
  printf 'safety_patch_sha256=%s\n' "$(cut -d' ' -f1 "$SAFETY_PATCH.sha256")"
} | tee "$REPORT"

info "Pinned SukiSU Ultra integration completed"
