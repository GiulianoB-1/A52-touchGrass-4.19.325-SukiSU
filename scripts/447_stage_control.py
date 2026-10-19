#!/usr/bin/env python3
"""P447 staged fail-closed runtime gates for NEW (wave>=2) driver ports.

Does not touch Wave0/1, existing DT bindings, GPU/display/USB/touch, KSU or
firmware.  An OFF gate is not permission to compile arbitrary legacy sources:
port each new driver, and call a52_447_probe_allowed() before suppliers and
hardware I/O.  Never claim a gate controls a driver until wired to its probe.
"""
from pathlib import Path
import argparse

TAG="A52_PHASE447_GATES_V1"
GATES=[
 (2,"sm5714"),(2,"directcharger"),(2,"battery"),(2,"charger"),(2,"fuelgauge"),(2,"muic"),(2,"pdic"),
 (2,"adc"),(3,"aoss"),(3,"glink"),(3,"glinkpkt"),(3,"ssrcompat"),(3,"rmtfscompat"),(3,"adsp"),(3,"cdsp"),(3,"mss"),
 (3,"ipa"),(4,"audio"),(4,"audioapr"),(4,"codec"),(5,"sensors"),(5,"fingerprint"),
 (5,"nfc"),(5,"vibrator"),(6,"wifi"),(6,"bt"),(7,"vidc"),
 (7,"camera"),(8,"devbw"),(8,"suspend"),(8,"thermal"),(8,"misc"),
]
ROOTDIR=Path("drivers/a52_447")
HEADER=Path("include/linux/a52_phase447.h")

def header():
    lines=["/* SPDX-License-Identifier: GPL-2.0-only */",
           "/* "+TAG+": runtime gates only; no driver port implied. */",
           "#ifndef _LINUX_A52_PHASE447_H",
           "#define _LINUX_A52_PHASE447_H",
           "#include <linux/types.h>",
           "#include <linux/device.h>",
           "enum a52_447_subsys {"]
    for _,n in GATES: lines.append("\tA52_447_"+n.upper()+",")
    lines+=["\tA52_447_COUNT", "};",
            "bool a52_447_enabled(enum a52_447_subsys id);",
            "int a52_447_probe_allowed(struct device *dev, enum a52_447_subsys id);",
            "#endif"]
    return "\n".join(lines)+"\n"

def source():
    lines=["// SPDX-License-Identifier: GPL-2.0-only",
           "/* "+TAG+" -- new drivers stay disabled until explicitly selected */",
           "#include <linux/init.h>",
           "#include <linux/kernel.h>",
           "#include <linux/errno.h>",
           "#include <linux/export.h>",
           "#include <linux/a52_phase447.h>",
           "static unsigned int a52_447_wave=1;",
           "static int __init a52_447_parse_wave(char *v)",
           "{",
           "\tunsigned int n;",
           "\tif (!v || kstrtouint(v,0,&n) || n>8) return -EINVAL;",
           "\ta52_447_wave=n;",
           "\treturn 0;",
           "}",
           'early_param("a52.wave",a52_447_parse_wave);',
           "#define GATE(name) \\",
           "\tstatic int a52_447_override_##name = -1; \\",
           "\tstatic int __init a52_447_parse_##name(char *v) \\",
           "\t{ \\",
           "\t\tunsigned int n; \\",
           "\t\tif (!v || kstrtouint(v,0,&n) || n>1) return -EINVAL; \\",
           "\t\ta52_447_override_##name=n; return 0; \\",
           "\t} \\",
           '\tearly_param("a52." #name,a52_447_parse_##name);']
    lines += ["GATE("+n+")" for _,n in GATES]
    lines += ["struct a52_447_gate { const char *name; unsigned int wave; int *override; };",
              "static struct a52_447_gate gates[A52_447_COUNT] = {"]
    for w,n in GATES: lines += ['\t[A52_447_'+n.upper()+'] = {"'+n+'",'+str(w)+',&a52_447_override_'+n+'},']
    lines += ["};",
              "bool a52_447_enabled(enum a52_447_subsys id)",
              "{",
              "\tif (id < 0 || id >= A52_447_COUNT) return false;",
              "\tif (*gates[id].override >= 0) return *gates[id].override != 0;",
              "\treturn a52_447_wave >= gates[id].wave;",
              "}",
              "EXPORT_SYMBOL_GPL(a52_447_enabled);",
              "int a52_447_probe_allowed(struct device *dev, enum a52_447_subsys id)",
              "{",
              "\tif (id < 0 || id >= A52_447_COUNT) return -EINVAL;",
              "\tif (a52_447_enabled(id)) return 0;",
              '\tif (dev) dev_info(dev,"P447 W%u %s DISABLED (a52.%s=0)\\n",',
              "\t\t\tgates[id].wave,gates[id].name,gates[id].name);",
              "\treturn -ENODEV;",
              "}",
              "EXPORT_SYMBOL_GPL(a52_447_probe_allowed);",
              "static int __init a52_447_gates_init(void)",
              "{",
              "\tint i;",
              '\tpr_info("P447 GATES initialized wave=%u new_drivers_only=1\\n",a52_447_wave);',
              "\tfor (i=0;i<A52_447_COUNT;i++)",
              '\t\tpr_info("P447 W%u %s %s override=%d\\n",gates[i].wave,',
              '\t\t\tgates[i].name,a52_447_enabled(i)?"ARMED":"DISABLED",',
              "\t\t\t*gates[i].override);",
              "\treturn 0;",
              "}",
              "subsys_initcall(a52_447_gates_init);"]
    return "\n".join(lines)+"\n"

def apply(root):
    p=root/ROOTDIR
    p.mkdir(parents=True,exist_ok=True)
    (p/"a52_447_gates.c").write_text(source())
    (p/"Makefile").write_text("obj-y += a52_447_gates.o\n")
    h=root/HEADER
    h.parent.mkdir(parents=True,exist_ok=True)
    h.write_text(header())
    mk=root/"drivers/Makefile"
    data=mk.read_text()
    marker="obj-y += a52_447/\n"
    if marker not in data:
        data += "\n# "+TAG+" (compiled, new-device gates OFF by default)\n"+marker
        mk.write_text(data)

def check(root):
    c=(root/ROOTDIR/"a52_447_gates.c").read_text()
    h=(root/HEADER).read_text()
    mk=(root/"drivers/Makefile").read_text()
    assert TAG in c and TAG in h and mk.count("obj-y += a52_447/")==1
    assert c.count('early_param("a52.wave"')==1
    assert c.count("GATE(")==len(GATES)+1
    for wave,name in GATES:
        assert f'A52_447_{name.upper()}' in h and f'GATE({name})' in c
        assert f'{{"{name}",{wave},&a52_447_override_{name}}}' in c
    assert "static unsigned int a52_447_wave=1;" in c
    assert "a52_447_probe_allowed" in c
    print(f"P447 GATES PASS: {len(GATES)} independent new-driver switches; default wave=1 (all W2-W8 OFF)")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:apply(a.root)
    check(a.root)
