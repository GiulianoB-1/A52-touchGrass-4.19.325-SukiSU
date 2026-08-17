#!/usr/bin/env python3
from __future__ import annotations
import difflib, hashlib, re, sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit(f"usage: {sys.argv[0]} <gki-common-root> <touchgrass-root>")
ROOT, TG = Path(sys.argv[1]), Path(sys.argv[2])
OUT = Path('phase276r-deep-path-parity-before.txt')

# Every function on the already-proven software path plus the exact low-level
# hardware functions that program memory DMA and expose controller errors.
PAIRS = [
    ('path', 'dsi_host_transfer', ROOT/'drivers/a52_display/msm/dsi/dsi_display.c', TG/'techpack/display/msm/dsi/dsi_display.c'),
    ('path', 'dsi_ctrl_cmd_transfer', ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl.c', TG/'techpack/display/msm/dsi/dsi_ctrl.c'),
    ('path', 'dsi_message_tx', ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl.c', TG/'techpack/display/msm/dsi/dsi_ctrl.c'),
    ('path', 'dsi_kickoff_msg_tx', ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl.c', TG/'techpack/display/msm/dsi/dsi_ctrl.c'),
    ('path', 'dsi_ctrl_dma_cmd_wait_for_done', ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl.c', TG/'techpack/display/msm/dsi/dsi_ctrl.c'),
    ('hw', 'dsi_ctrl_hw_cmn_kickoff_command', ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c', TG/'techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c'),
    ('hw', 'dsi_ctrl_hw_cmn_get_error_status', ROOT/'drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c', TG/'techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c'),
    ('hw', 'dsi_catalog_cmn_init', ROOT/'drivers/a52_display/msm/dsi/dsi_catalog.c', TG/'techpack/display/msm/dsi/dsi_catalog.c'),
]

def extract(text: str, name: str) -> str:
    # Keep this intentionally strict: the compared implementation must resolve
    # to one concrete C definition in both trees.
    pat = re.compile(
        r'^[ \t]*(?:static[ \t]+)?(?:int|ssize_t|void|bool|u32|u64)[ \t]+'
        + re.escape(name) + r'\s*\([^;]*?\)\s*\{', re.M | re.S)
    ms = list(pat.finditer(text))
    if len(ms) != 1:
        raise RuntimeError(f'{name}: definition count {len(ms)}')
    m = ms[0]; start = m.start(); brace = text.find('{', m.start(), m.end())
    depth = 0; ins = inc = esc = False
    for i in range(brace, len(text)):
        c = text[i]
        if esc: esc = False; continue
        if c == '\\' and (ins or inc): esc = True; continue
        if c == '"' and not inc: ins = not ins; continue
        if c == "'" and not ins: inc = not inc; continue
        if ins or inc: continue
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: return text[start:i+1]
    raise RuntimeError(f'{name}: unterminated')

def remove_once(text: str, snippet: str, what: str) -> str:
    count = text.count(snippet)
    if count != 1:
        raise RuntimeError(f'{what}: expected exactly one known diagnostic snippet, found {count}')
    return text.replace(snippet, '', 1)

def normalize_known_gki_diagnostics(name: str, text: str) -> tuple[str, list[str]]:
    """Remove only recorder-only additions already present before Phase276R."""
    removed = []
    if name == 'dsi_ctrl_cmd_transfer':
        text = remove_once(
            text,
            '\n\ta52_dsi_log_msg(dsi_ctrl, msg, flags ? *flags : 0);',
            'pre-Phase276R DSI message recorder',
        )
        removed.append('a52_dsi_log_msg')
        text = remove_once(
            text,
            '\n\ta52_ackfr_record("DISP DSI done i=%d t=%02x rc=%d",\n'
            '\t\t\tdsi_ctrl->cell_index, msg->type, rc);',
            'pre-Phase276R DSI completion recorder',
        )
        removed.append('DISP DSI done')
    return text, removed

def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

lines = []
ok = True
path_ok = True
hw_ok = True
for group, name, gp, tp in PAIRS:
    if not gp.is_file():
        raise RuntimeError(f'{name}: GKI file missing: {gp}')
    if not tp.is_file():
        raise RuntimeError(f'{name}: TouchGrass file missing: {tp}')
    g_raw = extract(gp.read_text(), name)
    t = extract(tp.read_text(), name)
    g, removed = normalize_known_gki_diagnostics(name, g_raw)
    raw_match = (g_raw == t)
    match = (g == t)
    ok &= match
    if group == 'path': path_ok &= match
    if group == 'hw': hw_ok &= match
    lines += [
        f'group={group}',
        f'function={name}',
        f'gki_path={gp}',
        f'touchgrass_path={tp}',
        f'gki_raw_sha256={sha(g_raw)}',
        f'gki_normalized_sha256={sha(g)}',
        f'touchgrass_sha256={sha(t)}',
        f'known_diagnostics_removed={",".join(removed) if removed else "none"}',
        f'raw_exact_match={int(raw_match)}',
        f'exact_match={int(match)}',
    ]
    if not match:
        lines += list(difflib.unified_diff(
            t.splitlines(), g.splitlines(),
            fromfile='TouchGrass:' + name,
            tofile='GKI-normalized:' + name,
            lineterm=''))
    lines.append('')
lines += [
    f'path_exact_match={int(path_ok)}',
    f'low_level_hw_exact_match={int(hw_ok)}',
    f'all_exact_match={int(ok)}',
]
OUT.write_text('\n'.join(lines) + '\n')
print(OUT.read_text(), end='')
raise SystemExit(0 if ok else 2)
