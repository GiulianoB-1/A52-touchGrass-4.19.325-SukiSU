#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: 58_patch_recovery_keycombo.py GENERATED_BUILD_SCRIPT")

    path = Path(sys.argv[1])
    text = path.read_text()

    config_anchor = '"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" \\\n'
    install_block = r"""
info "Adding boot-time physical recovery key combination"
install -m 0644 "$(dirname "$0")/58_recovery_keycombo.c" \
  "$KERNEL_DIR/drivers/input/recovery_keycombo.c"

python3 - "$KERNEL_DIR/drivers/input/Kconfig" "$KERNEL_DIR/drivers/input/Makefile" <<'RECOVERYKEYPY'
from pathlib import Path
import sys

kconfig = Path(sys.argv[1])
makefile = Path(sys.argv[2])

config_anchor = '''config INPUT_KEYCOMBO
	bool "Key combo"
	depends on INPUT
	---help---
	  Say Y here if you want to take action when some keys are pressed;
'''
config_block = '''
config INPUT_RECOVERY_KEYCOMBO
	bool "A52 boot-time Volume Up + Power recovery combination"
	depends on INPUT
	help
	  Reboot into Android recovery when the physical Volume Up and Power
	  buttons are held together during the early kernel boot window.

	  This is specific to the Galaxy A52 input device names gpio_keys and
	  qpnp_pon and is intended to be built into the kernel.
'''
text = kconfig.read_text()
if 'config INPUT_RECOVERY_KEYCOMBO\n' not in text:
    if text.count(config_anchor) != 1:
        raise SystemExit('drivers/input/Kconfig keycombo anchor mismatch')
    kconfig.write_text(text.replace(config_anchor, config_anchor + config_block, 1))

make_anchor = 'obj-$(CONFIG_INPUT_KEYCOMBO)\t+= keycombo.o\n'
make_line = 'obj-$(CONFIG_INPUT_RECOVERY_KEYCOMBO) += recovery_keycombo.o\n'
text = makefile.read_text()
if make_line not in text:
    if text.count(make_anchor) != 1:
        raise SystemExit('drivers/input/Makefile keycombo anchor mismatch')
    makefile.write_text(text.replace(make_anchor, make_anchor + make_line, 1))
RECOVERYKEYPY

"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" -e INPUT_RECOVERY_KEYCOMBO
git -C "$KERNEL_DIR" add -N drivers/input/recovery_keycombo.c
require_line "$DEFCONFIG" 'CONFIG_INPUT_RECOVERY_KEYCOMBO=y'
grep -Fq 'config INPUT_RECOVERY_KEYCOMBO' "$KERNEL_DIR/drivers/input/Kconfig" || \
  fail "Recovery keycombo Kconfig entry is missing"
grep -Fq 'obj-$(CONFIG_INPUT_RECOVERY_KEYCOMBO) += recovery_keycombo.o' \
  "$KERNEL_DIR/drivers/input/Makefile" || fail "Recovery keycombo Makefile entry is missing"
grep -Fq 'androidboot.boot_recovery=1' "$KERNEL_DIR/drivers/input/recovery_keycombo.c" || \
  fail "Recovery-mode loop guard is missing"
grep -Fq 'strcmp(input_device->name, "qpnp_pon")' \
  "$KERNEL_DIR/drivers/input/recovery_keycombo.c" || fail "Physical power-key filter is missing"
grep -Fq 'strcmp(input_device->name, "gpio_keys")' \
  "$KERNEL_DIR/drivers/input/recovery_keycombo.c" || fail "Physical volume-key filter is missing"

"""
    text = replace_once(
        text,
        config_anchor,
        install_block + config_anchor,
        "kernel config command anchor",
    )

    text = replace_once(
        text,
        "resukisu-v4.1.0-susfs-v1.4.2-manual-core",
        "resukisu-v4.1.0-susfs-v1.4.2-manual-core-recovery-keycombo",
        "build label",
    )

    image_anchor = (
        'grep -Fxq "$RESUKISU_VERSION_FULL" "$ARTIFACTS_DIR/Image-$LABEL.strings.txt" '
        '|| fail "Expected ReSukiSU version string is missing"\n'
    )
    image_check = (
        'grep -Fq \'recovery-keycombo\' "$ARTIFACTS_DIR/Image-$LABEL.strings.txt" || '
        'fail "Recovery keycombo marker is missing from the compiled Image"\n'
    )
    text = replace_once(
        text,
        image_anchor,
        image_anchor + image_check,
        "compiled Image marker anchor",
    )

    config_assert_anchor = 'require_line "$FINAL_CONFIG" \'CONFIG_KSU_SUSFS=y\'\n'
    text = replace_once(
        text,
        config_assert_anchor,
        config_assert_anchor
        + 'require_line "$FINAL_CONFIG" \'CONFIG_INPUT_RECOVERY_KEYCOMBO=y\'\n',
        "final recovery config assertion",
    )

    report_anchor = "  printf 'susfs_profile=core-only-all-features-off\\n'\n"
    report_extension = (
        "  printf 'recovery_keycombo=enabled\\n'\n"
        "  printf 'recovery_keycombo_hold_ms=800\\n'\n"
        "  printf 'recovery_keycombo_boot_window_ms=30000\\n'\n"
        "  printf 'recovery_keycombo_devices=gpio_keys+qpnp_pon\\n'\n"
    )
    text = replace_once(
        text,
        report_anchor,
        report_anchor + report_extension,
        "build report recovery anchor",
    )

    old_diff = (
        'git -C "$KERNEL_DIR" diff --binary -- arch/arm64/configs/a52xq_defconfig '
        'drivers/Makefile drivers/Kconfig drivers/input/input.c fs/read_write.c '
        'fs/stat.c kernel/reboot.c > "$HOST_PATCH"'
    )
    new_diff = (
        'git -C "$KERNEL_DIR" diff --binary -- arch/arm64/configs/a52xq_defconfig '
        'drivers/Makefile drivers/Kconfig drivers/input/Makefile drivers/input/Kconfig '
        'drivers/input/input.c drivers/input/recovery_keycombo.c fs/read_write.c '
        'fs/stat.c kernel/reboot.c > "$HOST_PATCH"'
    )
    text = replace_once(text, old_diff, new_diff, "host diagnostic diff list")

    path.write_text(text)


if __name__ == "__main__":
    main()
