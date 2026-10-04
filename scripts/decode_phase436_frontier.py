#!/usr/bin/env python3
from pathlib import Path
import base64,zlib
p=Path(__file__).with_suffix(Path(__file__).suffix+'.z64')
s=zlib.decompress(base64.b64decode(p.read_bytes())).decode()
# Reserved RAM on this device can suffer sparse bit damage across reboot.
# Preserve strict CRC-valid reporting, but if META has the correct structural
# identity/commit allow a degraded decode so later fixed slots are not hidden.
old_valid = "    valid=(magic==MAGIC and ver==VERSION and stage<64 and slot==stage and commit==COMMIT and crc32c(b[:CRC_OFF])==crc)\n"
new_valid = (
    "    basic=(magic==MAGIC and ver==VERSION and stage<64 and slot==stage and commit==COMMIT)\n"
    "    valid=(basic and crc32c(b[:CRC_OFF])==crc)\n"
)
if old_valid not in s:
    raise RuntimeError("Phase436 decoder: validity anchor missing")
s=s.replace(old_valid,new_valid,1)
s=s.replace("return dict(valid=valid,magic=", "return dict(valid=valid,basic=basic,magic=", 1)

old_meta = '''    meta=[copies[c][0] for c in range(2) if copies[c][0] and copies[c][0]['valid']]
    if not meta:
        print(f'source={src}\nmeta_valid=False')
        for c in range(2):
            r=copies[c][0]; print(f'copy{c}_meta magic={r["magic"]:#x} ver={r["ver"]} stage={r["stage"]} commit={r["commit"]:#x} crc={r["crc"]:#x}' if r else f'copy{c}_meta missing')
        return 2
'''
new_meta = '''    meta=[copies[c][0] for c in range(2) if copies[c][0] and copies[c][0]['valid']]
    degraded_meta=False
    if not meta:
        meta=[copies[c][0] for c in range(2) if copies[c][0] and copies[c][0].get('basic')]
        degraded_meta=bool(meta)
        print(f'source={src}\\nmeta_valid=False degraded_meta={degraded_meta}')
        for c in range(2):
            r=copies[c][0]; print(f'copy{c}_meta magic={r["magic"]:#x} ver={r["ver"]} stage={r["stage"]} commit={r["commit"]:#x} crc={r["crc"]:#x}' if r else f'copy{c}_meta missing')
        if not meta:
            return 2
'''
if old_meta not in s:
    raise RuntimeError("Phase436 decoder: meta anchor missing")
s=s.replace(old_meta,new_meta,1)
old_sel="valid=[copies[c][s] for c in range(2) if copies[c][s] and copies[c][s]['valid'] and copies[c][s]['boot']==boot]"
new_sel="valid=[copies[c][s] for c in range(2) if copies[c][s] and (copies[c][s]['valid'] or copies[c][s].get('basic')) and copies[c][s]['boot']==boot]"
if old_sel not in s:
    raise RuntimeError("Phase436 decoder: selection anchor missing")
s=s.replace(old_sel,new_sel,1)

exec(compile(s,str(p)[:-4],"exec"),globals(),globals())
