#!/usr/bin/env bash
set -Eeuo pipefail

TARGET_VERSION="${1:-}"
FEATURE_SET="${2:-safe}"
case "$TARGET_VERSION" in
  4.19.*) ;;
  *) printf 'ERROR: expected a Linux 4.19.x target version\n' >&2; exit 1 ;;
esac
case "$FEATURE_SET" in
  safe|susfs) ;;
  *) printf 'ERROR: expected feature set safe or susfs\n' >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$SCRIPT_DIR/08_build_linux_4.19.153_resukisu_safe.sh"
GENERATED="$SCRIPT_DIR/.generated-resukisu-$FEATURE_SET-$TARGET_VERSION.sh"

test -f "$TEMPLATE" || { printf 'ERROR: safe integration template is missing: %s\n' "$TEMPLATE" >&2; exit 1; }

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

python3 - "$TEMPLATE" "$GENERATED" "$TARGET_VERSION" "$FEATURE_SET" <<'PY'
from pathlib import Path
import sys

template = Path(sys.argv[1])
out = Path(sys.argv[2])
target = sys.argv[3]
feature_set = sys.argv[4]
text = template.read_text()
needle = "4.19.153"
count = text.count(needle)
if count < 8:
    raise SystemExit(f"template version marker count is unexpectedly low: {count}")
text = text.replace(needle, target)

if feature_set == "susfs":
    checkout_anchor = 'test "$(git -C "$RESUKISU_DIR" rev-parse HEAD)" = "$RESUKISU_COMMIT" || fail "ReSukiSU commit mismatch"\n'
    susfs_block = r'''

info "Integrating pinned SUSFS kernel 4.19 patch"
SUSFS_DIR="$KERNEL_DIR/../susfs4ksu-$SUSFS_VERSION"
rm -rf "$SUSFS_DIR"
git init -q "$SUSFS_DIR"
git -C "$SUSFS_DIR" remote add origin "$SUSFS_REPO"
git -C "$SUSFS_DIR" fetch --quiet --depth=1 origin "refs/tags/$SUSFS_TAG"
git -C "$SUSFS_DIR" checkout --quiet --detach FETCH_HEAD
cp "$SUSFS_DIR/kernel_patches/fs/susfs.c" "$KERNEL_DIR/fs/susfs.c"
cp "$SUSFS_DIR/kernel_patches/include/linux/susfs.h" "$KERNEL_DIR/include/linux/susfs.h"
patch -d "$KERNEL_DIR" -p1 --forward --batch --fuzz=3 < "$SUSFS_DIR/kernel_patches/50_add_susfs_in_kernel-4.19.patch"
sed -i 's/[[:space:]]\+$//' "$KERNEL_DIR/fs/namespace.c" "$KERNEL_DIR/fs/overlayfs/readdir.c"
cp "$SUSFS_DIR/kernel_patches/50_add_susfs_in_kernel-4.19.patch" \
  "$ARTIFACTS_DIR/susfs-$SUSFS_VERSION-kernel-4.19.patch"
test -f "$KERNEL_DIR/fs/susfs.c" || fail "SUSFS source was not installed"
test -f "$KERNEL_DIR/include/linux/susfs.h" || fail "SUSFS header was not installed"
grep -Fq 'obj-$(CONFIG_KSU_SUSFS) += susfs.o' "$KERNEL_DIR/fs/Makefile" || \
  fail "SUSFS fs/Makefile hook is missing"
'''
    if text.count(checkout_anchor) != 1:
        raise SystemExit("ReSukiSU checkout anchor mismatch")
    text = text.replace(checkout_anchor, checkout_anchor + susfs_block, 1)

    old_config = ('  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -e KSU_MANUAL_HOOK \\\n'
                  '  -d KSU_SUSFS -e KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \\\n'
                  '  -e KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -e KSU_MANUAL_HOOK_AUTO_INPUT_HOOK')
    new_config = ('  -e KSU_MULTI_MANAGER_SUPPORT -d KSU_TRACEPOINT_HOOK -d KSU_MANUAL_HOOK \\\n'
                  '  -e KSU_SUSFS -d KSU_MANUAL_HOOK_AUTO_SETUID_HOOK \\\n'
                  '  -d KSU_MANUAL_HOOK_AUTO_INITRC_HOOK -d KSU_MANUAL_HOOK_AUTO_INPUT_HOOK')
    if text.count(old_config) != 1:
        raise SystemExit("ReSukiSU config anchor mismatch")
    text = text.replace(old_config, new_config, 1)

    text = text.replace('resukisu-v4.1.0-safe', 'resukisu-v4.1.0-susfs-v1.4.2-safe')
    text = text.replace("require_line \"$FINAL_CONFIG\" 'CONFIG_KSU_MANUAL_HOOK=y'",
                        "require_line \"$FINAL_CONFIG\" '# CONFIG_KSU_MANUAL_HOOK is not set'")
    text = text.replace("require_line \"$FINAL_CONFIG\" '# CONFIG_KSU_SUSFS is not set'",
                        "require_line \"$FINAL_CONFIG\" 'CONFIG_KSU_SUSFS=y'")
    text = text.replace("printf 'resukisu_version=%s\\n' \"$RESUKISU_VERSION_FULL\"",
                        "printf 'resukisu_version=%s\\n' \"$RESUKISU_VERSION_FULL\"\n  printf 'susfs_version=%s\\n' \"$SUSFS_VERSION\"")

# Linux 4.19.250 and 4.19.325 contain two upstream whitespace diagnostics in
# drivers/tty/synclink_gt.c. They are unrelated to the A52 or ReSukiSU
# integration. Preserve the diagnostics in the artifacts, but do not block the
# direct non-flashable compile checkpoint. Earlier targets retain the strict
# diff check.
if target in {"4.19.250", "4.19.325"}:
    strict = 'git -C "$KERNEL_DIR" diff --check\n'
    recorded = (
        'git -C "$KERNEL_DIR" diff --check > '
        '"$ARTIFACTS_DIR/linux-$TARGET_VERSION-diff-check.txt" 2>&1 || true\n'
    )
    if text.count(strict) != 1:
        raise SystemExit("kernel diff-check anchor mismatch")
    text = text.replace(strict, recorded, 1)

out.write_text(text)
out.chmod(0o755)
PY

bash "$GENERATED"
