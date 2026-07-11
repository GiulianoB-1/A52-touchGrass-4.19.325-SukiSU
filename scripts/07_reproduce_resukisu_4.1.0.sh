#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.152
RESUKISU_DIR="$KERNEL_DIR/KernelSU"
DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
REFERENCE="$PROJECT_DIR/reference/tgk-v1.5.1-a52xq-resukisu.txt"
LABEL="touchgrass-4.19.152-resukisu-v4.1.0-reproduction"
REPORT="$ARTIFACTS_DIR/resukisu-v4.1.0-reproduction.txt"
HOST_PATCH="$ARTIFACTS_DIR/resukisu-v4.1.0-host-integration.patch"

require_line() {
  local file="$1"
  local line="$2"
  grep -Fxq -- "$line" "$file" || fail "Missing expected line in $file: $line"
}

require_absent() {
  local file="$1"
  local text="$2"
  if grep -Fq -- "$text" "$file"; then
    fail "Unexpected text remains in $file: $text"
  fi
}

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Unexpected touchGrass commit"
test -x "$KERNEL_DIR/scripts/config" || fail "Kernel scripts/config is missing"
test -f "$DEFCONFIG" || fail "a52xq defconfig is missing"
test -f "$REFERENCE" || fail "Release reference manifest is missing"

info "Fetching the exact ReSukiSU revision embedded in touchGrass v1.5.1"
rm -rf "$RESUKISU_DIR" "$KERNEL_DIR/drivers/kernelsu"
git init -q "$RESUKISU_DIR"
git -C "$RESUKISU_DIR" remote add origin "$RESUKISU_REPO"
git -C "$RESUKISU_DIR" fetch --quiet --depth=1 origin "$RESUKISU_COMMIT"
git -C "$RESUKISU_DIR" checkout --quiet --detach FETCH_HEAD
actual_resukisu_commit=$(git -C "$RESUKISU_DIR" rev-parse HEAD)
test "$actual_resukisu_commit" = "$RESUKISU_COMMIT" || fail "ReSukiSU commit mismatch"

info "Removing only obsolete hooks replaced by the release's automatic LSM/input hooks"
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

info "Connecting ReSukiSU to the touchGrass build"
ln -s ../KernelSU/kernel "$KERNEL_DIR/drivers/kernelsu"
python3 - "$KERNEL_DIR/drivers/Makefile" "$KERNEL_DIR/drivers/Kconfig" <<'PY'
from pathlib import Path
import sys

makefile = Path(sys.argv[1])
kconfig = Path(sys.argv[2])
make_line = "obj-$(CONFIG_KSU) += kernelsu/"
source_line = 'source "drivers/kernelsu/Kconfig"'

make_text = makefile.read_text()
make_text = "\n".join(
    line for line in make_text.splitlines()
    if not ("CONFIG_KSU" in line and "kernelsu" in line)
).rstrip() + "\n\n" + make_line + "\n"
makefile.write_text(make_text)

kconfig_text = "\n".join(
    line for line in kconfig.read_text().splitlines()
    if line.strip() != source_line
)
marker = "\nendmenu"
pos = kconfig_text.rfind(marker)
if pos < 0:
    raise SystemExit("drivers/Kconfig final endmenu was not found")
kconfig.write_text(kconfig_text[:pos] + "\n" + source_line + kconfig_text[pos:] + "\n")
PY

info "Applying the exact KSU configuration extracted from the released Image"
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" \
  -e MODULES \
  -e EXT4_FS \
  -d KPROBES \
  -e KSU \
  -d KSU_DEBUG \
  -d KSU_TOOLKIT_SUPPORT \
  -d KSU_DISABLE_MANAGER \
  -d KSU_DISABLE_POLICY \
  -e KSU_MULTI_MANAGER_SUPPORT \
  -d KSU_TRACEPOINT_HOOK \
  -e KSU_MANUAL_HOOK \
  -d KSU_SUSFS \
  -e KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \
  -e KSU_MANUAL_HOOK_AUTO_INITRC_HOOK \
  -e KSU_MANUAL_HOOK_AUTO_INPUT_HOOK
python3 - "$DEFCONFIG" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
line = 'CONFIG_KSU_FULL_NAME_FORMAT="%TAG_NAME%-%COMMIT_SHA%@%REPO_NAME%"'
lines = [item for item in path.read_text().splitlines() if not item.startswith("CONFIG_KSU_FULL_NAME_FORMAT=")]
lines.append(line)
path.write_text("\n".join(lines) + "\n")
PY

info "Validating the release manual-hook topology before compilation"
require_absent "$KERNEL_DIR/fs/read_write.c" "ksu_vfs_read_hook"
require_absent "$KERNEL_DIR/security/selinux/hooks.c" "is_ksu_transition"
require_absent "$KERNEL_DIR/security/security.c" "ksu_handle_rename"
grep -Fq 'ksu_handle_execveat' "$KERNEL_DIR/fs/exec.c" || fail "execveat hook is missing"
grep -Fq 'ksu_handle_faccessat' "$KERNEL_DIR/fs/open.c" || fail "faccessat hook is missing"
grep -Fq 'ksu_handle_stat' "$KERNEL_DIR/fs/stat.c" || fail "stat hook is missing"
grep -Fq 'ksu_handle_newfstat_ret' "$KERNEL_DIR/fs/stat.c" || fail "newfstat return hook is missing"
grep -Fq 'ksu_handle_fstat64_ret' "$KERNEL_DIR/fs/stat.c" || fail "fstat64 return hook is missing"
grep -Fq 'ksu_handle_sys_reboot' "$KERNEL_DIR/kernel/reboot.c" || fail "reboot hook is missing"

