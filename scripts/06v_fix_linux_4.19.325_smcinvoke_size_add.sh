#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/smcinvoke-size-add-compat-$TARGET_VERSION.txt"

 test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
 test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before smcinvoke repair"

python3 - "$KERNEL_DIR/drivers/soc/qcom/smcinvoke.c" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

include = "#include <linux/overflow.h>\n"
if include not in text:
    anchor = "#include <linux/slab.h>\n"
    if text.count(anchor) != 1:
        raise SystemExit(f"smcinvoke overflow include anchor mismatch: {text.count(anchor)}")
    text = text.replace(anchor, anchor + include, 1)

local_helper = (
    "/*\n"
    " * size_add saturates at SIZE_MAX. If integer overflow is detected,\n"
    " * this function would return SIZE_MAX otherwise normal a+b is returned.\n"
    " */\n"
    "static inline size_t size_add(size_t a, size_t b)\n"
    "{\n"
    "\treturn (b > (SIZE_MAX - a)) ? SIZE_MAX : a + b;\n"
    "}\n\n"
)

count = text.count(local_helper)
if count == 1:
    text = text.replace(local_helper, "", 1)
elif count == 0 and "static inline size_t size_add(" not in text:
    pass
else:
    raise SystemExit(
        "smcinvoke local size_add anchor mismatch: "
        f"block={count}, declarations={text.count('static inline size_t size_add(')}"
    )

path.write_text(text)

final = path.read_text()
if final.count(include) != 1:
    raise SystemExit("linux/overflow.h is not included exactly once")
if "static inline size_t size_add(" in final:
    raise SystemExit("duplicate local smcinvoke size_add remains")
if "return size_add(a, pad_size(a, b));" not in final:
    raise SystemExit("smcinvoke size_align no longer uses the saturating helper")
PY

git -C "$KERNEL_DIR" diff --check -- drivers/soc/qcom/smcinvoke.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'local_size_add=removed\n'
  printf 'replacement=linux-overflow-size_add\n'
  printf 'semantics=saturating-at-SIZE_MAX-preserved\n'
  printf 'result=linux-4.19.325-smcinvoke-size-add-collision-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION smcinvoke size_add compatibility repaired"
