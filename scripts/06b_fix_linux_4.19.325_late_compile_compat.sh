#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/late-compile-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying late Linux $TARGET_VERSION compile compatibility repairs"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor mismatch: {count}")
    return text.replace(old, new, 1)


# Linux 4.19.325 requires fscrypt_d_ops for ciphertext dentries.  The direct
# three-way merge can retain Samsung's older crypto.c, which has neither the
# dentry revalidation callback nor the operations table.  Restore the exact
# stable implementation when it is absent, while preserving it unchanged when
# the upstream side already won the merge.
private = root / "fs/crypto/fscrypt_private.h"
text = private.read_text()
declaration = "extern const struct dentry_operations fscrypt_d_ops;\n"
if declaration not in text:
    anchor = "extern struct kmem_cache *fscrypt_info_cachep;\n"
    text = replace_once(text, anchor, anchor + declaration, "fscrypt d_ops declaration")
    private.write_text(text)
    repairs.append("fs/crypto/fscrypt_private.h=declared-fscrypt-d-ops")
elif text.count(declaration) != 1:
    raise SystemExit("unexpected fscrypt_d_ops declaration count")

crypto = root / "fs/crypto/crypto.c"
crypto_text = crypto.read_text()
for include in ("#include <linux/dcache.h>\n", "#include <linux/namei.h>\n"):
    if include not in crypto_text:
        anchor = '#include <linux/ratelimit.h>\n'
        crypto_text = replace_once(crypto_text, anchor, anchor + include,
                                   f"fscrypt include {include.strip()}")
        repairs.append(f"fs/crypto/crypto.c=added-{include.strip()[10:-3]}-include")

function_marker = "static int fscrypt_d_revalidate(struct dentry *dentry, unsigned int flags)"
ops_marker = "const struct dentry_operations fscrypt_d_ops = {"
if function_marker not in crypto_text and ops_marker not in crypto_text:
    anchor = "void fscrypt_restore_control_page(struct page *page)\n"
    block = '''/*
 * Validate dentries in encrypted directories to make sure we aren't
 * potentially caching stale dentries after a key has been added.
 */
static int fscrypt_d_revalidate(struct dentry *dentry, unsigned int flags)
{
	struct dentry *dir;
	int err;
	int valid;

	if (!(dentry->d_flags & DCACHE_ENCRYPTED_NAME))
		return 1;

	if (flags & LOOKUP_RCU)
		return -ECHILD;

	dir = dget_parent(dentry);
	err = fscrypt_get_encryption_info(d_inode(dir));
	valid = !fscrypt_has_encryption_key(d_inode(dir));
	dput(dir);

	if (err < 0)
		return err;

	return valid;
}

const struct dentry_operations fscrypt_d_ops = {
	.d_revalidate = fscrypt_d_revalidate,
};

'''
    crypto_text = replace_once(crypto_text, anchor, block + anchor,
                               "fscrypt restore-control-page insertion")
    repairs.append("fs/crypto/crypto.c=restored-stable-fscrypt-d-ops")
elif crypto_text.count(function_marker) != 1 or crypto_text.count(ops_marker) != 1:
    raise SystemExit("partial or duplicate fscrypt_d_ops implementation")

crypto.write_text(crypto_text)

# The stable sysfs conversion changed this function to a length-based writer,
# but one Android line still used the removed str/end pointer variables.
wakelock = root / "kernel/power/wakelock.c"
text = wakelock.read_text()
old = '\t\t\tstr += scnprintf(str, end - str, "%s ", wl->name);\n'
new = '\t\t\tlen += sysfs_emit_at(buf, len, "%s ", wl->name);\n'
if old in text:
    text = replace_once(text, old, new, "wakelock sysfs writer")
    wakelock.write_text(text)
    repairs.append("kernel/power/wakelock.c=converted-stale-str-end-writer")
elif text.count(new) != 1:
    raise SystemExit("wakelock writer is neither old nor repaired")

# Two independently merged helpers received the same name but different
# signatures. Rename only the kobject release callback; keep the policy-level
# sugov_tunables_free() and its callers unchanged.
schedutil = root / "kernel/sched/cpufreq_schedutil.c"
text = schedutil.read_text()
old_sig = "static void sugov_tunables_free(struct kobject *kobj)\n"
new_sig = "static void sugov_tunables_release(struct kobject *kobj)\n"
old_release = "\t.release = &sugov_tunables_free,\n"
new_release = "\t.release = &sugov_tunables_release,\n"
if old_sig in text:
    text = replace_once(text, old_sig, new_sig, "schedutil kobject release signature")
    text = replace_once(text, old_release, new_release, "schedutil kobject release binding")
    schedutil.write_text(text)
    repairs.append("kernel/sched/cpufreq_schedutil.c=renamed-kobject-release-callback")
elif text.count(new_sig) != 1 or text.count(new_release) != 1:
    raise SystemExit("schedutil kobject release callback is neither old nor repaired")

# Exact postconditions.
private_text = private.read_text()
crypto_text = crypto.read_text()
wakelock_text = wakelock.read_text()
schedutil_text = schedutil.read_text()
if private_text.count(declaration) != 1:
    raise SystemExit("fscrypt_d_ops declaration repair failed")
if crypto_text.count(function_marker) != 1 or crypto_text.count(ops_marker) != 1:
    raise SystemExit("fscrypt_d_ops implementation repair failed")
if old in wakelock_text or wakelock_text.count(new) != 1:
    raise SystemExit("wakelock writer repair failed")
if schedutil_text.count(new_sig) != 1 or schedutil_text.count(new_release) != 1:
    raise SystemExit("schedutil release callback repair failed")
if schedutil_text.count("static void sugov_tunables_free(struct sugov_tunables *tunables)\n") != 1:
    raise SystemExit("schedutil policy-level free helper changed unexpectedly")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "Late Linux $TARGET_VERSION compile compatibility repairs applied"