test -L "$KERNEL_DIR/drivers/kernelsu" || fail "drivers/kernelsu symlink is missing"
test "$(readlink "$KERNEL_DIR/drivers/kernelsu")" = '../KernelSU/kernel' || fail "Unexpected kernelsu symlink"
require_line "$KERNEL_DIR/drivers/Makefile" 'obj-$(CONFIG_KSU) += kernelsu/'
require_line "$KERNEL_DIR/drivers/Kconfig" 'source "drivers/kernelsu/Kconfig"'

# The reproduction deliberately preserves the released unmount defaults. It is
# non-flashable; the safety lockout is a separate change before device testing.
grep -Fq 'default_non_root_profile.umount_modules = true;' "$RESUKISU_DIR/kernel/policy/allowlist.c" || fail "Release allowlist default changed"
grep -Fq 'static bool ksu_kernel_umount_enabled = true;' "$RESUKISU_DIR/kernel/feature/kernel_umount.c" || fail "Release kernel-unmount default changed"

git -C "$KERNEL_DIR" diff --check
git -C "$RESUKISU_DIR" diff --check

git -C "$KERNEL_DIR" diff --binary -- \
  arch/arm64/configs/a52xq_defconfig \
  drivers/Makefile drivers/Kconfig \
  drivers/input/input.c fs/read_write.c > "$HOST_PATCH"
sha256sum "$HOST_PATCH" > "$HOST_PATCH.sha256"

info "Building the 4.19.152 ReSukiSU reproduction"
build_kernel "$LABEL"

FINAL_CONFIG="$ARTIFACTS_DIR/config-$LABEL"
FINAL_IMAGE="$ARTIFACTS_DIR/Image-$LABEL"
test -s "$FINAL_CONFIG" || fail "Final config is missing"
test -s "$FINAL_IMAGE" || fail "Final Image is missing"

info "Comparing the generated build with the released configuration and metadata"
for line in \
  'CONFIG_KSU=y' \
  '# CONFIG_KSU_DEBUG is not set' \
  '# CONFIG_KSU_TOOLKIT_SUPPORT is not set' \
  'CONFIG_KSU_FULL_NAME_FORMAT="%TAG_NAME%-%COMMIT_SHA%@%REPO_NAME%"' \
  '# CONFIG_KSU_DISABLE_MANAGER is not set' \
  '# CONFIG_KSU_DISABLE_POLICY is not set' \
  'CONFIG_KSU_MULTI_MANAGER_SUPPORT=y' \
  '# CONFIG_KSU_TRACEPOINT_HOOK is not set' \
  'CONFIG_KSU_MANUAL_HOOK=y' \
  '# CONFIG_KSU_SUSFS is not set' \
  'CONFIG_KSU_MANUAL_HOOK_AUTO_SETUID_HOOK=y' \
  'CONFIG_KSU_MANUAL_HOOK_AUTO_INITRC_HOOK=y' \
  'CONFIG_KSU_MANUAL_HOOK_AUTO_INPUT_HOOK=y' \
  '# CONFIG_KPROBES is not set' \
  'CONFIG_MODULES=y' \
  'CONFIG_EXT4_FS=y'; do
  require_line "$FINAL_CONFIG" "$line"
done

strings "$FINAL_IMAGE" > "$ARTIFACTS_DIR/Image-$LABEL.strings.txt"
grep -Fq 'Linux version 4.19.152-touchGrassKernel+' "$ARTIFACTS_DIR/Image-$LABEL.strings.txt" || fail "Unexpected kernel release string"
grep -Fxq "$RESUKISU_VERSION_FULL" "$ARTIFACTS_DIR/Image-$LABEL.strings.txt" || fail "Exact ReSukiSU version string is missing"

built_sha=$(sha256sum "$FINAL_IMAGE" | awk '{print $1}')
built_size=$(stat -c %s "$FINAL_IMAGE")
release_sha=$(awk -F= '$1=="release_image_sha256" {print $2}' "$REFERENCE")
release_size=$(awk -F= '$1=="release_image_bytes" {print $2}' "$REFERENCE")
compiler=$($CC --version | head -n 1)

{
  printf 'status=reproduced-from-source\n'
  printf 'flashable=no\n'
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'touchgrass_commit=%s\n' "$TOUCHGRASS_COMMIT"
  printf 'resukisu_commit=%s\n' "$actual_resukisu_commit"
  printf 'resukisu_version_full=%s\n' "$RESUKISU_VERSION_FULL"
  printf 'hook_mode=manual-with-auto-lsm-and-input-hooks\n'
  printf 'susfs=disabled-as-in-release\n'
  printf 'kernel_unmount=same-as-release-not-safe-for-device-test\n'
  printf 'compiler=%s\n' "$compiler"
  printf 'built_image_sha256=%s\n' "$built_sha"
  printf 'release_image_sha256=%s\n' "$release_sha"
  printf 'image_hash_match=%s\n' "$([[ "$built_sha" = "$release_sha" ]] && echo yes || echo no-expected-with-different-build-metadata)"
  printf 'built_image_bytes=%s\n' "$built_size"
  printf 'release_image_bytes=%s\n' "$release_size"
  printf 'image_size_delta=%s\n' "$((built_size - release_size))"
  printf 'host_patch_sha256=%s\n' "$(cut -d' ' -f1 "$HOST_PATCH.sha256")"
} | tee "$REPORT"

info "touchGrass 4.19.152 + exact ReSukiSU v4.1.0 reproduction passed"
