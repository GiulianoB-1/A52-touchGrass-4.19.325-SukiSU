#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
HEADER="$KERNEL_DIR/include/linux/bpf_verifier.h"
VERIFIER="$KERNEL_DIR/kernel/bpf/verifier.c"
SYSCALL="$KERNEL_DIR/kernel/bpf/syscall.c"
REPORT="$ARTIFACTS_DIR/bpf-verifier-repair.txt"
PATCH_OUT="$ARTIFACTS_DIR/bpf-verifier-repair.patch"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before BPF repair"
test -f "$ARTIFACTS_DIR/apply-result-v4.19.153.txt" || fail "Linux 4.19.153 update result is missing"
grep -q '^apply_exit=0$' "$ARTIFACTS_DIR/apply-result-v4.19.153.txt" || fail "Linux 4.19.153 update did not complete successfully"
test -f "$HEADER" || fail "Missing $HEADER"
test -f "$VERIFIER" || fail "Missing $VERIFIER"
test -f "$SYSCALL" || fail "Missing $SYSCALL"

cp "$HEADER" "$ARTIFACTS_DIR/bpf_verifier.h.before"
cp "$VERIFIER" "$ARTIFACTS_DIR/verifier.c.before"
cp "$SYSCALL" "$ARTIFACTS_DIR/syscall.c.before"

python3 - "$HEADER" "$VERIFIER" "$SYSCALL" <<'PY'
from pathlib import Path
import re
import sys

header_path = Path(sys.argv[1])
verifier_path = Path(sys.argv[2])
syscall_path = Path(sys.argv[3])
header = header_path.read_text()
verifier = verifier_path.read_text()
syscall = syscall_path.read_text()


def sub_once(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return updated


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


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

# The vendor BPF attach backport called sock_map_get_from_fd() before prog was
# initialized and left ptype unset for SK_MSG/SK_SKB attach types. Restore the
# upstream type-selection flow, adapted to the vendor helper name/signature.
syscall = replace_once(
    syscall,
    "\tcase BPF_SK_MSG_VERDICT:\n"
    "\t\tret = sock_map_get_from_fd(attr, prog);\n"
    "\t\tbreak;\n"
    "\tcase BPF_SK_SKB_STREAM_PARSER:\n"
    "\tcase BPF_SK_SKB_STREAM_VERDICT:\n"
    "\t\tret = sock_map_get_from_fd(attr, prog);\n"
    "\t\tbreak;\n",
    "\tcase BPF_SK_MSG_VERDICT:\n"
    "\t\tptype = BPF_PROG_TYPE_SK_MSG;\n"
    "\t\tbreak;\n"
    "\tcase BPF_SK_SKB_STREAM_PARSER:\n"
    "\tcase BPF_SK_SKB_STREAM_VERDICT:\n"
    "\t\tptype = BPF_PROG_TYPE_SK_SKB;\n"
    "\t\tbreak;\n",
    "restore socket-map attach type selection",
)

# The same backport omitted the break after BPF_LIRC_MODE2, causing it to fall
# through and be treated as a cgroup-sysctl program.
syscall = replace_once(
    syscall,
    "\tcase BPF_LIRC_MODE2:\n"
    "\t\tptype = BPF_PROG_TYPE_LIRC_MODE2;\n"
    "\tcase BPF_CGROUP_SYSCTL:\n",
    "\tcase BPF_LIRC_MODE2:\n"
    "\t\tptype = BPF_PROG_TYPE_LIRC_MODE2;\n"
    "\t\tbreak;\n"
    "\tcase BPF_CGROUP_SYSCTL:\n",
    "restore LIRC attach-type break",
)

header_path.write_text(header)
verifier_path.write_text(verifier)
syscall_path.write_text(syscall)
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
grep -Fq 'ptype = BPF_PROG_TYPE_SK_MSG;' "$SYSCALL" || fail "SK_MSG attach type repair is missing"
grep -Fq 'ptype = BPF_PROG_TYPE_SK_SKB;' "$SYSCALL" || fail "SK_SKB attach type repair is missing"
! grep -Fq $'case BPF_SK_MSG_VERDICT:\n\t\tret = sock_map_get_from_fd(attr, prog);' "$SYSCALL" || fail "Uninitialized sock-map attach call remains"

git -C "$KERNEL_DIR" diff --check -- include/linux/bpf_verifier.h kernel/bpf/verifier.c kernel/bpf/syscall.c
git -C "$KERNEL_DIR" diff --binary -- include/linux/bpf_verifier.h kernel/bpf/verifier.c kernel/bpf/syscall.c > "$PATCH_OUT"
test -s "$PATCH_OUT" || fail "BPF repair produced no patch"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'removed_dead_flag=REG_LIVE_DONE\n'
  printf 'unified_id_map_type=struct bpf_id_pair\n'
  printf 'unified_id_map_size=BPF_ID_MAP_SIZE\n'
  printf 'restored_guard=env->explore_alu_limits\n'
  printf 'initialized_ctx_access=upstream-branch-semantics\n'
  printf 'repaired_attach_types=SK_MSG,SK_SKB,LIRC_MODE2\n'
  printf 'changed_files=include/linux/bpf_verifier.h,kernel/bpf/verifier.c,kernel/bpf/syscall.c\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "BPF verifier and syscall source repair completed"
