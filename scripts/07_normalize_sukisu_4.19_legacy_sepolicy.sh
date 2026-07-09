#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.153
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUKISU_DIR="$KERNEL_DIR/KernelSU"
SEPOLICY_C="$SUKISU_DIR/kernel/selinux/sepolicy.c"
LEGACY_SCRIPT="$SCRIPT_DIR/07_patch_sukisu_4.19_legacy_sepolicy.sh"
LEGACY_REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy.txt"
PATCH_OUT="$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy-placement.patch"
REPORT="$ARTIFACTS_DIR/sukisu-linux-4.19-legacy-sepolicy-placement.txt"

test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before legacy SELinux placement normalization"
test -f "$SEPOLICY_C" || fail "SukiSU sepolicy.c is missing"
test -f "$LEGACY_SCRIPT" || fail "Legacy SELinux conversion script is missing"

before=$(mktemp)
temporary_legacy="$SCRIPT_DIR/.legacy-sepolicy-exec.$$.sh"
trap 'rm -f "$before" "$temporary_legacy"' EXIT

# The original conversion script intentionally verifies that no newer-layout
# implementation remains. Three replacement ranges in its first revision begin
# at forward declarations, so those two checks must run only after placement is
# normalized. remove_avtab_node is replaced at its real function body and must
# remain a single definition. Create a temporary execution copy with only those
# premature checks deferred; every other fail-closed check remains active.
python3 - "$LEGACY_SCRIPT" "$temporary_legacy" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text()
output = Path(sys.argv[2])

deferred = {
    '! grep -Fq \'struct filename_trans_key\' "$SEPOLICY_C" || fail "New filename transition layout remains"\n',
    '! grep -Fq \'db->type_val_to_struct,\' "$SEPOLICY_C" || fail "New type-value pointer-array path remains"\n',
}

for line in deferred:
    if source.count(line) != 1:
        raise SystemExit(f"expected exactly one deferred verification line: {line.strip()}")
    source = source.replace(line, '', 1)

output.write_text(source)
PY

bash "$temporary_legacy"
test -f "$LEGACY_REPORT" || fail "Legacy SELinux conversion did not produce its report"
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
    "add_filename_trans",
    "add_type",
    "add_typeattribute_raw",
):
    text = normalize(text, function_name)

path.write_text(text)
PY

# remove_avtab_node was replaced at its real body and therefore has one
# definition and no forward declaration to normalize.
test "$(grep -Ec '^static bool remove_avtab_node\(' "$SEPOLICY_C")" -eq 1 || \
  fail "Expected exactly one legacy remove_avtab_node definition"
grep -Fq "Android's legacy flex-array avtab has no safe public unlink primitive" "$SEPOLICY_C" || \
  fail "Legacy remove_avtab_node implementation is missing"

for fn in add_filename_trans add_type add_typeattribute_raw; do
  test "$(grep -Ec "^static (bool|void) ${fn}\\(" "$SEPOLICY_C")" -eq 2 || \
    fail "Expected one declaration and one definition for $fn"
done

grep -Fq '#define strip_av' "$SEPOLICY_C" || fail "SELinux helper macro section is missing"
add_type_definition=$(grep -n '^static bool add_type(struct policydb' "$SEPOLICY_C" | tail -n 1 | cut -d: -f1)
macro_line=$(grep -n '^#define strip_av' "$SEPOLICY_C" | cut -d: -f1)
test -n "$add_type_definition" || fail "Normalized add_type definition is missing"
test "$add_type_definition" -gt "$macro_line" || fail "Legacy function definitions still precede required compatibility macros"

# Run the formerly deferred checks now, using the normalized source.
! grep -Fq 'struct filename_trans_key' "$SEPOLICY_C" || fail "New filename transition layout remains after normalization"
! grep -Fq 'db->type_val_to_struct,' "$SEPOLICY_C" || fail "New type-value pointer-array path remains after normalization"
grep -Fq 'struct filename_trans key;' "$SEPOLICY_C" || fail "Legacy filename transition implementation is missing"
grep -Fq 'type_val_to_struct_array' "$SEPOLICY_C" || fail "Legacy type-value flex-array implementation is missing"
grep -Fq 'flex_array_get(db->type_attr_map_array' "$SEPOLICY_C" || fail "Legacy type-attribute flex-array access is missing"
! grep -Fq 'ksu_dup_sepolicy' "$SEPOLICY_C" || fail "Unsupported policy duplication implementation remains"
! grep -Fq 'ksu_destroy_sepolicy' "$SEPOLICY_C" || fail "Unsupported policy destruction implementation remains"

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
  printf 'legacy_conversion=completed-before-normalization\n'
  printf 'normalized_functions=3\n'
  printf 'single_body_replacement=remove_avtab_node\n'
  printf 'forward_declarations=restored\n'
  printf 'legacy_implementations=placed-at-original-body-locations\n'
  printf 'definitions_after_compatibility_macros=yes\n'
  printf 'new_filename_transition_layout_remaining=no\n'
  printf 'new_type_value_pointer_array_remaining=no\n'
  printf 'patch_sha256=%s\n' "$(cut -d' ' -f1 "$PATCH_OUT.sha256")"
} | tee "$REPORT"

info "Legacy SELinux conversion and function placement completed atomically"
