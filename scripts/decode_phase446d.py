#!/usr/bin/env python3
from __future__ import annotations
import argparse
import struct
from pathlib import Path

MAGIC=0x363434504D4F4346
REC_COMMIT=0x446C0DE5
HDR_BYTES=4096
REC_BYTES=508

EVENTS={
0x000:"INIT",
0x003:"DRM_BIND",0x004:"POWER_INIT_PRE",0x005:"POWER_INIT_POST",
0x006:"KMS_MMU_PRE",0x007:"KMS_MMU_POST",
0x00a:"SPLASH_MAP_PRE",0x00b:"SPLASH_MAP_POST",
0x00c:"EARLY_MAP_PRE",0x00d:"EARLY_MAP_POST",
0x00e:"VBIF_INIT",0x00f:"IRQ_PREINSTALL",0x010:"CONT_SPLASH",
0x100:"PERIODIC",
0x160:"COMP0_PRE",0x161:"COMP0_POST",0x162:"COMP1_PRE",0x163:"COMP1_POST",
0x164:"COMP2_PRE",0x165:"COMP2_POST",0x166:"COMP3_PRE",0x167:"COMP3_POST",
0x168:"COMP4_PRE",0x169:"COMP4_POST",0x16a:"COMP5_PRE",0x16b:"COMP5_POST",
0x16c:"COMP6_PRE",0x16d:"COMP6_POST",0x16e:"COMP7_PRE",0x16f:"COMP7_POST",
0x170:"SDE_KMS_INIT_PRE",0x171:"SDE_KMS_INIT_POST",
0x172:"KMS_HW_INIT_PRE",0x173:"KMS_HW_INIT_POST",
0x174:"SDE_HW_INIT_ENTRY",0x175:"IOREMAP_PRE",0x176:"IOREMAP_POST",
0x177:"SPLASH_DATA_PRE",0x178:"SPLASH_DATA_POST",
0x179:"PM_RUNTIME_GET_PRE",0x17a:"PM_RUNTIME_GET_POST",
0x17b:"HW_BLOCKS_PRE",0x17c:"HW_BLOCKS_POST",
0x190:"DBUS_PRE",0x191:"DBUS_POST",0x192:"VREGS_PRE",0x193:"VREGS_POST",
0x194:"REG_BUS_PRE",0x195:"REG_BUS_POST",0x196:"RSC_PRE",0x197:"RSC_POST",
0x198:"CLKS_PRE",0x199:"CLKS_POST",0x19a:"POST_ENABLE_EVT_PRE",0x19b:"POST_ENABLE_EVT_POST",
0x1a0:"CLK_ENABLE_PRE",0x1a1:"CLK_ENABLE_POST",
0x1a2:"CLK_RATE_PRE",0x1a3:"CLK_RATE_POST",
0x1a4:"VREG_ENABLE_PRE",0x1a5:"VREG_ENABLE_POST",
}

def u32(b,o): return struct.unpack_from("<I",b,o)[0]
def s32(b,o): return struct.unpack_from("<i",b,o)[0]
def u64(b,o): return struct.unpack_from("<Q",b,o)[0]

def name4(v:int)->str:
    b=v.to_bytes(4,"little")
    return "".join(chr(x) if 32<=x<127 else "." for x in b).rstrip(".")

def name8(a:int,b:int)->str:
    q=a.to_bytes(4,"little")+b.to_bytes(4,"little")
    return "".join(chr(x) if 32<=x<127 else "." for x in q).rstrip(".")

def bitdist(a:int,b:int)->int:
    return (a^b).bit_count()

