#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
HEADER="$KERNEL_DIR/include/linux/bpf_verifier.h"
VERIFIER="$KERNEL_DIR/kernel/bpf/verifier.c"
REPORT="$ARTIFACTS_DIR/bpf-verifier-repair.txt"
PATCH_OUT="$ARTIFACTS_DIR/bpf-verifier-repair.patch"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before BPF repair"
test -f "$ARTIFACTS_DIR/apply-result-v4.19.153.txt" || fail "Linux 4.19.153 update result is missing"
grep -q '^apply_exit=0$' "$ARTIFACTS_DIR/apply-result-v4.19.153.txt" || fail "Linux 4.19.153 update did not complete successfully"
test -f "$HEADER" || fail "Missing $HEADER"
test -f "$VERIFIER" || fail "Missing $VERIFIER"

cp "$HEADER" "$ARTIFACTS_DIR/bpf_verifier.h.before"
cp "$VERIFIER" "$ARTIFACTS_DIR/verifier.c.before"

python3 - "$HEADER" "$VERIFIER" <<'PY'
from pathlib import Path
import re
import sys

header_path = Path(sys.argv[1])
verifier_path = Path(sys.argv[2])
header = header_path.read_text()
verifier = verifier_path.read_text()


def sub_once(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return updated

# REG_LIVE_DONE was added locally, checked in two places, but never set.
header = sub_once(
    header,
    r"^[ \t]*REG_LIVE_DONE[ \t]*=[ \t]*4,[^\n]*\n",
    "",
    "remove REG_LIVE_DONE enum",
    re.MULTILINE,
)

verifier = sub_once(
    verifier,
    r"[ \t]*/\* stop traversal if already fully propagated upward \*/\n"
    r"[ \t]*if \(parent->frame\[parent->curframe\]->regs\[regno\]\.live & REG_LIVE_DONE\)\n"
    r"[ \t]*break;\n",
    "",
    "remove dead register REG_LIVE_DONE check",
)

verifier = sub_once(
    verifier,
    r"[ \t]*/\* stop traversal if already fully propagated \*/\n"
    r"[ \t]*if \(parent->frame\[frameno\]->stack\[slot\]\.spilled_ptr\.live & REG_LIVE_DONE\)\n"
    r"[ \t]*break;\n",
    "",
    "remove dead stack REG_LIVE_DONE check",
)

# The header already provides the official bpf_id_pair and BPF_ID_MAP_SIZE.
# Remove the duplicate local type and size macro from verifier.c.
verifier = sub_once(
    verifier,
    r"/\* Maximum number of register states that can exist at once \*/\n"
    r"#define ID_MAP_SIZE[^\n]*\n"
    r"struct idpair \{\n"
    r"[ \t]*u32 old;\n"
    r"[ \t]*u32 cur;\n"
    r"\};\n\n",
    "",
    "remove duplicate id-map definition",
)

verifier = sub_once(
    verifier,
    r"static bool check_ids\(u32 old_id, u32 cur_id, struct idpair \*idmap\)",
    "static bool check_ids(u32 old_id, u32 cur_id, struct bpf_id_pair *idmap)",
    "convert check_ids to bpf_id_pair",
)

verifier = sub_once(
    verifier,
    r"for \(i = 0; i < ID_MAP_SIZE; i\+\+\)",
    "for (i = 0; i < BPF_ID_MAP_SIZE; i++)",
    "convert ID_MAP_SIZE use",
)

# touchGrass added explore_alu_limits to the environment but omitted the
# matching state-pruning guard used by the official stable verifier.
verifier = sub_once(
    verifier,
    r"(\tcase SCALAR_VALUE:\n)(\t\tif \(rcur->type == SCALAR_VALUE\) \{)",
    r"\1\t\tif (env->explore_alu_limits)\n\t\t\treturn false;\n\2",
    "restore explore_alu_limits pruning guard",
)

# A later BPF backport introduced ctx_access but omitted the assignments
# present in upstream. Restore the upstream branch semantics so the variable
# is initialized on every path that reaches its use.
verifier = sub_once(
    verifier,
    r"(\t\tif \(insn->code == \(BPF_LDX \| BPF_MEM \| BPF_B\) \|\|\n"
    r"\t\t    insn->code == \(BPF_LDX \| BPF_MEM \| BPF_H\) \|\|\n"
    r"\t\t    insn->code == \(BPF_LDX \| BPF_MEM \| BPF_W\) \|\|\n"
    r"\t\t    insn->code == \(BPF_LDX \| BPF_MEM \| BPF_DW\)\)\n"
    r"\t\t\ttype = BPF_READ;\n"
    r"\t\telse if \(insn->code == \(BPF_STX \| BPF_MEM \| BPF_B\) \|\|\n"
    r"\t\t\t insn->code == \(BPF_STX \| BPF_MEM \| BPF_H\) \|\|\n"
    r"\t\t\t insn->code == \(BPF_STX \| BPF_MEM \| BPF_W\) \|\|\n"
    r"\t\t\t insn->code == \(BPF_STX \| BPF_MEM \| BPF_DW\)\)\n"
    r"\t\t\ttype = BPF_WRITE;\n"
    r"\t\telse\n"
    r"\t\t\tcontinue;)",
    "\t\tif (insn->code == (BPF_LDX | BPF_MEM | BPF_B) ||\n"
    "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_H) ||\n"
    "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_W) ||\n"
    "\t\t    insn->code == (BPF_LDX | BPF_MEM | BPF_DW)) {\n"
    "\t\t\ttype = BPF_READ;\n"
    "\t\t\tctx_access = true;\n"
    "\t\t} else if (insn->code == (BPF_STX | BPF_MEM | BPF_B) ||\n"
    "\t\t\t   insn->code == (BPF_STX | BPF_MEM | BPF_H) ||\n"
    "\t\t\t   insn->code == (BPF_STX | BPF_MEM | BPF_W) ||\n"
    "\t\t\t   insn->code == (BPF_STX | BPF_MEM | BPF_DW)) {\n"
    "\t\t\ttype = BPF_WRITE;\n"
    "\t\t\tctx_access = BPF_CLASS(insn->code) == BPF_STX;\n"
    "\t\t} else {\n"
    "\t\t\tcontinue;\n"
    "\t\t}",
    "initialize ctx_access using upstream branch semantics",
)

header_path.write_text(header)
verifier_path.write_text(verifier)
PY

# Strict post-repair checks.
! grep -RFn 'REG_LIVE_DONE' "$HEADER" "$VERIFIER" || fail "REG_LIVE_DONE remains after repair"
! grep -Fq 'struct idpair' "$VERIFIER" || fail "Duplicate struct idpair remains"
! grep -Eq '(^|[^A-Z_])ID_MAP_SIZE([^A-Z_]|$)' "$VERIFIER" || fail "Legacy ID_MAP_SIZE remains"
grep -Fq 'struct bpf_id_pair *idmap' "$VERIFIER" || fail "bpf_id_pair conversion is missing"
grep -Fq 'i < BPF_ID_MAP_SIZE' "$VERIFIER" || fail "BPF_ID_MAP_SIZE use is missing"
grep -Fq 'if (env->explore_alu_limits)' "$VERIFIER" || fail "explore_alu_limits guard is missing"
grep -Fq 'ctx_access = true;' "$VERIFIER" || fail "ctx_access read assignment is missing"
grep -Fq 'ctx_access = BPF_CLASS(insn->code) == BPF_STX;' "$VERIFIER" || fail "ctx_access write assignment is missing"
grep -Fq 'struct bpf_id_pair idmap_scratch[BPF_ID_MAP_SIZE];' "$HEADER" || fail "Header id-map scratch definition is missing"

git -C "$KERNEL_DIR" diff --check -- include/linux/bpf_verifier.h kernel/bpf/verifier.c
git -C "$KERNEL_DIR" diff --binary -- include/linux/bpf_verifier.h kernel/bpf/verifier.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "BPF repair produced no patch"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'removed_dead_flag=REG_LIVE_DONE\n'
  printf 'unified_id_map_type=struct bpf_id_pair\n'
  printf 'unified_id_map_size=BPF_ID_MAP_SIZE\n'
  printf 'restored_guard=env->explore_alu_limits\n'
  printf 'initialized_ctx_access=upstream-branch-semantics\n'
  printf 'changed_files=include/linux/bpf_verifier.h,kernel/bpf/verifier.c\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "BPF verifier source repair completed"
