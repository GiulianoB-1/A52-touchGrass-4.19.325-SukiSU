#!/usr/bin/env python3
"""P447C: per-variant compiled Kconfig gates; cmdline is optional override only.

Preserve existing W0/W1, the 446AG touchscreen, KSU, USB and display.
This creates *only* an independently compiled switch core; NO battery,
modem, or other absent drivers become functional by changing config.
"""
import argparse
from pathlib import Path

TAG="A52_PHASE447_GATES_V1"
GATES=[
 (2,"sm5714"),(2,"directcharger"),(2,"battery"),(2,"charger"),
 (2,"fuelgauge"),(2,"muic"),(2,"pdic"),(2,"adc"),
 (3,"aoss"),(3,"glink"),(3,"glinkpkt"),(3,"ssrcompat"),(3,"rmtfscompat"),
 (3,"adsp"),(3,"cdsp"),(3,"mss"),(3,"ipa"),
 (4,"audio"),(4,"audioapr"),(4,"codec"),
 (5,"sensors"),(5,"fingerprint"),(5,"nfc"),(5,"vibrator"),
 (6,"wifi"),(6,"bt"),(7,"vidc"),(7,"camera"),
 (8,"devbw"),(8,"suspend"),(8,"thermal"),(8,"misc"),
]
DIR=Path("drivers/a52_447")
HEADER=Path("include/linux/a52_phase447.h")

def header():
    lines=["/* SPDX-License-Identifier: GPL-2.0-only */",
           "/* "+TAG+"; no port implied. */",
           "#ifndef _LINUX_A52_PHASE447_H","#define _LINUX_A52_PHASE447_H",
           "#include <linux/types.h>","#include <linux/device.h>",
           "enum a52_447_subsys {"]
    lines+=["\tA52_447_"+n.upper()+"," for _,n in GATES]
    lines+=["\tA52_447_COUNT","};",
            "bool a52_447_enabled(enum a52_447_subsys id);",
            "int a52_447_probe_allowed(struct device *dev, enum a52_447_subsys id);",
            "#endif"]
    return "\n".join(lines)+"\n"

def kconfig():
    return """# Phase447C built-in defaults: each matrix variant compiles its own Image.
config A52_WAVE_DEFAULT
    int "A52 staged wave compiled fallback"
    range 1 8
    default 1
    help
      Do not rely on Android bootloader command line for wave selection.
config A52_MSS_DEFAULT
    bool "A52 allow MSS when wave reaches 3"
    default n
config A52_IPA_DEFAULT
    bool "A52 allow IPA when wave reaches 3"
    default n
"""

