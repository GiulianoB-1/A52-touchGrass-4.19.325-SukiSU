#!/usr/bin/env python3
"""TGU2: register the TouchGrass ACM console without relying on boot extra_cmdline.

Observed on physically tested TGU1: effective /proc/cmdline has console=null,
and /proc/consoles has only pstore-1 despite CONFIG_U_SERIAL_CONSOLE=y.
Samsung's bootloader did not relay extra_cmdline console=ttyGS0.
Select ttyGS0 in gserial_console_init() before register_console().
This changes neither Android's existing USB composition nor DSI logic.
"""
from pathlib import Path
import argparse

MARK="A52_TGU2_GOLDEN_RUNTIME_ACM_CONSOLE_V1"
REL=Path("drivers/usb/gadget/function/u_serial.c")
OLD="""static void gserial_console_init(void)
{
	register_console(&gserial_cons);
}
"""
NEW="""/* A52_TGU2_GOLDEN_RUNTIME_ACM_CONSOLE_V1
 * The A52 bootloader drops boot v2 extra_cmdline. The effective boot
 * cmdline still says console=null. Pick the already-allocated ACM ttyGS0
 * at gadget function creation instead, leaving the rest of the boot
 * command line and Android gadget untouched.
 */
static bool a52_tgu2_console_registered;
static void gserial_console_init(void)
{
	int ret;

	if (a52_tgu2_console_registered)
		return;
	ret = add_preferred_console("ttyGS", 0, NULL);
	if (ret) {
		pr_err("A52TGU2 cannot select ttyGS0 console: %d\\n", ret);
		return;
	}
	register_console(&gserial_cons);
	a52_tgu2_console_registered =
		!!(gserial_cons.flags & CON_ENABLED);
	pr_warn("A52TGU2 ttyGS%d console enabled=%u (boot console=null)\\n",
		gserial_cons.index, a52_tgu2_console_registered);
}
"""
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    path=a.root/REL
    src=path.read_text()
    if not a.check_only and MARK not in src:
        n=src.count(OLD)
        if n!=1:
            raise SystemExit("TGU2: expected one exact console init body; got %d"%n)
        src=src.replace(OLD,NEW,1)
        path.write_text(src)
    src=path.read_text()
    for token in (MARK,'add_preferred_console("ttyGS", 0, NULL)',
                  "register_console(&gserial_cons);",
                  "A52TGU2 ttyGS%d console enabled=%u",
                  "CON_PRINTBUFFER",
                  "gs_console_connect(port_num)"):
        if token not in src:
            raise SystemExit("TGU2 missing source contract: "+token)
    if src.count(MARK)!=1:
        raise SystemExit("TGU2 duplicate source modification")
    print("TGU2 source audit PASS: add_preferred_console before register_console; no Android USB/DSI changes")
    print("TGU2 hardware USB serial enumeration is UNTESTED")

if __name__=="__main__":
    main()
