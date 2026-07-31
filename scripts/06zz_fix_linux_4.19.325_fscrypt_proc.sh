#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/fscrypt-proc-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before fscrypt/proc repair"

python3 - "$KERNEL_DIR" "$TOUCHGRASS_COMMIT" "$REPORT" <<'PY'
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
touchgrass = sys.argv[2]
report = Path(sys.argv[3])
repairs = []


def git_blob(commit: str, path: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        text=True,
    )


# fname.c and the surrounding fscrypt data structures were restored from the
# touchGrass side. Restore its matching object list as well; the transient
# stable Makefile still requested removed keyinfo.o.
crypto_makefile = root / "fs/crypto/Makefile"
vendor_makefile = git_blob(touchgrass, "fs/crypto/Makefile")
if crypto_makefile.read_text() != vendor_makefile:
    crypto_makefile.write_text(vendor_makefile)
    repairs.append("fs/crypto/Makefile=restored-touchgrass-object-set")

required_fscrypt_sources = (
    "crypto.c", "fname.c", "hkdf.c", "hooks.c", "keyring.c",
    "keysetup.c", "keysetup_v1.c", "policy.c",
)
for name in required_fscrypt_sources:
    if not (root / "fs/crypto" / name).is_file():
        raise SystemExit(f"touchGrass fscrypt source is missing: {name}")
if "keyinfo.o" in crypto_makefile.read_text():
    raise SystemExit("obsolete fscrypt keyinfo.o remains in Makefile")


# 06b temporarily supplied fscrypt_d_revalidate() for the old merged fname.c.
# The later vendor-subsystem pass restores touchGrass fname.c, which contains the
# native exported implementation. Remove only the injected crypto.c function and
# retain fscrypt_d_ops there so hooks.c still has the expected operations table.
crypto_source = root / "fs/crypto/crypto.c"
crypto_text = crypto_source.read_text()
fname_text = (root / "fs/crypto/fname.c").read_text()
revalidate_sig = "int fscrypt_d_revalidate(struct dentry *dentry, unsigned int flags)\n"
injected_revalidate = '''/*
 * Validate dentries in encrypted directories to make sure we aren't
 * potentially caching stale dentries after a key has been added.
 */
int fscrypt_d_revalidate(struct dentry *dentry, unsigned int flags)
{
\tstruct dentry *dir;
\tint err;
\tint valid;

\tif (!(dentry->d_flags & DCACHE_ENCRYPTED_NAME))
\t\treturn 1;

\tif (flags & LOOKUP_RCU)
\t\treturn -ECHILD;

\tdir = dget_parent(dentry);
\terr = fscrypt_get_encryption_info(d_inode(dir));
\tvalid = !fscrypt_has_encryption_key(d_inode(dir));
\tdput(dir);

\tif (err < 0)
\t\treturn err;

\treturn valid;
}

'''
if fname_text.count(revalidate_sig) != 1:
    raise SystemExit("touchGrass fname.c native fscrypt_d_revalidate is missing or duplicated")
if injected_revalidate in crypto_text:
    if crypto_text.count(injected_revalidate) != 1:
        raise SystemExit("unexpected injected fscrypt_d_revalidate block count")
    crypto_text = crypto_text.replace(injected_revalidate, "", 1)
    crypto_source.write_text(crypto_text)
    repairs.append("fs/crypto/crypto.c=removed-duplicate-dentry-validator")
elif revalidate_sig in crypto_text:
    raise SystemExit("crypto.c contains an unrecognized fscrypt_d_revalidate implementation")


# The merged inode.c uses Linux 4.19.325's three-argument proc_fill_super(),
# while the Samsung mount path may still use the old one-argument prototype.
# Keep Samsung's explicit option validation, expose its parser to inode.c, and
# call the stable fill helper with no second parse. The prior 06z pass may have
# already installed the unnamed three-argument declaration, so accept it.
proc_internal = root / "fs/proc/internal.h"
internal = proc_internal.read_text()
old_fill_decl = "extern int proc_fill_super(struct super_block *);\n"
new_fill_decl = "extern int proc_fill_super(struct super_block *, void *, int);\n"
if old_fill_decl in internal:
    if internal.count(old_fill_decl) != 1:
        raise SystemExit("unexpected old proc_fill_super declaration count")
    internal = internal.replace(old_fill_decl, new_fill_decl, 1)
    repairs.append("fs/proc/internal.h=matched-stable-proc-fill-super-prototype")
