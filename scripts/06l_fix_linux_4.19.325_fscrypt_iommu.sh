#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/fscrypt-iommu-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying fscrypt dentry and ARM IOMMU compatibility repairs"
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


# Samsung already provides fscrypt_handle_d_move() in include/linux/fscrypt.h.
# Linux stable also inserted a local copy in dcache.c, causing a redefinition in
# this combined tree. Keep the shared header helper and remove only the local
# duplicate.
header = (root / "include/linux/fscrypt.h").read_text()
if header.count("static inline void fscrypt_handle_d_move(struct dentry *dentry)") != 1:
    raise SystemExit("unexpected fscrypt header helper definition count")

path = root / "fs/dcache.c"
text = path.read_text()
marker = "static inline void fscrypt_handle_d_move(struct dentry *dentry)"
if marker in text:
    start = text.rfind("/*", 0, text.index(marker))
    if start < 0:
        raise SystemExit("fscrypt dcache comment start not found")
    body_start = text.index(marker, start)
    open_brace = text.index("{", body_start)
    close_brace = text.index("\n}", open_brace) + 2
    end = close_brace
    while end < len(text) and text[end] == "\n":
        end += 1
    block = text[start:end]
    if "DCACHE_ENCRYPTED_NAME" not in block or "d_splice_alias" not in block:
        raise SystemExit("unexpected fscrypt dcache helper block")
    text = text[:start] + text[end:]
    path.write_text(text)
    repairs.append("fs/dcache.c=removed-header-duplicate-fscrypt-helper")


# Samsung's ARM LPAE implementation tracks child-table references, while the
# later stable code requires the full arm_lpae_io_pgtable object to encode the
# table address. Merge both requirements: pass data plus the reference count,
# and derive cfg inside the helper.
path = root / "drivers/iommu/io-pgtable-arm.c"
text = path.read_text()
old_signature = (
    "static arm_lpae_iopte arm_lpae_install_table(arm_lpae_iopte *table,\n"
    "\t\t\t\t\t     arm_lpae_iopte *ptep,\n"
    "\t\t\t\t\t     arm_lpae_iopte curr,\n"
    "\t\t\t\t\t     struct io_pgtable_cfg *cfg,\n"
    "\t\t\t\t\t     int ref_count)\n"
    "{\n"
    "\tarm_lpae_iopte old, new;\n"
    "\tstruct io_pgtable_cfg *cfg = &data->iop.cfg;\n"
)
new_signature = (
    "static arm_lpae_iopte arm_lpae_install_table(arm_lpae_iopte *table,\n"
    "\t\t\t\t\t     arm_lpae_iopte *ptep,\n"
    "\t\t\t\t\t     arm_lpae_iopte curr,\n"
    "\t\t\t\t\t     struct arm_lpae_io_pgtable *data,\n"
    "\t\t\t\t\t     int ref_count)\n"
    "{\n"
    "\tarm_lpae_iopte old, new;\n"
    "\tstruct io_pgtable_cfg *cfg = &data->iop.cfg;\n"
)
if old_signature in text:
    text = replace_once(text, old_signature, new_signature, "ARM LPAE installer signature")
    text = replace_once(
        text,
        "arm_lpae_install_table(cptep, ptep, 0, cfg, 0)",
        "arm_lpae_install_table(cptep, ptep, 0, data, 0)",
        "ARM LPAE map installer call",
    )
    text = replace_once(
        text,
        "arm_lpae_install_table(tablep, ptep, blk_pte, cfg, child_cnt)",
        "arm_lpae_install_table(tablep, ptep, blk_pte, data, child_cnt)",
        "ARM LPAE split installer call",
    )
    path.write_text(text)
    repairs.append("drivers/iommu/io-pgtable-arm.c=merged-data-and-refcount-installer-api")


# Exact postconditions.
dcache = (root / "fs/dcache.c").read_text()
if marker in dcache:
    raise SystemExit("local fscrypt_handle_d_move definition remains in dcache")
if (root / "include/linux/fscrypt.h").read_text().count(marker) != 1:
    raise SystemExit("shared fscrypt helper validation failed")

iommu = (root / "drivers/iommu/io-pgtable-arm.c").read_text()
start = iommu.index("static arm_lpae_iopte arm_lpae_install_table(")
end = iommu.index("\nstruct map_state", start)
installer = iommu[start:end]
if "struct arm_lpae_io_pgtable *data" not in installer:
    raise SystemExit("ARM LPAE installer data argument is missing")
if installer.count("struct io_pgtable_cfg *cfg") != 1:
    raise SystemExit("ARM LPAE installer cfg declaration validation failed")
if "paddr_to_iopte(__pa(table), data)" not in installer:
    raise SystemExit("ARM LPAE table-address encoding validation failed")
if "iopte_tblcnt_set(&new, ref_count);" not in installer:
    raise SystemExit("Samsung ARM LPAE table reference count was lost")
if iommu.count("arm_lpae_install_table(cptep, ptep, 0, data, 0)") != 1:
    raise SystemExit("ARM LPAE map call validation failed")
if iommu.count("arm_lpae_install_table(tablep, ptep, blk_pte, data, child_cnt)") != 1:
    raise SystemExit("ARM LPAE split call validation failed")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "fscrypt dentry and ARM IOMMU compatibility repairs applied"
