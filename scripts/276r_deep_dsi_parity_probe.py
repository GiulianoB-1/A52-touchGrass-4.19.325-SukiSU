#!/usr/bin/env python3
from __future__ import annotations
import difflib, hashlib, re, sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root> <touchgrass-root>")
ROOT, TG = Path(sys.argv[1]), Path(sys.argv[2])
OUT = Path('phase276r-deep-path-parity-before.txt')
PAIRS = [
    ('dsi_host_transfer', ROOT/'drivers/a52_display/msm/dsi/dsi_display.c', TG/'techpack/display/msm/dsi/dsi_display.c'),
    ('dsi_ctrl_cmd_transfer', ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl.c', TG/'techpack/display/msm/dsi/dsi_ctrl.c'),
    ('dsi_message_tx', ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl.c', TG/'techpack/display/msm/dsi/dsi_ctrl.c'),
]

def extract(text: str, name: str) -> str:
    # Accept static/non-static and ssize_t/int return types. Find a real definition.
    pat = re.compile(r'^[ \t]*(?:static[ \t]+)?(?:int|ssize_t)[ \t]+' + re.escape(name) + r'\s*\([^;]*?\)\s*\{', re.M|re.S)
    ms = list(pat.finditer(text))
    if len(ms) != 1:
        raise RuntimeError(f'{name}: definition count {len(ms)}')
    m = ms[0]; start = m.start(); brace = text.find('{', m.start(), m.end())
    depth=0; ins=inc=esc=False
    for i in range(brace, len(text)):
        c=text[i]
        if esc: esc=False; continue
        if c=='\\' and (ins or inc): esc=True; continue
        if c=='"' and not inc: ins=not ins; continue
        if c=="'" and not ins: inc=not inc; continue
        if ins or inc: continue
        if c=='{': depth += 1
        elif c=='}':
            depth -= 1
            if depth == 0: return text[start:i+1]
    raise RuntimeError(f'{name}: unterminated')

def sha(s): return hashlib.sha256(s.encode()).hexdigest()

lines=[]; ok=True
for name,gp,tp in PAIRS:
    g=extract(gp.read_text(), name)
    t=extract(tp.read_text(), name)
    match=(g==t); ok &= match
    lines += [f'function={name}', f'gki_path={gp}', f'touchgrass_path={tp}', f'gki_sha256={sha(g)}', f'touchgrass_sha256={sha(t)}', f'exact_match={int(match)}']
    if not match:
        lines += list(difflib.unified_diff(t.splitlines(), g.splitlines(), fromfile='TouchGrass:'+name, tofile='GKI:'+name, lineterm=''))
    lines.append('')
lines.append(f'all_exact_match={int(ok)}')
OUT.write_text('\n'.join(lines)+'\n')
print(OUT.read_text(), end='')
raise SystemExit(0 if ok else 2)
