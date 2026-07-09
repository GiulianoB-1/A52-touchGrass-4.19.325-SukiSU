#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
BASE_AUDIT_SCRIPT="$(dirname "$0")/08_audit_and_build_sukisu.sh"
SUSFS_REPORT="$ARTIFACTS_DIR/susfs-v1.5.5-integration.txt"
TEMP_AUDIT_SCRIPT="$(mktemp)"
trap 'rm -f "$TEMP_AUDIT_SCRIPT"' EXIT

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before SukiSU SUSFS audit"
test -f "$BASE_AUDIT_SCRIPT" || fail "Base SukiSU audit script is missing"
test -f "$SUSFS_REPORT" || fail "SUSFS integration report is missing"
grep -Fq 'susfs_version=v1.5.5' "$SUSFS_REPORT" || fail "Unexpected SUSFS integration version"
grep -Fq 'manager_abi=reboot-supercall-compat' "$SUSFS_REPORT" || fail "SukiSU SUSFS manager ABI bridge is missing"
grep -Fq 'legacy_abi=prctl-compat' "$SUSFS_REPORT" || fail "Legacy SUSFS ABI bridge is missing"

info "Preparing fail-closed SukiSU plus SUSFS v1.5.5 audit build"
python3 - "$BASE_AUDIT_SCRIPT" "$TEMP_AUDIT_SCRIPT" <<'PY'
from pathlib import Path
import sys

source_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
text = source_path.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    'BUILD_LABEL="linux-4.19.153-sukisu-unmount-off"',
    'BUILD_LABEL="linux-4.19.153-sukisu-susfs-v1.5.5-unmount-off"',
    'SUSFS build label',
)

replace_once(
    'test -f "$ARTIFACTS_DIR/sukisu-linux-4.19-compat.txt" || fail "SukiSU Linux 4.19 compatibility report is missing"\n',
    'test -f "$ARTIFACTS_DIR/sukisu-linux-4.19-compat.txt" || fail "SukiSU Linux 4.19 compatibility report is missing"\n'
    'test -f "$ARTIFACTS_DIR/susfs-v1.5.5-integration.txt" || fail "SUSFS v1.5.5 integration report is missing"\n',
    'SUSFS report requirement',
)

susfs_config_checks = '''grep -Fq 'CONFIG_KSU_SUSFS=y' "$AUDIT_CONFIG" || fail "Final config does not enable SUSFS"
grep -Fq 'CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=y' "$AUDIT_CONFIG" || fail "Final config does not enable SUSFS magic mount"
grep -Fq 'CONFIG_KSU_SUSFS_SUS_MOUNT=y' "$AUDIT_CONFIG" || fail "Final config does not enable SUSFS mount hiding"
grep -Fq 'CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT=y' "$AUDIT_CONFIG" || fail "Final config does not auto-hide SukiSU mounts"
grep -Fq 'CONFIG_KSU_SUSFS_SPOOF_UNAME=y' "$AUDIT_CONFIG" || fail "Final config does not enable SUSFS uname spoofing"
grep -Fq 'CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y' "$AUDIT_CONFIG" || fail "Final config does not hide SukiSU/SUSFS symbols"
grep -Fq 'CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y' "$AUDIT_CONFIG" || fail "Final config does not enable cmdline spoofing"
grep -Fq '# CONFIG_KSU_SUSFS_SUS_PATH is not set' "$AUDIT_CONFIG" || fail "Risky SUS_PATH is enabled in the first SUSFS build"
grep -Fq '# CONFIG_KSU_SUSFS_SUS_KSTAT is not set' "$AUDIT_CONFIG" || fail "Risky SUS_KSTAT is enabled in the first SUSFS build"
grep -Fq '# CONFIG_KSU_SUSFS_TRY_UMOUNT is not set' "$AUDIT_CONFIG" || fail "Risky TRY_UMOUNT is enabled in the first SUSFS build"
grep -Fq '# CONFIG_KSU_SUSFS_OPEN_REDIRECT is not set' "$AUDIT_CONFIG" || fail "Risky OPEN_REDIRECT is enabled in the first SUSFS build"
grep -Fq '# CONFIG_KSU_SUSFS_SUS_SU is not set' "$AUDIT_CONFIG" || fail "Deprecated SUS_SU is enabled"
grep -Fq '# CONFIG_KSU_SUSFS_ENABLE_LOG is not set' "$AUDIT_CONFIG" || fail "SUSFS debug logging is enabled"
'''
replace_once(
    '! grep -Eq \'^CONFIG_.*SUSFS.*=y$\' "$AUDIT_CONFIG" || fail "SUSFS is unexpectedly enabled"\n',
    susfs_config_checks,
    'audit SUSFS config checks',
)

replace_once(
    '    kernel/bpf/verifier.o drivers/kernelsu/\n',
    '    kernel/bpf/verifier.o drivers/kernelsu/ fs/susfs.o fs/susfs_sukisu_compat.o\n',
    'targeted SUSFS object build',
)

replace_once(
    "  printf 'susfs=not-integrated\\n'\n",
    "  printf 'susfs=v1.5.5-dual-abi\\n'\n"
    "  printf 'susfs_manager_abi=reboot-supercall-compat\\n'\n"
    "  printf 'susfs_legacy_abi=prctl-compat\\n'\n",
    'audit SUSFS report',
)

