#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SUKISU_DIR="$KERNEL_DIR/KernelSU"
SEPOLICY_C="$SUKISU_DIR/kernel/selinux/sepolicy.c"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy-placement.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy-placement.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before legacy SELinux placement normalization"
test -f "$SEPOLICY_C" || fail "SukiSU sepolicy.c is missing"
test -f "$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy.txt" || fail "Legacy SELinux compatibility stage did not run"

before=$(mktemp)
trap 'rm -f "$before"' EXIT
cp "$SEPOLICY_C" "$before"

python3 - "$SEPOLICY_C" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text()


def find_matching_brace(source: str, opening: int) -> int:
    depth = 0
    state = "normal"
    i = opening
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""

        if state == "normal":
            if ch == '"':
                state = "double"
            elif ch == "'":
                state = "single"
            elif ch == '/' and nxt == '/':
                state = "line_comment"
                i += 1
            elif ch == '/' and nxt == '*':
                state = "block_comment"
                i += 1
            elif ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return i + 1
        elif state == "double":
            if ch == '\\':
                i += 1
            elif ch == '"':
                state = "normal"
        elif state == "single":
            if ch == '\\':
                i += 1
            elif ch == "'":
                state = "normal"
        elif state == "line_comment":
            if ch == '\n':
                state = "normal"
        elif state == "block_comment":
            if ch == '*' and nxt == '/':
                state = "normal"
                i += 1
        i += 1
    raise SystemExit("unterminated function body")


def function_blocks(source: str, name: str):
    pattern = re.compile(rf"(?m)^static\s+(?:bool|void)\s+{re.escape(name)}\s*\(")
    blocks = []
    for match in pattern.finditer(source):
        semi = source.find(';', match.end())
        opening = source.find('{', match.end())
        if opening < 0:
            continue
        if semi >= 0 and semi < opening:
            continue
        end = find_matching_brace(source, opening)
        while end < len(source) and source[end] in ' \t':
            end += 1
        if end < len(source) and source[end] == '\n':
            end += 1
        blocks.append((match.start(), end, opening))
    return blocks


def normalize(source: str, name: str) -> str:
    blocks = function_blocks(source, name)
    if len(blocks) != 2:
        raise SystemExit(f"{name}: expected two generated definitions, found {len(blocks)}")

    first_start, first_end, first_open = blocks[0]
    second_start, second_end, _ = blocks[1]
    legacy_body = source[first_start:first_end]
    header = source[first_start:first_open].rstrip()
    declaration = header + ";\n\n"

    # Replace from the end backwards so earlier offsets remain valid.
    source = source[:second_start] + legacy_body + source[second_end:]
    source = source[:first_start] + declaration + source[first_end:]
    return source


for function_name in (
    "remove_avtab_node",
    "add_filename_trans",
    "add_type",
    "add_typeattribute_raw",
):
    text = normalize(text, function_name)

path.write_text(text)
PY

for fn in remove_avtab_node add_filename_trans add_type add_typeattribute_raw; do
  test "$(grep -Ec "^static (bool|void) ${fn}\\(" "$SEPOLICY_C")" -eq 2 || \
    fail "Expected one declaration and one definition for $fn"
done

grep -Fq '#define strip_av' "$SEPOLICY_C" || fail "SELinux helper macro section is missing"
add_type_definition=$(grep -n '^static bool add_type(struct policydb' "$SEPOLICY_C" | tail -n 1 | cut -d: -f1)
macro_line=$(grep -n '^#define strip_av' "$SEPOLICY_C" | cut -d: -f1)
test -n "$add_type_definition" || fail "Normalized add_type definition is missing"
test "$add_type_definition" -gt "$macro_line" || fail "Legacy function definitions still precede required compatibility macros"

git -C "$SUKISU_DIR" diff --check
set +e
diff -u --label a/kernel/selinux/sepolicy.c --label b/kernel/selinux/sepolicy.c \
  "$before" "$SEPOLICY_C" > "$PATCH_OUT"
diff_rc=$?
set -e
test "$diff_rc" -eq 1 || fail "Could not produce legacy SELinux placement patch"
test -s "$PATCH_OUT" || fail "Legacy SELinux placement patch is empty"
sha256sum "$PATCH_OUT" > "$PATCH_OUT.sha256"

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'sukisu_commit=%s\n' "$(git -C "$SUKISU_DIR" rev-parse HEAD)"
  printf 'normalized_functions=4\n'
  printf 'forward_declarations=restored\n'
  printf 'legacy_implementations=placed-at-original-body-locations\n'
  printf 'definitions_after_compatibility_macros=yes\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "Legacy SELinux compatibility function placement normalized"
