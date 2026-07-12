#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.250
REPORT="$ARTIFACTS_DIR/compile-api-fix-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before compile repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

assembler = root / "arch/arm64/include/asm/assembler.h"
text = assembler.read_text()
block = (
    "/*\n"
    " * Clear Branch History instruction\n"
    " */\n"
    "\t.macro clearbhb\n"
    "\thint\t#22\n"
    "\t.endm\n"
)
count = text.count(block)
if count != 2:
    raise SystemExit(f"arm64 clearbhb duplicate: expected two definitions, found {count}")
text = text.replace(block + "\n" + block, block, 1)
assembler.write_text(text)

final = assembler.read_text()
if final.count("\t.macro clearbhb\n") != 1:
    raise SystemExit("arm64 clearbhb postcondition failed")
print("applied=arm64 clearbhb duplicate removal")

verifier = root / "kernel/bpf/verifier.c"
text = verifier.read_text()
start = text.index("static int convert_ctx_accesses(")
end = text.index("\nstatic int jit_subprogs(", start)
segment = text[start:end]

declaration_anchor = "\t\tbool ctx_access;\n\n"
declaration = (
    "\t\tbool ctx_access;\n"
    "\t\tbpf_convert_ctx_access_t convert_ctx_access;\n\n"
)
if segment.count(declaration_anchor) != 1:
    raise SystemExit(
        "BPF context-conversion declaration anchor mismatch: "
        f"found {segment.count(declaration_anchor)}"
    )
segment = segment.replace(declaration_anchor, declaration, 1)

dead_filter = (
    "\t\tif (env->insn_aux_data[i + delta].ptr_type != PTR_TO_CTX)\n"
    "\t\t\tcontinue;\n"
)
if segment.count(dead_filter) != 1:
    raise SystemExit(
        "BPF pointer-type filter mismatch: "
        f"found {segment.count(dead_filter)}"
    )
segment = segment.replace(dead_filter, "", 1)
text = text[:start] + segment + text[end:]
verifier.write_text(text)

final = verifier.read_text()
start = final.index("static int convert_ctx_accesses(")
end = final.index("\nstatic int jit_subprogs(", start)
segment = final[start:end]
if segment.count("bpf_convert_ctx_access_t convert_ctx_access;") != 1:
    raise SystemExit("BPF conversion callback declaration postcondition failed")
if dead_filter in segment:
    raise SystemExit("BPF PTR_TO_CTX-only filter remains after repair")
for required in (
    "case PTR_TO_CTX:",
    "case PTR_TO_SOCKET:",
    "case PTR_TO_SOCK_COMMON:",
    "case PTR_TO_TCP_SOCK:",
    "cnt = convert_ctx_access(type, insn, insn_buf, env->prog,",
):
    if required not in segment:
        raise SystemExit(f"BPF conversion dispatch postcondition missing: {required}")
print("applied=BPF context-conversion callback and pointer dispatch")
PY

{
  echo 'target=4.19.250'
  echo 'arm64_clearbhb_definitions=1'
  echo 'bpf_convert_ctx_access_callback=restored'
  echo 'bpf_socket_pointer_dispatch=reachable'
  echo 'result=compile-api-compatible'
} | tee "$REPORT"

info "Linux $TARGET_VERSION compile mismatches repaired"
