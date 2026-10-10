#!/usr/bin/env python3
"""U2RAM16B: detach early ttyGS0 printk console BEFORE unbinding g_serial.

Apply AFTER U2 + U2RAM16, before compile. This is a deliberately narrow,
experimental *functional A/B* of the previous reproducible gs_unbind hang.
The 16-copy P445 recorder retains progress when COM3 disappears.
No console synchronous-writing hack, no forced panic, no new RAM region.
No kthread_stop timeout is claimed: step 53 without step 54 identifies one.
"""
import argparse
from pathlib import Path

MARK="A52_U2RAM16B_PREDETACH_TTYGS0_V1"
SERIAL=Path("drivers/usb/gadget/legacy/serial.c")
US=Path("drivers/usb/gadget/function/u_serial.c")
CONFIG=Path("drivers/usb/gadget/configfs.c")

def one(s,a,b,why):
    n=s.count(a)
    if n!=1:
        raise RuntimeError(f"U2RAM16B {why} requires 1 anchor; got {n}")
    return s.replace(a,b,1)

def patch_serial(s):
    if MARK in s: return s
    s=one(s,
        "    a52_u2_ram_checkpoint(12, 0); /* before release call */\n"
        "    ret = switch_gserial_enable(false);\n",
        "    /* "+MARK+": Console->USB must detach while the UDC is still bound.\n"
        "     * All writes after this point go only to the P445 RAM recorder.\n"
        "     */\n"
        "    a52_u2_ram_checkpoint(12, 0);\n"
        "#if IS_ENABLED(CONFIG_U_SERIAL_CONSOLE)\n"
        "    a52_u2_ram_checkpoint(30, 0); /* starting ttyGS0 console detach */\n"
        "    ret = (int)gserial_set_console(0, \"0\", 1);\n"
        "    a52_u2_ram_checkpoint(31, ret); /* console detach returned */\n"
        "    if (ret < 0)\n"
        "        return ret; /* never release USB while console still live */\n"
        "#endif\n"
        "    a52_u2_ram_checkpoint(38, 0); /* after detach, before composite unregister */\n"
        "    ret = switch_gserial_enable(false);\n",
        "pre-detach call")
    s=one(s,
        "\tfor (i = 0; i < n_ports; i++) {\n"
        "\t\tusb_put_function(f_serial[i]);\n"
        "\t\tusb_put_function_instance(fi_serial[i]);\n"
        "\t}\n",
        "\tfor (i = 0; i < n_ports; i++) {\n"
        "\t\ta52_u2_ram_checkpoint(40 + i * 4, 0); /* put function entry */\n"
        "\t\tusb_put_function(f_serial[i]);\n"
        "\t\ta52_u2_ram_checkpoint(41 + i * 4, 0); /* function returned */\n"
        "\t\tusb_put_function_instance(fi_serial[i]);\n"
        "\t\ta52_u2_ram_checkpoint(42 + i * 4, 0); /* instance freed */\n"
        "\t}\n",
        "gs_unbind port phases")
    return s

def patch_us(s):
    if MARK in s: return s
    s=one(s,
        "static void gs_console_exit(struct gs_port *port)\n",
        "/* "+MARK+": persistent markers only, safe if dying USB console prints nothing. */\n"
        "extern void a52_u2_ram_checkpoint(u32 step, s32 result);\n"
        "static void gs_console_exit(struct gs_port *port)\n",
        "recorder symbol")
    s=one(s,
        "\tif (!cons)\n"
        "\t\treturn;\n\n"
        "\tunregister_console(&cons->console);\n",
        "\tif (!cons)\n"
        "\t\treturn;\n\n"
        "\ta52_u2_ram_checkpoint(50, 0); /* console exit entered */\n"
        "\tunregister_console(&cons->console);\n"
        "\ta52_u2_ram_checkpoint(51, 0); /* console unregistered */\n",
        "console unregister")
    s=one(s,
        "\tspin_lock_irq(&port->port_lock);\n"
        "\tif (cons->req)\n"
        "\t\tgs_console_disconnect(port);\n"
        "\tspin_unlock_irq(&port->port_lock);\n\n"
        "\tif (cons->task)\n"
        "\t\tkthread_stop(cons->task);\n"
        "\tkfifo_free(&cons->buf);\n",
        "\ta52_u2_ram_checkpoint(52, 0); /* before USB request disconnect */\n"
        "\tspin_lock_irq(&port->port_lock);\n"
        "\tif (cons->req)\n"
        "\t\tgs_console_disconnect(port);\n"
        "\tspin_unlock_irq(&port->port_lock);\n"
        "\ta52_u2_ram_checkpoint(53, 0); /* before kthread_stop */\n"
        "\tif (cons->task)\n"
        "\t\tkthread_stop(cons->task);\n"
        "\ta52_u2_ram_checkpoint(54, 0); /* kthread stopped */\n"
        "\tkfifo_free(&cons->buf);\n",
        "console endpoint drain and thread")
    return s

def validate(root):
    s=(root/SERIAL).read_text()
    u=(root/US).read_text()
    c=(root/CONFIG).read_text()
    for v in (MARK,'gserial_set_console(0, "0", 1)',
              'a52_u2_ram_checkpoint(30, 0)',
              'a52_u2_ram_checkpoint(31, ret)',
              'a52_u2_ram_checkpoint(38, 0)',
              'a52_u2_ram_checkpoint(40 + i * 4, 0)',
              'a52_u2_ram_checkpoint(42 + i * 4, 0)',
              'a52_u2_ram_checkpoint(15, 0)'):
        if v not in s: raise RuntimeError("U2RAM16B missing serial "+v)
    for v in (MARK,'a52_u2_ram_checkpoint(50, 0)',
              'a52_u2_ram_checkpoint(51, 0)',
              'a52_u2_ram_checkpoint(52, 0)',
              'a52_u2_ram_checkpoint(53, 0)',
              'a52_u2_ram_checkpoint(54, 0)',
              'kthread_stop(cons->task)'):
        if v not in u: raise RuntimeError("U2RAM16B missing u_serial "+v)
    if 'a52_u2_ram_checkpoint(22, 0)' not in c:
        raise RuntimeError("U2RAM16B lost ConfigFS bind checkpoint")
    print("U2RAM16B source audit PASS: early console detach before unregister")
    print("U2RAM16B source audit PASS: P445 steps 30/31, 50-54, 38, 40-42 retained")
    print("U2RAM16B NOTE: this is an A/B test. Detach can still block, and no timeout is implemented.")

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--check-only',action='store_true')
    a=p.parse_args()
    if not a.check_only:
        for rel,fn in ((SERIAL,patch_serial),(US,patch_us)):
            path=a.root/rel
            orig=path.read_text()
            if MARK in orig: continue
            path.write_text(fn(orig))
    validate(a.root)
if __name__=='__main__': main()
