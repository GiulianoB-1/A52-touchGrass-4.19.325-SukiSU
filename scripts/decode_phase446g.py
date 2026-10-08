#!/usr/bin/env python3
from __future__ import annotations
import base64, zlib
from pathlib import Path

p=Path(__file__).with_name("decode_phase446d.py.z64")
src=zlib.decompress(base64.b64decode(p.read_text().strip())).decode("utf-8","replace")
src=src.replace("REC_BYTES=664","REC_BYTES=672",1)
src=src.replace(
    "0x100:'PERIODIC'}",
    "0x190:'SMMU_COMPONENT_MATCH_SKIPPED',0x191:'TE_IRQ_SKIP_PRE',0x192:'TE_IRQ_SKIP_POST',"
    "0x193:'PANEL_LATE_SAFE',0x194:'PANEL_ENTER',0x195:'PANEL_LOCKED',"
    "0x196:'SS_ON_PRE_PRE',0x197:'SS_ON_PRE_POST',0x198:'ON_CMD_PRE',"
    "0x199:'ON_CMD_POST',0x19a:'ON_POST_PRE',0x19b:'ON_POST_POST',"
    "0x19c:'PANEL_RETURN',0x19d:'DISPLAY_PANEL_RETURN',"
    "0x1a0:'F0_TX_ENTER',0x1a1:'F0_MODE_DONE',0x1a2:'F0_VALIDATE_DONE',"
    "0x1a3:'F0_ARMED',0x1a4:'F0_KICKOFF_WRAP_PRE',0x1a5:'F0_HW_KICK_PRE',"
    "0x1a6:'F0_HW_KICK_POST',0x1a7:'F0_WAIT_ENTER',0x1a8:'F0_WAIT_SHORT_DONE',"
    "0x1a9:'F0_WAIT_RESULT',0x1aa:'F0_R1_BEGIN',0x1ab:'F0_R1_RESULT',"
    "0x1ac:'F0_TX_RETURN',0x1ad:'F0_KICKOFF_WRAP_POST',0x100:'PERIODIC'}",
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
