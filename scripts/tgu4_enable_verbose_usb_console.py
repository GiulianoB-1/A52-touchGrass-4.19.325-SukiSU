#!/usr/bin/env python3
"""TGU4: verbose TouchGrass ACM kernel console, NO raw FIFO test.

The stock A52 bootloader keeps console=null and ignores our extra_cmdline.
TGU2 made ttyGS0 a real registered kernel console. TGU3 established a clean
no-injected-F0 baseline. TGU4 only changes PRINTK defaults/buffering.

The 1MiB printk history survives longer, but CON_PRINTBUFFER registration
may happen before the USB host opens the port. u_serial's kfifo (64KiB with
safe 8KiB allocation fallback) can still overflow when not connected;
replay of every early message is not guaranteed.
"""
import argparse
from pathlib import Path

MARK = "A52_TGU4_VERBOSE_ACM_KERNEL_CONSOLE_V1"
CONFIG = Path("arch/arm64/configs/a52xq_defconfig")
PRINTK = Path("kernel/printk/printk.c")
SERIAL = Path("drivers/usb/gadget/function/u_serial.c")

def replace_exact(s, before, after, label):
    n = s.count(before)
    if n != 1:
        raise SystemExit(f"TGU4 {label}: expected one exact anchor; found {n}")
    return s.replace(before, after, 1)

def patch_config(s):
    s = replace_exact(s,
        "CONFIG_CONSOLE_LOGLEVEL_DEFAULT=7\n",
        "CONFIG_CONSOLE_LOGLEVEL_DEFAULT=8\n",
        "console threshold")
    s = replace_exact(s,
        "CONFIG_LOG_BUF_SHIFT=17\n",
        "CONFIG_LOG_BUF_SHIFT=20\n",
        "printk ring size")
    return s

def patch_printk(s):
    s = replace_exact(s,
        "static bool __read_mostly ignore_loglevel;\n",
        f"/* {MARK}: retain all priority 0..7 messages regardless of Android's later\n"
        " * writes to /proc/sys/kernel/printk; override remains writable for root.\n"
        " */\n"
        "static bool __read_mostly ignore_loglevel = true;\n",
        "ignore_loglevel default")
    return s

def patch_serial(s):
    s = replace_exact(s,
        "#define GS_CONSOLE_BUF_SIZE\t8192\n",
        "#define GS_CONSOLE_BUF_SIZE\t65536 /* "+MARK+" */\n",
        "GS FIFO capacity")
    s = replace_exact(s,
        """\tstatus = kfifo_alloc(&info->con_buf, GS_CONSOLE_BUF_SIZE, GFP_KERNEL);
\tif (status) {
\t\tpr_err("%s: allocate console buffer failed\\n", __func__);
\t\treturn status;
\t}
""",
        """\tstatus = kfifo_alloc(&info->con_buf, GS_CONSOLE_BUF_SIZE, GFP_KERNEL);
\tif (status) {
\t\t/* TGU4: allow a boot-time 8KiB fallback if 64KiB cannot be
\t\t * allocated as physically contiguous memory.
\t\t */
\t\tpr_warn("A52TGU4 USB console FIFO 64KiB allocation failed (%d), trying 8KiB\\n",
\t\t\tstatus);
\t\tstatus = kfifo_alloc(&info->con_buf, 8192, GFP_KERNEL);
\t\tif (status) {
\t\t\tpr_err("%s: allocate console buffer failed\\n", __func__);
\t\t\treturn status;
\t\t}
\t}
""",
        "64KiB FIFO fallback")
    return s

def validate(root):
    conf = (root / CONFIG).read_text()
    printk = (root / PRINTK).read_text()
    serial = (root / SERIAL).read_text()
    for t in ("CONFIG_CONSOLE_LOGLEVEL_DEFAULT=8",
              "CONFIG_LOG_BUF_SHIFT=20",
              "CONFIG_U_SERIAL_CONSOLE=y"):
        if conf.count(t) != 1:
            raise SystemExit("TGU4 config contract failed: " + t)
    for t in (MARK, "static bool __read_mostly ignore_loglevel = true;",
              "module_param(ignore_loglevel"):
        if t not in printk:
            raise SystemExit("TGU4 printk contract missing: " + t)
    for t in ("#define GS_CONSOLE_BUF_SIZE\t65536", MARK,
              "A52TGU4 USB console FIFO 64KiB allocation failed",
              "kfifo_alloc(&info->con_buf, 8192, GFP_KERNEL)",
              "CON_PRINTBUFFER",
              'add_preferred_console("ttyGS", 0, NULL)',
              "register_console(&gserial_cons);"):
        if t not in serial:
            raise SystemExit("TGU4 serial contract missing: "+t)
    if "P446L2 stage=" in serial or "P446L2 stage=" in printk:
        raise SystemExit("Unexpected active F0 stage marker in TGU4 patch scope")
    print("TGU4 source audit PASS: printk default 8, ignore_loglevel default true")
    print("TGU4 source audit PASS: 1MiB printk history, 64KiB USB kfifo + 8KiB fallback")
    print("TGU4 source audit PASS: TGU2 ttyGS0 console + CON_PRINTBUFFER retained")
    print("TGU4 USB gadget is unchanged; early replay NOT guaranteed.")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--check-only", action="store_true")
    args = p.parse_args()
    root = args.root.resolve()
    if not args.check_only:
        for path, patch in ((CONFIG, patch_config),
                            (PRINTK, patch_printk),
                            (SERIAL, patch_serial)):
            fn = root / path
            if not fn.exists():
                raise SystemExit("TGU4 missing source "+str(fn))
            data = fn.read_text()
            if MARK in data:
                print("TGU4 already patched", str(path))
                continue
            fn.write_text(patch(data))
            print("TGU4 patched", str(path))
    validate(root)

if __name__ == "__main__":
    main()