final_config_checks = '''grep -Fq 'CONFIG_KSU_SUSFS=y' "$FINAL_CONFIG" || fail "Full build lost SUSFS"
grep -Fq 'CONFIG_KSU_SUSFS_HAS_MAGIC_MOUNT=y' "$FINAL_CONFIG" || fail "Full build lost SUSFS magic mount"
grep -Fq 'CONFIG_KSU_SUSFS_SUS_MOUNT=y' "$FINAL_CONFIG" || fail "Full build lost SUSFS mount hiding"
grep -Fq 'CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT=y' "$FINAL_CONFIG" || fail "Full build lost SukiSU mount auto-hide"
grep -Fq 'CONFIG_KSU_SUSFS_SPOOF_UNAME=y' "$FINAL_CONFIG" || fail "Full build lost SUSFS uname spoofing"
grep -Fq 'CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y' "$FINAL_CONFIG" || fail "Full build lost SUSFS symbol hiding"
grep -Fq 'CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y' "$FINAL_CONFIG" || fail "Full build lost SUSFS cmdline spoofing"
grep -Fq '# CONFIG_KSU_SUSFS_SUS_PATH is not set' "$FINAL_CONFIG" || fail "Full build enabled SUS_PATH"
grep -Fq '# CONFIG_KSU_SUSFS_SUS_KSTAT is not set' "$FINAL_CONFIG" || fail "Full build enabled SUS_KSTAT"
grep -Fq '# CONFIG_KSU_SUSFS_TRY_UMOUNT is not set' "$FINAL_CONFIG" || fail "Full build enabled TRY_UMOUNT"
grep -Fq '# CONFIG_KSU_SUSFS_OPEN_REDIRECT is not set' "$FINAL_CONFIG" || fail "Full build enabled OPEN_REDIRECT"
grep -Fq '# CONFIG_KSU_SUSFS_SUS_SU is not set' "$FINAL_CONFIG" || fail "Full build enabled SUS_SU"
'''
replace_once(
    '! grep -Eq \'^CONFIG_.*SUSFS.*=y$\' "$FINAL_CONFIG" || fail "Full build enabled SUSFS"\n',
    final_config_checks,
    'full-build SUSFS config checks',
)

replace_once(
    "  printf 'config_susfs=n\\n'\n",
    "  printf 'config_susfs=y\\n'\n"
    "  printf 'susfs_version=v1.5.5\\n'\n"
    "  printf 'susfs_variant=NON-GKI\\n'\n"
    "  printf 'susfs_manager_abi=reboot-supercall-compat\\n'\n"
    "  printf 'susfs_legacy_abi=prctl-compat\\n'\n",
    'build result SUSFS fields',
)

replace_once(
    'info "Linux $TARGET_VERSION with pinned SukiSU Ultra compiled successfully"',
    'info "Linux $TARGET_VERSION with pinned SukiSU Ultra and SUSFS v1.5.5 compiled successfully"',
    'success message',
)

out_path.write_text(text)
PY

chmod +x "$TEMP_AUDIT_SCRIPT"
bash -n "$TEMP_AUDIT_SCRIPT"
bash "$TEMP_AUDIT_SCRIPT"

AUDIT_OUT="$KERNEL_DIR/out-sukisu-audit"
SUSFS_OBJECT="$AUDIT_OUT/fs/susfs.o"
SUSFS_COMPAT_OBJECT="$AUDIT_OUT/fs/susfs_sukisu_compat.o"
FINAL_IMAGE="$ARTIFACTS_DIR/Image-linux-4.19.153-sukisu-susfs-v1.5.5-unmount-off"
FINAL_CONFIG="$ARTIFACTS_DIR/config-linux-4.19.153-sukisu-susfs-v1.5.5-unmount-off"

test -s "$SUSFS_OBJECT" || fail "SUSFS audit object was not produced"
test -s "$SUSFS_COMPAT_OBJECT" || fail "SukiSU SUSFS compatibility object was not produced"
test -s "$FINAL_IMAGE" || fail "SUSFS full kernel image was not produced"
test -s "$FINAL_CONFIG" || fail "SUSFS full kernel config was not produced"

cp "$SUSFS_OBJECT" "$ARTIFACTS_DIR/susfs-v1.5.5.o"
cp "$SUSFS_COMPAT_OBJECT" "$ARTIFACTS_DIR/susfs-sukisu-compat-v1.5.5.o"
sha256sum \
  "$ARTIFACTS_DIR/susfs-v1.5.5.o" \
  "$ARTIFACTS_DIR/susfs-sukisu-compat-v1.5.5.o" \
  > "$ARTIFACTS_DIR/susfs-v1.5.5-objects.sha256"

strings "$FINAL_IMAGE" | grep -F 'v1.5.5' > "$ARTIFACTS_DIR/susfs-image-version-strings.txt" || true
grep -Fq 'v1.5.5' "$ARTIFACTS_DIR/susfs-image-version-strings.txt" || fail "Final Image does not contain the SUSFS v1.5.5 marker"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'susfs_version=v1.5.5\n'
  printf 'susfs_object_sha256=%s\n' "$(sha256sum "$ARTIFACTS_DIR/susfs-v1.5.5.o" | cut -d' ' -f1)"
  printf 'susfs_compat_object_sha256=%s\n' "$(sha256sum "$ARTIFACTS_DIR/susfs-sukisu-compat-v1.5.5.o" | cut -d' ' -f1)"
  printf 'image_sha256=%s\n' "$(sha256sum "$FINAL_IMAGE" | cut -d' ' -f1)"
  printf 'runtime_tests=deferred-to-device\n'
} | tee "$ARTIFACTS_DIR/susfs-v1.5.5-audit-result.txt"

info "SukiSU plus SUSFS v1.5.5 audit and full build completed"
