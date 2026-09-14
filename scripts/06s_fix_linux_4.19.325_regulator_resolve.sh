#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/regulator-resolve-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before regulator repair"

python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
core = root / "drivers/regulator/core.c"
text = core.read_text()

func_start = text.index("static int regulator_resolve_supply(struct regulator_dev *rdev)\n")
func_end = text.index("\n/* Internal regulator request function */", func_start)
segment = text[func_start:func_end]

old_tail = (
    "\tret = set_supply(rdev, r);\n"
    "\tif (ret < 0) {\n"
    "\t\tregulator_unlock(rdev);\n"
    "\t\tput_device(&r->dev);\n"
    "\t\tgoto out;\n"
    "\t}\n"
    "\n"
    "\treturn 0;\n"
    "}\n"
)
new_tail = (
    "\tret = set_supply(rdev, r);\n"
    "\tif (ret < 0) {\n"
    "\t\tregulator_unlock(rdev);\n"
    "\t\tput_device(&r->dev);\n"
    "\t\tgoto out;\n"
    "\t}\n"
    "\n"
    "\tregulator_unlock(rdev);\n"
    "\n"
    "\t/*\n"
    "\t * In set_machine_constraints() we may have turned this regulator on\n"
    "\t * but we couldn't propagate to the supply if it hadn't been resolved\n"
    "\t * yet.  Do it now.\n"
    "\t */\n"
    "\tif (rdev->use_count) {\n"
    "\t\tret = regulator_enable(rdev->supply);\n"
    "\t\tif (ret < 0) {\n"
    "\t\t\t_regulator_put(rdev->supply);\n"
    "\t\t\trdev->supply = NULL;\n"
    "\t\t\tgoto out;\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
    "out:\n"
    "\treturn ret;\n"
    "}\n"
)

# This function already has the complete stable semantics in Linux 4.19.206,
# and the 4.19.325 version is identical. Older uplift attempts starting from
# 4.19.154 could leave a truncated merge tail, so keep that repair as a
# fallback, but accept any correctly merged Samsung/stable formatting.
required_semantics = (
    "regulator_lock(rdev);",
    "if (rdev->supply) {",
    "ret = set_supply(rdev, r);",
    "regulator_unlock(rdev);",
    "if (rdev->use_count) {",
    "ret = regulator_enable(rdev->supply);",
    "_regulator_put(rdev->supply);",
    "rdev->supply = NULL;",
    "out:",
    "return ret;",
)

if all(item in segment for item in required_semantics):
    # Already complete: do not rewrite a clean 4.19.206 -> 4.19.325 merge.
    pass
elif old_tail in segment:
    if segment.count(old_tail) != 1:
        raise SystemExit("unexpected truncated regulator tail count")
    segment = segment.replace(old_tail, new_tail, 1)
    text = text[:func_start] + segment + text[func_end:]
    core.write_text(text)
else:
    missing = [item for item in required_semantics if item not in segment]
    raise SystemExit(
        "regulator_resolve_supply has an unrecognized incomplete merge; "
        f"missing semantics: {missing}"
    )

final = core.read_text()
final_start = final.index("static int regulator_resolve_supply(struct regulator_dev *rdev)\n")
final_end = final.index("\n/* Internal regulator request function */", final_start)
final_segment = final[final_start:final_end]
for required in required_semantics:
    if required not in final_segment:
        raise SystemExit(f"regulator postcondition failed for {required!r}")

# Safety checks for the locking/cleanup ordering that matters here.
if final_segment.index("regulator_lock(rdev);") > final_segment.index("ret = set_supply(rdev, r);"):
    raise SystemExit("regulator lock occurs after set_supply")
if final_segment.rindex("regulator_unlock(rdev);") > final_segment.index("if (rdev->use_count) {"):
    raise SystemExit("regulator remains locked across supply enable propagation")
if final_segment.index("out:") > final_segment.rindex("return ret;"):
    raise SystemExit("regulator out label does not lead to return ret")
PY

git -C "$KERNEL_DIR" diff --check -- drivers/regulator/core.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'regulator=restored-unlock-enable-propagation-and-out-label\n'
  printf 'result=linux-4.19.325-regulator-supply-resolution-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION regulator supply resolution compatibility repaired"