def source():
    lines=[
"// SPDX-License-Identifier: GPL-2.0-only",
"/* "+TAG+" -- Phase447C defaults compiled from .config */",
"#include <linux/init.h>","#include <linux/kernel.h>",
"#include <linux/errno.h>","#include <linux/export.h>",
"#include <linux/kconfig.h>","#include <linux/a52_phase447.h>",
"#if !defined(CONFIG_A52_WAVE_DEFAULT)",
'#error "P447C requires compiled CONFIG_A52_WAVE_DEFAULT"',
"#endif",
"static unsigned int a52_447_wave=CONFIG_A52_WAVE_DEFAULT;",
"static bool a52_447_source_cmdline;",
"static int __init a52_447_parse_wave(char *v)",
"{","\tunsigned int n;",
"\tif (!v || kstrtouint(v,0,&n) || n<1 || n>8) return -EINVAL;",
"\ta52_447_wave=n;",
"\ta52_447_source_cmdline=true;",
"\treturn 0;","}",
'early_param("a52.wave",a52_447_parse_wave);',
"#define GATE_DEFAULT(name,initial) \\",
"\tstatic int a52_447_override_##name=(initial); \\",
"\tstatic int __init a52_447_parse_##name(char *v) \\",
"\t{ \\",
"\t\tunsigned int n; \\",
"\t\tif (!v || kstrtouint(v,0,&n) || n>1) return -EINVAL; \\",
"\t\ta52_447_override_##name=n; \\",
"\t\ta52_447_source_cmdline=true; \\",
"\t\treturn 0; \\",
"\t} \\",
'\tearly_param("a52." #name,a52_447_parse_##name);',
"#define GATE(name) GATE_DEFAULT(name,-1)",
]
    for _,name in GATES:
        if name=="mss":
            lines+=["GATE_DEFAULT(mss,IS_ENABLED(CONFIG_A52_MSS_DEFAULT))"]
        elif name=="ipa":
            lines+=["GATE_DEFAULT(ipa,IS_ENABLED(CONFIG_A52_IPA_DEFAULT))"]
        else:lines+=["GATE("+name+")"]
    lines+=["struct a52_447_gate { const char *name; unsigned int wave; int *override; };",
            "static struct a52_447_gate gates[A52_447_COUNT] = {"]
    for wave,name in GATES:
        lines+=['\t[A52_447_'+name.upper()+'] = {"'+name+'",'+str(wave)+',&a52_447_override_'+name+'},']
    lines+=["};",
"bool a52_447_enabled(enum a52_447_subsys id)",
"{","\tif (id < 0 || id >= A52_447_COUNT) return false;",
"\tif (*gates[id].override == 0) return false;",
"\tif (*gates[id].override > 0) return true;",
"\treturn a52_447_wave >= gates[id].wave;","}",
"EXPORT_SYMBOL_GPL(a52_447_enabled);",
"int a52_447_probe_allowed(struct device *dev, enum a52_447_subsys id)",
"{","\tif (id < 0 || id >= A52_447_COUNT) return -EINVAL;",
"\tif (a52_447_enabled(id)) return 0;",
'\tif (dev) dev_info(dev,"P447 W%u %s DISABLED (a52.%s=0)\\n",',
"\t\t\tgates[id].wave,gates[id].name,gates[id].name);",
"\treturn -ENODEV;","}",
"EXPORT_SYMBOL_GPL(a52_447_probe_allowed);",
"static int __init a52_447_gates_init(void)",
"{","\tint i;",
'\tpr_info("P447 GATES source=%s wave=%u mss=%d ipa=%d new_drivers_only=1\\n",',
'\t\ta52_447_source_cmdline?"cmdline":"kconfig",a52_447_wave,',
'\t\ta52_447_enabled(A52_447_MSS),a52_447_enabled(A52_447_IPA));',
'\tpr_info("P447 GATES initialized wave=%u new_drivers_only=1\\n",a52_447_wave);',
"\tfor (i=0;i<A52_447_COUNT;i++)",
'\t\tpr_info("P447 W%u %s %s override=%d\\n",gates[i].wave,',
'\t\t\tgates[i].name,a52_447_enabled(i)?"ARMED":"DISABLED",',
"\t\t\t*gates[i].override);",
"\treturn 0;","}",
"subsys_initcall(a52_447_gates_init);"]
    return "\n".join(lines)+"\n"

def apply(root):
    p=root/DIR
    p.mkdir(parents=True,exist_ok=True)
    (p/"a52_447_gates.c").write_text(source())
    (p/"Makefile").write_text("obj-y += a52_447_gates.o\n")
    (p/"Kconfig").write_text(kconfig())
    h=root/HEADER
    h.parent.mkdir(parents=True,exist_ok=True)
    h.write_text(header())
    k=root/"drivers/Kconfig"
    data=k.read_text()
    line='source "drivers/a52_447/Kconfig"\n'
    if line not in data:
        pos=data.rfind("endmenu")
        if pos<0:raise RuntimeError("P447C drivers/Kconfig has no endmenu")
        data=data[:pos]+line+"\n"+data[pos:]
        k.write_text(data)
    mk=root/"drivers/Makefile"
    data=mk.read_text()
    marker="obj-y += a52_447/\n"
    if marker not in data:
        data+="\n# "+TAG+" (wave controlled at compile time)\n"+marker
        mk.write_text(data)

def check(root):
    p=root/DIR
    c=(p/"a52_447_gates.c").read_text()
    h=(root/HEADER).read_text()
    mk=(root/"drivers/Makefile").read_text()
    assert TAG in c and TAG in h and mk.count("obj-y += a52_447/")==1
    assert 'source "drivers/a52_447/Kconfig"' in (root/"drivers/Kconfig").read_text()
    assert "config A52_WAVE_DEFAULT" in (p/"Kconfig").read_text()
    assert "CONFIG_A52_WAVE_DEFAULT" in c
    assert "P447 GATES source=%s wave=%u mss=%d ipa=%d" in c
    assert c.count('early_param("a52.wave"')==1
    assert c.count("GATE(")==len(GATES)-2+1
    assert c.count("GATE_DEFAULT(")==4
    for wave,name in GATES:
        assert "A52_447_"+name.upper() in h
        assert f'{{"{name}",{wave},&a52_447_override_{name}}}' in c
        if name not in ("mss","ipa"):assert "GATE("+name+")" in c
    print(f"P447C GATES PASS {len(GATES)} independent switches, .config wave/MSS/IPA defaults, cmdline override optional")

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:apply(a.root)
    check(a.root)