def decode_record(r:bytes):
    b2=240; b3=272
    return {
      "ns":u64(r,8),"seq":u32(r,16),"event":u32(r,20),"flags":u32(r,24),
      "aux0":u32(r,28),"aux1":u32(r,32),"frame":u32(r,36),
      "line":u32(r,68),
      "auto":u32(r,76),
      "mdp_cmd":u32(r,b2+0),"mdp_cfg":u32(r,b2+4),"mdp_m":u32(r,b2+8),"mdp_n":u32(r,b2+12),
      "mdp_d":u32(r,b2+16),"byte_cmd":u32(r,b2+20),"pclk_cmd":u32(r,b2+24),"esc_cmd":u32(r,b2+28),
      "vsync_cmd":u32(r,b3+0),"ahb_cmd":u32(r,b3+4),"pll_mode":u32(r,b3+8),"pll_l":u32(r,b3+12),
      "pll_frac":u32(r,b3+16),"pll_user":u32(r,b3+20),"pll_status":u32(r,b3+24),"pll_opmode":u32(r,b3+28),
      "magic":u64(r,0),"commit":u32(r,504),
    }

def main():
    ap=argparse.ArgumentParser(description="Decode Phase446d clock/power microscope records.")
    ap.add_argument("raw",type=Path)
    ap.add_argument("--all",action="store_true",help="include periodic records")
    ap.add_argument("--salvage",action="store_true",help="accept records with <=5 corrupted bits in magic/commit")
    a=ap.parse_args()
    b=a.raw.read_bytes()
    if len(b)<HDR_BYTES+REC_BYTES:
        raise SystemExit("input too small")
    count=min(u32(b,20), (len(b)-HDR_BYTES)//REC_BYTES, 2048)
    init_ns=u64(b,36)
    print(f"header_magic=0x{u64(b,0):016x} rec_bytes={u32(b,12)} count={count} init_ns={init_ns}")
    print("time_ms   seq event                 frame line  aux0/aux1          mdp_cmd  mdp_cfg  pll_mode pll_L    pll_status name")
    for i in range(count):
        r=b[HDR_BYTES+i*REC_BYTES:HDR_BYTES+(i+1)*REC_BYTES]
        d=decode_record(r)
        good=d["magic"]==MAGIC and d["commit"]==REC_COMMIT
        if not good and not (a.salvage and bitdist(d["magic"],MAGIC)<=5 and bitdist(d["commit"],REC_COMMIT)<=5):
            continue
        if not a.all and d["event"]==0x100:
            continue
        ev=d["event"]; label=EVENTS.get(ev,f"EV_{ev:03x}")
        extra=""
        if ev==0:
            flags=d["aux1"] & 0xf
            extra=f"cmdhash={d['aux0']:08x} id={d['aux1']>>16:04x} cmdflags={flags:x}"
        elif 0x160<=ev<=0x16f:
            if (ev & 1)==0: extra="component="+name8(d["aux0"],d["aux1"])
            else: extra=f"index={d['aux0']} rc={s32(r,32)}"
        elif ev in (0x1a0,0x1a2,0x1a4):
            extra=f"name4={name4(d['aux0'])} arg={d['aux1']}"
        elif ev in (0x1a1,0x1a3,0x1a5):
            extra=f"name4={name4(d['aux0'])} rc={s32(r,32)}"
        t=(d["ns"]-init_ns)/1e6 if init_ns and d["ns"]>=init_ns else d["ns"]/1e6
        print(f"{t:8.3f} {d['seq']:5d} {label:<21} {d['frame']:5d} {d['line']:4x} "
              f"{d['aux0']:08x}/{d['aux1']:08x} {d['mdp_cmd']:08x} {d['mdp_cfg']:08x} "
              f"{d['pll_mode']:08x} {d['pll_l']:08x} {d['pll_status']:08x} {extra}")
    print("\nclock word map:")
    print("mdp: CMD@0x107c CFG@0x1080 M@0x1084 N@0x1088 D@0x108c")
    print("other RCG CMD: byte0@0x10c4 pclk0@0x1064 esc0@0x10e0 vsync@0x10ac ahb@0x115c")
    print("pll0 FABIA: MODE@0x0 L@0x4 FRAC@0x38 USER_CTL@0xc STATUS@0x24 OPMODE@0x2c")

if __name__=="__main__":
    main()
