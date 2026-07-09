#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
SEPOLICY_C="$SUKISU_DIR/kernel/selinux/sepolicy.c"
KERNEL_SERVICES_C="$KERNEL_DIR/security/selinux/ss/services.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-filename-transition-bitmap.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-filename-transition-bitmap.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before filename-transition bitmap correction"
test -f "$SEPOLICY_C" || fail "SukiSU sepolicy.c is missing"
test -f "$KERNEL_SERVICES_C" || fail "Kernel SELinux services.c is missing"
test -f "$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy.txt" || fail "Legacy SELinux compatibility stage did not run"

# The Android 4.19 lookup tests the parent target type's policy value directly.
grep -Fq 'ebitmap_get_bit(&policydb->filename_trans_ttypes, ttype)' "$KERNEL_SERVICES_C" || \
  fail "Unexpected kernel filename-transition bitmap lookup semantics"

old='return ebitmap_set_bit(&db->filename_trans_ttypes, tgt->value - 1, 1) == 0;'
new='return ebitmap_set_bit(&db->filename_trans_ttypes, tgt->value, 1) == 0;'
test "$(grep -Fc "$old" "$SEPOLICY_C")" -eq 1 || fail "Expected exactly one off-by-one legacy filename bitmap assignment"

before=$(mktemp)
trap 'rm -f "$before"' EXIT
cp "$SEPOLICY_C" "$before"
python3 - "$SEPOLICY_C" "$old" "$new" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
old = sys.argv[2]
new = sys.argv[3]
text = path.read_text()
if text.count(old) != 1:
    raise SystemExit(f"expected one bitmap assignment, found {text.count(old)}")
path.write_text(text.replace(old, new, 1))
PY

! grep -Fq "$old" "$SEPOLICY_C" || fail "Off-by-one filename-transition bitmap assignment remains"
grep -Fq "$new" "$SEPOLICY_C" || fail "Correct filename-transition bitmap assignment is missing"
git -C "$SUKISU_DIR" diff --check

set +e
diff -u --label a/kernel/selinux/sepolicy.c --label b/kernel/selinux/sepolicy.c \
  "$before" "$SEPOLICY_C" > "$PATCH_OUT"
diff_rc=$?
set -e
test "$diff_rc" -eq 1 || fail "Could not produce filename-transition bitmap patch"
test -s "$PATCH_OUT" || fail "Filename-transition bitmap patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'kernel_lookup_bitmap_index=ttype-policy-value\n'
  printf 'inserted_bitmap_index=tgt-value\n'
  printf 'off_by_one_removed=yes\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "Legacy SELinux filename-transition bitmap index corrected"
