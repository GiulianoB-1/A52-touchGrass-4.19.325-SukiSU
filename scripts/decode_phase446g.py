#!/usr/bin/env python3
from __future__ import annotations
import base64, zlib
from pathlib import Path

p=Path(__file__).with_name("decode_phase446d.py.z64")
src=zlib.decompress(base64.b64decode(p.read_text().strip())).decode("utf-8","replace")
src=src.replace("REC_BYTES=664","REC_BYTES=672",1)
src=src.replace(
    "0x100:'PERIODIC'}",
    "0x190:'SMMU_COMPONENT_MATCH_SKIPPED',0x191:'TE_IRQ_SKIP_PRE',0x192:'TE_IRQ_SKIP_POST',0x100:'PERIODIC'}",
    1,
)
src=src.replace(
    "r['rcg']=U32(30);r['pll0']=U32(9);r['crc32']=U32();r['commit']=U32()",
    "r['rcg']=U32(30);r['pll0']=U32(9);r['te23_cfg']=U32();r['te23_intr_cfg']=U32();r['crc32']=U32();r['commit']=U32()",
    1,
)
src=src.replace(
    ' pll={r["pll0"][0]:08x}/{r["pll0"][1]:08x}/{r["pll0"][2]:08x}/{r["pll0"][3]:08x}/{r["pll0"][8]:08x} crc=',
    ' pll={r["pll0"][0]:08x}/{r["pll0"][1]:08x}/{r["pll0"][2]:08x}/{r["pll0"][3]:08x}/{r["pll0"][8]:08x} te23={r["te23_cfg"]:08x}/f={(r["te23_cfg"]>>2)&0xf:x}/irq={r["te23_intr_cfg"]:08x} crc=',
    1,
)
exec(compile(src,"decode_phase446g-expanded.py","exec"))
