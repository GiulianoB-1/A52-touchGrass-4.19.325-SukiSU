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

verifier_text = verifier.read_text()
adjust_start = verifier_text.index("static int adjust_ptr_min_max_vals(")
adjust_end = verifier_text.index("\nstatic int adjust_scalar_min_max_vals(", adjust_start)
adjust = verifier_text[adjust_start:adjust_end]
if "u32 dst = insn->dst_reg, src = insn->src_reg;" not in adjust:
    raise SystemExit("BPF source-register postcondition failed")

namei_text = namei.read_text()
if "&old.de, NULL, &old.lblk);" not in namei_text:
    raise SystemExit("ext4 logical-block postcondition failed")

print("result=linux-4.19.200-compile-api-repairs-complete")
PY

{
  echo 'target=4.19.200'
  echo 'bpf_src_register=restored'
  echo 'ext4_resetent_lblk=passed'
  echo 'result=compile-api-compatible'
} | tee "$REPORT"

info "Linux 4.19.200 compile API mismatches repaired"
