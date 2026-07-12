#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

REPORT="$ARTIFACTS_DIR/compile-api-fix-4.19.200.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "4.19.200" || fail "Expected Linux 4.19.200 before compile API repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()


def replace_once_in_segment(path, start_marker, end_marker, old, new, label):
    text = path.read_text()
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    segment = text[start:end]
    count = segment.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    segment = segment.replace(old, new, 1)
    path.write_text(text[:start] + segment + text[end:])
    print(f"applied={label}")


def replace_once(path, old, new, label):
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))
    print(f"applied={label}")


verifier = root / "kernel/bpf/verifier.c"
replace_once_in_segment(
    verifier,
    "static int adjust_ptr_min_max_vals(",
    "\nstatic int adjust_scalar_min_max_vals(",
    "\tu32 dst = insn->dst_reg;\n",
    "\tu32 dst = insn->dst_reg, src = insn->src_reg;\n",
    "BPF source-register declaration",
)

namei = root / "fs/ext4/namei.c"
namei_text = namei.read_text()
for required in [
    "\text4_lblk_t lblk;\n",
    "int *inlined, ext4_lblk_t *lblk)",
]:
    if required not in namei_text:
        raise SystemExit(f"ext4 vendor API prerequisite missing: {required!r}")

replace_once(
    namei,
    "\told.bh = ext4_find_entry(old.dir, &old.dentry->d_name, &old.de, NULL);\n",
    "\told.bh = ext4_find_entry(old.dir, &old.dentry->d_name,\n"
    "\t\t\t\t &old.de, NULL, &old.lblk);\n",
    "ext4 reset-entry logical-block argument",
)

mmc = root / "drivers/mmc/core/mmc.c"
mmc_text = mmc.read_text()
helper = "static bool _mmc_cache_enabled(struct mmc_host *host)"
field = "\t.cache_enabled = _mmc_cache_enabled,\n"
if helper not in mmc_text:
    raise SystemExit("MMC cache helper prerequisite missing")
if mmc_text.count(field) != 1:
    raise SystemExit(
        f"MMC misplaced cache field: expected one fuzzy-applied field, found {mmc_text.count(field)}"
    )
mmc_text = mmc_text.replace(field, "", 1)
old_ops = (
    "\t.hw_reset = _mmc_hw_reset,\n"
    "\t.change_bus_speed = mmc_change_bus_speed,\n"
)
new_ops = (
    "\t.hw_reset = _mmc_hw_reset,\n"
    "\t.cache_enabled = _mmc_cache_enabled,\n"
    "\t.change_bus_speed = mmc_change_bus_speed,\n"
)
if mmc_text.count(old_ops) != 1:
    raise SystemExit(
        f"MMC bus-ops insertion point: expected one match, found {mmc_text.count(old_ops)}"
    )
mmc.write_text(mmc_text.replace(old_ops, new_ops, 1))
print("applied=MMC cache callback placement")

ffs = root / "drivers/usb/gadget/function/f_fs.c"
replace_once_in_segment(
    ffs,
    "static inline struct f_fs_opts *ffs_do_functionfs_bind(",
    "\nstatic int _ffs_func_bind(",
    "\tstruct ffs_data *ffs_data;\n",
    "\tstruct ffs_data *ffs, *ffs_data;\n",
    "FunctionFS vendor log pointer declaration",
)
replace_once_in_segment(
    ffs,
    "static inline struct f_fs_opts *ffs_do_functionfs_bind(",
    "\nstatic int _ffs_func_bind(",
    "\tfunc->ffs = ffs_data;\n",
    "\tfunc->ffs = ffs_data;\n\tffs = ffs_data;\n",
    "FunctionFS vendor log pointer assignment",
)

verifier_text = verifier.read_text()
adjust_start = verifier_text.index("static int adjust_ptr_min_max_vals(")
adjust_end = verifier_text.index("\nstatic int adjust_scalar_min_max_vals(", adjust_start)
adjust = verifier_text[adjust_start:adjust_end]
if "u32 dst = insn->dst_reg, src = insn->src_reg;" not in adjust:
    raise SystemExit("BPF source-register postcondition failed")

namei_text = namei.read_text()
if "&old.de, NULL, &old.lblk);" not in namei_text:
    raise SystemExit("ext4 logical-block postcondition failed")

mmc_text = mmc.read_text()
ops_start = mmc_text.index("static const struct mmc_bus_ops mmc_ops = {")
ops_end = mmc_text.index("\n};", ops_start)
ops = mmc_text[ops_start:ops_end]
if field.strip() not in ops:
    raise SystemExit("MMC cache callback postcondition failed")
outside_ops = mmc_text[:ops_start] + mmc_text[ops_end:]
if field in outside_ops:
    raise SystemExit("MMC cache callback remains outside mmc_ops")

ffs_text = ffs.read_text()
ffs_start = ffs_text.index("static inline struct f_fs_opts *ffs_do_functionfs_bind(")
ffs_end = ffs_text.index("\nstatic int _ffs_func_bind(", ffs_start)
ffs_bind = ffs_text[ffs_start:ffs_end]
if "struct ffs_data *ffs, *ffs_data;" not in ffs_bind:
    raise SystemExit("FunctionFS vendor log declaration postcondition failed")
if "func->ffs = ffs_data;\n\tffs = ffs_data;" not in ffs_bind:
    raise SystemExit("FunctionFS vendor log assignment postcondition failed")
if 'ffs_log("functionfs_bind returned %d", ret);' not in ffs_bind:
    raise SystemExit("FunctionFS vendor log call postcondition failed")

print("result=linux-4.19.200-compile-api-repairs-complete")
PY

{
  echo 'target=4.19.200'
  echo 'bpf_src_register=restored'
  echo 'ext4_resetent_lblk=passed'
  echo 'mmc_cache_callback=relocated'
  echo 'functionfs_log_pointer=restored'
  echo 'result=compile-api-compatible'
} | tee "$REPORT"

info "Linux 4.19.200 compile API mismatches repaired"
