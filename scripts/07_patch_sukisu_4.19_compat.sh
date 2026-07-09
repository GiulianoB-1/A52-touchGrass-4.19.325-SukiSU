#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
INIT_C="$SUKISU_DIR/kernel/core/init.c"
SUCOMPAT_C="$SUKISU_DIR/kernel/feature/sucompat.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-compat.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-compat.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before SukiSU compatibility patch"
test -f "$INIT_C" || fail "SukiSU core/init.c is missing"
test -f "$SUCOMPAT_C" || fail "SukiSU feature/sucompat.c is missing"
test ! -e "$KERNEL_DIR/include/linux/pgtable.h" || fail "Unexpected linux/pgtable.h exists; review compatibility patch"
test -f "$KERNEL_DIR/arch/arm64/include/asm/pgtable.h" || fail "ARM64 asm/pgtable.h is missing"
! grep -Fq '#define MODULE_IMPORT_NS' "$KERNEL_DIR/include/linux/module.h" || fail "MODULE_IMPORT_NS already exists; review compatibility patch"

info "Applying exact SukiSU compatibility fixes for Linux 4.19"
python3 - "$INIT_C" "$SUCOMPAT_C" <<'PY'
from pathlib import Path
import sys

init_c = Path(sys.argv[1])
sucompat_c = Path(sys.argv[2])

init_text = init_c.read_text()
old_import = '''#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 13, 0)
MODULE_IMPORT_NS("VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver");
#else
MODULE_IMPORT_NS(VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver);
#endif
'''
new_import = '''#ifdef MODULE_IMPORT_NS
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 13, 0)
MODULE_IMPORT_NS("VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver");
#else
MODULE_IMPORT_NS(VFS_internal_I_am_really_a_filesystem_and_am_NOT_a_driver);
#endif
#endif
'''
if init_text.count(old_import) != 1:
    raise SystemExit(f"core/init.c: expected one MODULE_IMPORT_NS block, found {init_text.count(old_import)}")
init_c.write_text(init_text.replace(old_import, new_import, 1))

sucompat_text = sucompat_c.read_text()
old_include = '#include <linux/pgtable.h>\n'
new_include = '#include <asm/pgtable.h> /* Linux 4.19 has no <linux/pgtable.h> wrapper. */\n'
if sucompat_text.count(old_include) != 1:
    raise SystemExit(f"feature/sucompat.c: expected one linux/pgtable.h include, found {sucompat_text.count(old_include)}")
sucompat_c.write_text(sucompat_text.replace(old_include, new_include, 1))
PY

grep -Fq '#ifdef MODULE_IMPORT_NS' "$INIT_C" || fail "MODULE_IMPORT_NS guard was not added"
! grep -Fq '#include <linux/pgtable.h>' "$SUCOMPAT_C" || fail "Unsupported linux/pgtable.h include remains"
grep -Fq '#include <asm/pgtable.h>' "$SUCOMPAT_C" || fail "ARM64 pgtable fallback was not added"

git -C "$SUKISU_DIR" diff --check
git -C "$SUKISU_DIR" diff --binary -- kernel/core/init.c kernel/feature/sucompat.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "SukiSU Linux 4.19 compatibility patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'module_import_ns=guarded-when-macro-exists\n'
  printf 'pgtable_include=asm/pgtable.h\n'
  printf 'compat_scope=linux-4.19-only\n'
  printf 'compat_patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "SukiSU Linux 4.19 compatibility patch applied"
