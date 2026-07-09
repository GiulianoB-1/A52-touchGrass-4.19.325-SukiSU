#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

KCONFIG="$KERNEL_DIR/drivers/Kconfig"
MAIN_PATCH="$ARTIFACTS_DIR/sukisu-host-integration.patch"
REPORT="$ARTIFACTS_DIR/sukisu-integration.txt"
SOURCE_LINE='source "drivers/kernelsu/Kconfig"'

test -f "$KCONFIG" || fail "drivers/Kconfig is missing"
test -L "$KERNEL_DIR/drivers/kernelsu" || fail "drivers/kernelsu symlink is missing"

info "Moving SukiSU Kconfig source outside the Samsung Drivers menu"
python3 - "$KCONFIG" "$SOURCE_LINE" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source_line = sys.argv[2]
lines = path.read_text().splitlines()
lines = [line for line in lines if line.strip() != source_line]

last_nonempty = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
if last_nonempty is None or lines[last_nonempty].strip() != "endmenu":
    raise SystemExit("drivers/Kconfig does not end with the expected top-level endmenu")

lines = lines[: last_nonempty + 1] + ["", source_line]
path.write_text("\n".join(lines) + "\n")
PY

source_count=$(grep -Fxc "$SOURCE_LINE" "$KCONFIG" || true)
test "$source_count" -eq 1 || fail "Expected one SukiSU Kconfig source line, found $source_count"

endmenu_line=$(grep -n '^endmenu$' "$KCONFIG" | tail -n 1 | cut -d: -f1)
source_line_no=$(grep -nF "$SOURCE_LINE" "$KCONFIG" | cut -d: -f1)
test -n "$endmenu_line" || fail "Top-level endmenu was not found"
test -n "$source_line_no" || fail "SukiSU source line was not found"
test "$source_line_no" -gt "$endmenu_line" || fail "SukiSU Kconfig is still nested inside the Samsung Drivers menu"

git -C "$KERNEL_DIR" diff --check

git -C "$KERNEL_DIR" diff --binary -- \
  arch/arm64/configs/a52xq_defconfig \
  drivers/Makefile drivers/Kconfig \
  drivers/input/input.c fs/exec.c fs/open.c fs/read_write.c fs/stat.c > "$MAIN_PATCH"
test -s "$MAIN_PATCH" || fail "Corrected host integration patch is empty"
sha256sum "$MAIN_PATCH" > "$MAIN_PATCH.sha256"

if test -f "$REPORT"; then
  sed -i '/^host_patch_sha256=/d' "$REPORT"
  printf 'host_patch_sha256=%s\n' "$(cut -d' ' -f1 "$MAIN_PATCH.sha256")" >> "$REPORT"
  printf 'kconfig_source_location=after-top-level-drivers-endmenu\n' >> "$REPORT"
fi

{
  printf 'drivers_kconfig=%s\n' "$KCONFIG"
  printf 'top_level_endmenu_line=%s\n' "$endmenu_line"
  printf 'sukisu_source_line=%s\n' "$source_line_no"
  printf 'placement=outside-drivers-menu\n'
  printf 'host_patch_sha256=%s\n' "$(cut -d' ' -f1 "$MAIN_PATCH.sha256")"
} | tee "$ARTIFACTS_DIR/sukisu-kconfig-placement.txt"

info "SukiSU Kconfig placement corrected"
