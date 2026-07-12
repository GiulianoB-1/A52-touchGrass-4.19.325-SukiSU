#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.180
HEADER="$KERNEL_DIR/include/linux/fscrypt.h"
REPORT="$ARTIFACTS_DIR/fscrypt-nokey-helper-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before fscrypt repair"
test -f "$HEADER" || fail "Samsung consolidated fscrypt header is missing"

python3 - "$HEADER" "$REPORT" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
report = Path(sys.argv[2])
text = path.read_text()
repairs = []

supported_old = '''static inline void fscrypt_handle_d_move(struct dentry *dentry)
{
\tdentry->d_flags &= ~DCACHE_ENCRYPTED_NAME;
}
'''
supported_new = '''static inline void fscrypt_handle_d_move(struct dentry *dentry)
{
\tdentry->d_flags &= ~DCACHE_ENCRYPTED_NAME;
}

static inline bool fscrypt_is_nokey_name(const struct dentry *dentry)
{
\treturn dentry->d_flags & DCACHE_ENCRYPTED_NAME;
}
'''

notsupp_old = '''static inline void fscrypt_handle_d_move(struct dentry *dentry)
{
}
'''
notsupp_new = '''static inline void fscrypt_handle_d_move(struct dentry *dentry)
{
}

static inline bool fscrypt_is_nokey_name(const struct dentry *dentry)
{
\treturn false;
}
'''

if text.count('static inline bool fscrypt_is_nokey_name') == 0:
    if text.count(supported_old) != 1:
        raise SystemExit('encrypted fscrypt_handle_d_move anchor mismatch')
    if text.count(notsupp_old) != 1:
        raise SystemExit('non-encryption fscrypt_handle_d_move anchor mismatch')
    text = text.replace(supported_old, supported_new, 1)
    text = text.replace(notsupp_old, notsupp_new, 1)
    path.write_text(text)
    repairs.append('include/linux/fscrypt.h=ported-no-key-helper-to-both-config-branches')
elif text.count('static inline bool fscrypt_is_nokey_name') != 2:
    raise SystemExit('partial fscrypt no-key helper repair detected')

final = path.read_text()
if final.count('static inline bool fscrypt_is_nokey_name') != 2:
    raise SystemExit('fscrypt no-key helper count is not two')
if final.count('return dentry->d_flags & DCACHE_ENCRYPTED_NAME;') != 1:
    raise SystemExit('encrypted fscrypt no-key implementation is missing')
if final.count('static inline bool fscrypt_is_nokey_name(const struct dentry *dentry)\n{\n\treturn false;\n}') != 1:
    raise SystemExit('non-encryption fscrypt no-key stub is missing')

report.write_text('\n'.join(repairs or ['repairs=already-present']) + '\n')
print(report.read_text(), end='')
PY

git -C "$KERNEL_DIR" diff --check -- include/linux/fscrypt.h

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'header=include/linux/fscrypt.h\n'
  printf 'helper_definitions=2\n'
  printf 'result=linux-4.19.180-samsung-fscrypt-nokey-helper-ready\n'
} | tee -a "$REPORT"

info "Linux $TARGET_VERSION Samsung fscrypt no-key helper repaired"
