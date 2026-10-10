#!/usr/bin/env python3
"""TGU1: opt-in USB ACM kernel console for pinned TouchGrass 4.19 Golden.

This changes only the Golden kernel configuration. It does NOT enable early
g_serial or modify Android's ConfigFS gadget, bootloader, or display code.
The packaged boot image must also request console=ttyGS0, and Android must
instantiate acm.gs0 alongside ffs.adb before both are available on USB.
"""
import argparse
from pathlib import Path

MARK = "A52_TGU1_GOLDEN_ACM_CONSOLE_OPT_IN_V1"
DISABLED = "# CONFIG_U_SERIAL_CONSOLE is not set"
ENABLED = "CONFIG_U_SERIAL_CONSOLE=y"

def validate(root: Path):
    cfg = (root / "arch/arm64/configs/a52xq_defconfig").read_text()
    if cfg.count(ENABLED) != 1:
        raise RuntimeError("TGU1: console option not enabled exactly once")
    for token in ("CONFIG_USB_GADGET=y", "CONFIG_USB_CONFIGFS=y",
                  "CONFIG_USB_CONFIGFS_ACM=y", "CONFIG_USB_CONFIGFS_F_FS=y",
                  "CONFIG_USB_F_ACM=y"):
        if token not in cfg.splitlines():
            raise RuntimeError("TGU1: missing baseline " + token)
    if "CONFIG_USB_G_SERIAL=y" in cfg:
        raise RuntimeError("TGU1: do NOT enable competing early g_serial")
    us = (root / "drivers/usb/gadget/function/u_serial.c").read_text()
    acm = (root / "drivers/usb/gadget/function/f_acm.c").read_text()
    for token in ("#ifdef CONFIG_U_SERIAL_CONSOLE",
                  "register_console(&gserial_cons)",
                  "gserial_console_init();",
                  "gserial_console_exit();"):
        if token not in us:
            raise RuntimeError("TGU1: missing TouchGrass console hook " + token)
    if "gserial_alloc_line(&opts->port_num)" not in acm:
        raise RuntimeError("TGU1: ConfigFS ACM doesn't allocate a ttyGS line")
    print("TGU1 config PASS: ACM/FunctionFS intact; console enabled; no competing g_serial")
    print("TGU1 runtime NOT PROVEN: verify Android adb+acm composition and ttyGS0 console")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    p = args.root / "arch/arm64/configs/a52xq_defconfig"
    txt = p.read_text()
    if not args.check_only and ENABLED not in txt:
        if txt.count(DISABLED) != 1:
            raise RuntimeError("TGU1: unknown TouchGrass config baseline")
        txt = txt.replace(DISABLED, "# " + MARK + "\n" + ENABLED, 1)
        p.write_text(txt)
    validate(args.root)

if __name__ == "__main__":
    main()
