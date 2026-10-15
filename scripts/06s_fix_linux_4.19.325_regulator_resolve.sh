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

if old_tail in segment:
    if segment.count(old_tail) != 1:
        raise SystemExit("unexpected truncated regulator tail count")
    segment = segment.replace(old_tail, new_tail, 1)
    text = text[:func_start] + segment + text[func_end:]
    core.write_text(text)
elif segment.count(new_tail) != 1:
    raise SystemExit("regulator_resolve_supply tail is neither truncated nor repaired")

final = core.read_text()
final_start = final.index("static int regulator_resolve_supply(struct regulator_dev *rdev)\n")
final_end = final.index("\n/* Internal regulator request function */", final_start)
final_segment = final[final_start:final_end]
if final_segment.count(new_tail) != 1 or old_tail in final_segment:
    raise SystemExit("regulator_resolve_supply cleanup repair failed")
for required in (
    "\tregulator_unlock(rdev);\n",
    "\tif (rdev->use_count) {\n",
    "\t\tret = regulator_enable(rdev->supply);\n",
    "out:\n\treturn ret;\n",
):
    if required not in final_segment:
        raise SystemExit(f"regulator postcondition failed for {required.strip()!r}")
PY

git -C "$KERNEL_DIR" diff --check -- drivers/regulator/core.c

{
  printf 'kernel_version=%s\n' "$(kernel_version)"
  printf 'regulator=restored-unlock-enable-propagation-and-out-label\n'
  printf 'result=linux-4.19.325-regulator-supply-resolution-repaired\n'
} | tee "$REPORT"

info "Linux $TARGET_VERSION regulator supply resolution compatibility repaired"
