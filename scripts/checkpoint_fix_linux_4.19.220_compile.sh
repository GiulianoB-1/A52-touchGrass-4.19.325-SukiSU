#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.220
REPORT="$ARTIFACTS_DIR/compile-api-fix-$TARGET_VERSION.txt"
VERIFIER="$KERNEL_DIR/kernel/bpf/verifier.c"
FILE_TABLE="$KERNEL_DIR/fs/file_table.c"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"
test -f "$VERIFIER" || fail "BPF verifier source is missing"
test -f "$FILE_TABLE" || fail "File table source is missing"

info "Repairing Linux $TARGET_VERSION merge-shape compile mismatches"
python3 - "$VERIFIER" "$FILE_TABLE" "$REPORT" <<'PY'
from pathlib import Path
import sys

verifier = Path(sys.argv[1])
file_table = Path(sys.argv[2])
report = Path(sys.argv[3])
rows = []

# Preserve the reviewed 4.19.200 BPF socket-context backport. The 4.19.220
# three-way merge retained the selector switch but dropped its local function
# pointer and reintroduced a PTR_TO_CTX-only guard, making the socket cases
# unreachable.
text = verifier.read_text()
old = "\tfor (i = 0; i < insn_cnt; i++, insn++) {\n\t\tbool ctx_access;\n"
new = (
    "\tfor (i = 0; i < insn_cnt; i++, insn++) {\n"
    "\t\tbool ctx_access;\n"
    "\t\tbpf_convert_ctx_access_t convert_ctx_access;\n"
)
if text.count(old) != 1:
    raise SystemExit(
        "kernel/bpf/verifier.c: expected one convert_ctx_accesses loop declaration anchor"
    )
text = text.replace(old, new, 1)

stale_guard = (
    "\t\tif (env->insn_aux_data[i + delta].ptr_type != PTR_TO_CTX)\n"
    "\t\t\tcontinue;\n"
)
if text.count(stale_guard) != 1:
    raise SystemExit(
        "kernel/bpf/verifier.c: expected one stale PTR_TO_CTX-only guard"
    )
text = text.replace(stale_guard, "", 1)

if text.count("bpf_convert_ctx_access_t convert_ctx_access;") != 1:
    raise SystemExit("kernel/bpf/verifier.c: converter declaration count is not one")
if "convert_ctx_access = bpf_sock_convert_ctx_access;" not in text:
    raise SystemExit("kernel/bpf/verifier.c: socket converter selection is missing")
if "convert_ctx_access = bpf_tcp_sock_convert_ctx_access;" not in text:
    raise SystemExit("kernel/bpf/verifier.c: TCP socket converter selection is missing")
verifier.write_text(text)
rows.append("bpf_converter_pointer=restored\n")
rows.append("bpf_socket_context_paths=reachable\n")

# Linux 4.19.220 introduced fput_many(file, refs). The merge retained its body
# but attached the old fput(file) signature, then also retained the wrapper,
# producing an undefined refs variable and duplicate fput symbol.
text = file_table.read_text()
broken = (
    "void fput(struct file *file)\n"
    "{\n"
    "\tif (atomic_long_sub_and_test(refs, &file->f_count)) {\n"
)
fixed = (
    "void fput_many(struct file *file, unsigned int refs)\n"
    "{\n"
    "\tif (atomic_long_sub_and_test(refs, &file->f_count)) {\n"
)
if text.count(broken) != 1:
    raise SystemExit("fs/file_table.c: expected one malformed fput_many body")
text = text.replace(broken, fixed, 1)
if text.count("void fput_many(struct file *file, unsigned int refs)") != 1:
    raise SystemExit("fs/file_table.c: fput_many definition count is not one")
if text.count("void fput(struct file *file)") != 1:
    raise SystemExit("fs/file_table.c: fput wrapper definition count is not one")
if "\tfput_many(file, 1);\n" not in text:
    raise SystemExit("fs/file_table.c: fput wrapper no longer calls fput_many")
file_table.write_text(text)
rows.append("fput_many_signature=restored\n")
rows.append("duplicate_fput=removed\n")

report.write_text("".join(rows))
PY

git -C "$KERNEL_DIR" diff --check
cat "$REPORT"
info "Linux $TARGET_VERSION compile API mismatches repaired"