elif internal.count(new_fill_decl) != 1:
    raise SystemExit("proc_fill_super declaration is neither old nor repaired")

net_inode_decl = "extern const struct inode_operations proc_net_inode_operations;\n"
net_dentry_decl = "extern const struct dentry_operations proc_net_dentry_ops;\n"
if "static inline void pde_force_lookup(" in internal and net_dentry_decl not in internal:
    if internal.count(net_inode_decl) != 1:
        raise SystemExit("proc_net inode declaration anchor mismatch")
    internal = internal.replace(net_inode_decl, net_inode_decl + net_dentry_decl, 1)
    repairs.append("fs/proc/internal.h=declared-proc-net-dentry-ops")
elif net_dentry_decl in internal and internal.count(net_dentry_decl) != 1:
    raise SystemExit("unexpected proc_net_dentry_ops declaration count")
proc_internal.write_text(internal)

proc_root = root / "fs/proc/root.c"
root_text = proc_root.read_text()
old_parser = "static int proc_parse_options(char *options, struct pid_namespace *pid)\n"
new_parser = "int proc_parse_options(char *options, struct pid_namespace *pid)\n"
if old_parser in root_text:
    if root_text.count(old_parser) != 1:
        raise SystemExit("unexpected static proc_parse_options count")
    root_text = root_text.replace(old_parser, new_parser, 1)
    repairs.append("fs/proc/root.c=exported-proc-option-parser")
elif root_text.count(new_parser) != 1:
    raise SystemExit("proc_parse_options definition is neither old nor repaired")

old_fill_call = "\t\terr = proc_fill_super(sb);\n"
new_fill_call = "\t\terr = proc_fill_super(sb, NULL, 0);\n"
if old_fill_call in root_text:
    if root_text.count(old_fill_call) != 1:
        raise SystemExit("unexpected old proc_fill_super call count")
    root_text = root_text.replace(old_fill_call, new_fill_call, 1)
    repairs.append("fs/proc/root.c=called-stable-proc-fill-super-abi")
elif root_text.count(new_fill_call) != 1:
    raise SystemExit("proc_fill_super call is neither old nor repaired")
proc_root.write_text(root_text)

# Exact postconditions.
final_makefile = crypto_makefile.read_text()
for obj in ("hkdf.o", "keyring.o", "keysetup.o", "keysetup_v1.o"):
    if obj not in final_makefile:
        raise SystemExit(f"touchGrass fscrypt object missing from Makefile: {obj}")

final_crypto = crypto_source.read_text()
if revalidate_sig in final_crypto:
    raise SystemExit("duplicate fscrypt_d_revalidate remains in crypto.c")
if final_crypto.count("const struct dentry_operations fscrypt_d_ops = {\n") != 1:
    raise SystemExit("fscrypt_d_ops table was lost or duplicated")
if (root / "fs/crypto/fname.c").read_text().count(revalidate_sig) != 1:
    raise SystemExit("native fscrypt_d_revalidate postcondition failed")

final_internal = proc_internal.read_text()
if final_internal.count(new_fill_decl) != 1:
    raise SystemExit("stable proc_fill_super declaration postcondition failed")
if "static inline void pde_force_lookup(" in final_internal and \
        final_internal.count(net_dentry_decl) != 1:
    raise SystemExit("proc_net_dentry_ops declaration postcondition failed")

final_root = proc_root.read_text()
if final_root.count(new_parser) != 1 or old_parser in final_root:
    raise SystemExit("proc_parse_options linkage postcondition failed")
if final_root.count(new_fill_call) != 1 or old_fill_call in final_root:
    raise SystemExit("proc_fill_super call postcondition failed")
if "if (!proc_parse_options(options, ns))" not in final_root:
    raise SystemExit("Samsung proc mount option validation was lost")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  fs/crypto/Makefile fs/crypto/crypto.c fs/crypto/fname.c \
  fs/proc/internal.h fs/proc/root.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'fscrypt=touchgrass-object-set-and-native-dentry-validator\n'
  printf 'procfs=stable-fill-super-abi-with-samsung-mount-validation\n'
  printf 'result=linux-4.19.325-fscrypt-proc-compatibility-repaired\n'
} | tee -a "$REPORT"

info "Linux $TARGET_VERSION fscrypt and procfs compatibility repaired"
