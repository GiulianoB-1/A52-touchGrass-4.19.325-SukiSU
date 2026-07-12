#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/vendor-subsystem-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before vendor subsystem repair"

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


# The direct merge retained the old stable fname.c while preserving Samsung's
# substantially newer fscrypt_info and public filename APIs. Restore the exact
# touchGrass implementation that matches those headers and data structures.
fname_path = root / "fs/crypto/fname.c"
fname_vendor = git_blob(touchgrass, "fs/crypto/fname.c")
if fname_path.read_text() != fname_vendor:
    fname_path.write_text(fname_vendor)
    repairs.append("fs/crypto/fname.c=restored-touchgrass-fscrypt-api-match")

# task_mmu.c, base.c and root.c retain Samsung PAGE_BOOST and LMKD extensions.
# Their shared definitions were lost when internal.h came from the stable side.
# Restore the matching private procfs ABI, then retain the stable helper needed
# by the merged generic.c implementation.
proc_internal = root / "fs/proc/internal.h"
proc_vendor = git_blob(touchgrass, "fs/proc/internal.h")
if proc_internal.read_text() != proc_vendor:
    proc_internal.write_text(proc_vendor)
    repairs.append("fs/proc/internal.h=restored-touchgrass-private-proc-abi")

proc_text = proc_internal.read_text()
generic_text = (root / "fs/proc/generic.c").read_text()
force_lookup = '''static inline void pde_force_lookup(struct proc_dir_entry *pde)
{
\t/* /proc/net entries can change under setns(CLONE_NEWNET). */
\tpde->proc_dops = &proc_net_dentry_ops;
}
'''
if "pde_force_lookup(" in generic_text and "static inline void pde_force_lookup(" not in proc_text:
    anchor = "extern const struct inode_operations proc_net_inode_operations;\n"
    if proc_text.count(anchor) != 1:
        raise SystemExit("proc_net operations insertion anchor mismatch")
    proc_text = proc_text.replace(anchor, anchor + force_lookup, 1)
    proc_internal.write_text(proc_text)
    repairs.append("fs/proc/internal.h=retained-stable-pde-force-lookup")

# io-pgtable-arm.c belongs to the Samsung tree shape, whose public definitions
# live in include/linux/io-pgtable.h. The merge left a local-header include even
# though neither source side contains drivers/iommu/io-pgtable.h.
io_arm = root / "drivers/iommu/io-pgtable-arm.c"
io_text = io_arm.read_text()
old_include = '#include "io-pgtable.h"\n'
new_include = '#include <linux/io-pgtable.h>\n'
if old_include in io_text:
    if io_text.count(old_include) != 1:
        raise SystemExit("unexpected local io-pgtable include count")
    io_arm.write_text(io_text.replace(old_include, new_include, 1))
    repairs.append("drivers/iommu/io-pgtable-arm.c=used-public-page-table-header")
elif io_text.count(new_include) != 1:
    raise SystemExit("IOMMU page-table include is neither local nor repaired")

# Samsung SD bus operations still initialize these callbacks. Preserve them in
# the merged bus-ops structure alongside Linux stable's cache_enabled callback.
core_h = root / "drivers/mmc/core/core.h"
core_text = core_h.read_text()
start = core_text.index("struct mmc_bus_ops {\n")
end = core_text.index("};\n", start) + 3
block = core_text[start:end]
fields = (
    ("\tint (*deferred_resume)(struct mmc_host *host);\n",
     "\tint (*resume)(struct mmc_host *);\n"),
    ("\tint (*change_bus_speed)(struct mmc_host *host, unsigned long *freq);\n",
     "\tbool (*cache_enabled)(struct mmc_host *);\n"),
    ("\tint (*change_bus_speed_deferred)(struct mmc_host *host,\n"
     "\t\t\t\t\t\t\tunsigned long *freq);\n",
     "\tint (*change_bus_speed)(struct mmc_host *host, unsigned long *freq);\n"),
)
for field, anchor in fields:
    if field not in block:
        if block.count(anchor) != 1:
            raise SystemExit(f"MMC bus-ops insertion anchor mismatch for {field.strip()!r}")
        block = block.replace(anchor, anchor + field, 1)
        repairs.append("drivers/mmc/core/core.h=restored-" + field.strip().split("(")[1].split(")")[0])
core_text = core_text[:start] + block + core_text[end:]
core_h.write_text(core_text)

# Exact postconditions for the compile errors addressed by this pass.
fname_final = fname_path.read_text()
for required in (
    "const struct fscrypt_info *ci = inode->i_crypt_info;",
    "struct crypto_skcipher *tfm = ci->ci_key.tfm;",
    "fscrypt_policy_flags(&ci->ci_policy)",
    "struct fscrypt_nokey_name",
):
    if required not in fname_final:
        raise SystemExit(f"touchGrass fscrypt filename postcondition missing: {required}")

proc_final = proc_internal.read_text()
for required in (
    "#define MAX_PAGE_BOOST_FILEPATH_LEN 256",
    "struct proc_filemap_private {",
    "extern int proc_pid_statlmkd(",
    "extern const struct file_operations proc_reclaim_operations;",
):
    if required not in proc_final:
        raise SystemExit(f"touchGrass procfs ABI postcondition missing: {required}")
if "pde_force_lookup(" in generic_text and "static inline void pde_force_lookup(" not in proc_final:
    raise SystemExit("stable procfs force-lookup helper was not retained")

io_final = io_arm.read_text()
if old_include in io_final or io_final.count(new_include) != 1:
    raise SystemExit("public IOMMU page-table include repair failed")
if not (root / "include/linux/io-pgtable.h").exists():
    raise SystemExit("public Linux IOMMU page-table header is missing")

core_final = core_h.read_text()
core_start = core_final.index("struct mmc_bus_ops {\n")
core_end = core_final.index("};\n", core_start) + 3
core_block = core_final[core_start:core_end]
for field, _ in fields:
    if core_block.count(field) != 1:
        raise SystemExit(f"MMC bus-ops callback postcondition failed: {field.strip()!r}")
if core_block.count("\tbool (*cache_enabled)(struct mmc_host *);\n") != 1:
    raise SystemExit("Linux stable MMC cache_enabled callback disappeared")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  fs/crypto/fname.c fs/proc/internal.h \
  drivers/iommu/io-pgtable-arm.c drivers/mmc/core/core.h

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'fscrypt=touchgrass-filename-api-preserved\n'
  printf 'procfs=touchgrass-private-abi-plus-stable-net-lookup\n'
  printf 'iommu=public-page-table-header-used\n'
  printf 'mmc=vendor-bus-callbacks-restored\n'
  printf 'result=linux-4.19.325-vendor-subsystem-compatibility-repaired\n'
} | tee -a "$REPORT"

info "Linux $TARGET_VERSION vendor subsystem compatibility repaired"
