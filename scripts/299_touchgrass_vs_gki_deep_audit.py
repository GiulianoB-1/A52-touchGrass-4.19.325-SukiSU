#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

TEXT_EXT = {'.c','.h','.S','.s','.dts','.dtsi','.yaml','.yml','.txt','.md','.mk','.cfg','.defconfig'}
TEXT_NAMES = {'Makefile','Kconfig'}

CRITICAL_FILES = [
    'dsi/dsi_ctrl.c',
    'dsi/dsi_ctrl_hw_cmn.c',
    'dsi/dsi_display.c',
    'dsi/dsi_panel.c',
    'msm_gem.c',
    'msm_gem.h',
    'msm_drv.c',
    'msm_atomic.c',
    'sde/sde_kms.c',
    'sde/sde_encoder.c',
    'sde/sde_connector.c',
    'sde/sde_power_handle.c',
]

PATTERNS = {
    'cmd-buffer-allocation': [r'MSM_BO_UNCACHED', r'msm_gem_new', r'msm_gem_get_iova', r'msm_gem_get_vaddr'],
    'cmd-buffer-sync': [r'msm_gem_sync', r'dma_sync_sg_for_device', r'dma_sync_sg_for_cpu', r'DMA_BIDIRECTIONAL'],
    'dsi-memory-fetch': [r'DSI_CTRL_CMD_FETCH_MEMORY', r'DSI_CTRL_CMD_FIFO_STORE', r'cmd_buffer_iova', r'tx_cmd_buf'],
    'dsi-dma-kickoff': [r'trigger_command_dma', r'kickoff_command', r'DSI_CMD_MODE_DMA_CTRL', r'DSI_CMD_MODE_DMA_SW_TRIGGER'],
    'dsi-completion': [r'DMA_DONE', r'wait_for_completion_timeout', r'reinit_completion', r'complete\('],
    'iommu': [r'iommu_', r'arm_smmu', r'msm_smmu', r'iova_to_phys', r'MSM_SMMU_DOMAIN_UNSECURE'],
    'dma-api': [r'dma_map', r'dma_unmap', r'dma_sync', r'dma_set_mask', r'dma_coherent'],
    'interconnect': [r'msm_bus_', r'icc_', r'interconnect', r'ab_quota', r'ib_quota'],
    'clock': [r'clk_prepare_enable', r'clk_disable_unprepare', r'clk_set_rate', r'clk_get_rate'],
    'runtime-pm': [r'pm_runtime_', r'runtime_suspend', r'runtime_resume'],
    'regulator': [r'regulator_enable', r'regulator_disable', r'regulator_set_voltage'],
    'atomic': [r'atomic_check', r'atomic_commit', r'prepare_commit', r'complete_commit', r'drm_dev_register'],
    'continuous-splash': [r'cont_splash', r'continuous_splash', r'splash_enabled'],
    'irq': [r'request_irq', r'enable_irq', r'disable_irq', r'irq_status', r'clear.*irq', r'IRQ_HANDLED'],
}

LOWER_LAYER_PAIRS = [
    ('ARM SMMU', 'drivers/iommu/arm-smmu.c', 'drivers/iommu/arm/arm-smmu/arm-smmu.c'),
    ('DMA mapping core', 'kernel/dma/mapping.c', 'kernel/dma/mapping.c'),
    ('DMA direct', 'kernel/dma/direct.c', 'kernel/dma/direct.c'),
    ('QCOM SCM', 'drivers/firmware/qcom_scm.c', 'drivers/firmware/qcom_scm.c'),
    ('RPMh', 'drivers/soc/qcom/rpmh.c', 'drivers/soc/qcom/rpmh.c'),
    ('Command DB', 'drivers/soc/qcom/cmd-db.c', 'drivers/soc/qcom/cmd-db.c'),
]

CONFIG_PREFIXES = (
    'CONFIG_IOMMU','CONFIG_ARM_SMMU','CONFIG_DMA','CONFIG_SWIOTLB','CONFIG_CMA',
    'CONFIG_INTERCONNECT','CONFIG_QCOM','CONFIG_COMMON_CLK','CONFIG_CLK_',
    'CONFIG_PM','CONFIG_REGULATOR','CONFIG_DRM','CONFIG_FB','CONFIG_IRQ',
)


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1<<20), b''):
            h.update(b)
    return h.hexdigest()


def is_text(path: Path) -> bool:
    return path.name in TEXT_NAMES or path.suffix in TEXT_EXT


def files(root: Path) -> dict[str,Path]:
    out={}
    if not root.exists(): return out
    for p in root.rglob('*'):
        if p.is_file() and '.git' not in p.parts:
            out[p.relative_to(root).as_posix()]=p
    return out


def read(path: Path) -> str:
    return path.read_text(errors='replace')


def contexts(path: Path, regexes: list[str], radius: int=3) -> list[dict]:
    if not path.exists() or not is_text(path): return []
    lines=read(path).splitlines()
    rx=[re.compile(x, re.I) for x in regexes]
    hits=[]
    for i,line in enumerate(lines):
        if any(r.search(line) for r in rx):
            lo=max(0,i-radius); hi=min(len(lines),i+radius+1)
            hits.append({'line':i+1,'text':line.strip(),'context':'\n'.join(f'{n+1:5d}: {lines[n]}' for n in range(lo,hi))})
    return hits


def parse_config(path: Path) -> dict[str,str]:
    d={}
    if not path.exists(): return d
    for raw in read(path).splitlines():
        if raw.startswith('CONFIG_') and '=' in raw:
            k,v=raw.split('=',1); d[k]=v
        elif raw.startswith('# CONFIG_') and raw.endswith(' is not set'):
            d[raw[2:].split(' ',1)[0]]='n'
    return d


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--touchgrass',type=Path,required=True)
    ap.add_argument('--gki',type=Path,required=True)
    ap.add_argument('--gki-config',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    tg=a.touchgrass; gki=a.gki; out=a.output
    tgdisp=tg/'techpack/display/msm'; gdisp=gki/'drivers/a52_display/msm'
    for p in (tgdisp,gdisp,a.gki_config):
        if not p.exists(): raise SystemExit(f'missing required input: {p}')
    out.mkdir(parents=True,exist_ok=True)
    (out/'diffs/display').mkdir(parents=True,exist_ok=True)
    (out/'contexts').mkdir(parents=True,exist_ok=True)

    T=files(tgdisp); G=files(gdisp); names=sorted(set(T)|set(G))
    rows=[]; counts=Counter(); changed=[]
    for rel in names:
        tp=T.get(rel); gp=G.get(rel)
        if tp and gp:
            th,gh=sha(tp),sha(gp)
            status='identical' if th==gh else 'modified'
        elif tp:
            th,gh,status=sha(tp),'','touchgrass-only'
        else:
            th,gh,status='',sha(gp),'gki-only'
        counts[status]+=1
        rows.append((rel,status,th,gh,tp.stat().st_size if tp else '',gp.stat().st_size if gp else ''))
        if status=='modified': changed.append(rel)
        if status=='modified' and is_text(tp) and is_text(gp) and tp.stat().st_size<2_000_000 and gp.stat().st_size<2_000_000:
            diff=''.join(difflib.unified_diff(read(tp).splitlines(True),read(gp).splitlines(True),fromfile='touchgrass/'+rel,tofile='gki/'+rel,n=5))
            target=out/'diffs/display'/(rel.replace('/','__')+'.diff')
            target.write_text(diff)
    with (out/'display-file-parity.tsv').open('w',newline='') as f:
        w=csv.writer(f,delimiter='\t'); w.writerow(['relative_path','status','touchgrass_sha256','gki_sha256','touchgrass_bytes','gki_bytes']); w.writerows(rows)

    critical=[]
    for rel in CRITICAL_FILES:
        tp=tgdisp/rel; gp=gdisp/rel
        critical.append({'file':rel,'touchgrass':tp.exists(),'gki':gp.exists(),'identical':tp.exists() and gp.exists() and sha(tp)==sha(gp),'touchgrass_sha256':sha(tp) if tp.exists() else None,'gki_sha256':sha(gp) if gp.exists() else None})
    (out/'critical-file-parity.json').write_text(json.dumps(critical,indent=2)+'\n')

    # Extract critical semantic contexts from both display trees.
    semantic={}
    for category,regexes in PATTERNS.items():
        semantic[category]={'touchgrass':[],'gki':[]}
        for label,root,key in [('touchgrass',tgdisp,'touchgrass'),('gki',gdisp,'gki')]:
            for rel,p in files(root).items():
                if not is_text(p): continue
                hs=contexts(p,regexes,2)
                for h in hs[:40]:
                    semantic[category][key].append({'file':rel,**h})
        # bounded human-readable context file
        lines=[f'# {category}']
        for side in ('touchgrass','gki'):
            lines += ['',f'## {side}']
            for h in semantic[category][side][:120]:
                lines += ['',f"### {h['file']}:{h['line']}",'```',h['context'],'```']
        (out/'contexts'/f'{category}.md').write_text('\n'.join(lines)+'\n')
    (out/'semantic-hit-counts.json').write_text(json.dumps({k:{s:len(v[s]) for s in ('touchgrass','gki')} for k,v in semantic.items()},indent=2)+'\n')

    # Fingerprint lower layers where unchanged vendor display code meets a new kernel.
    lower=[]
    for label,tr,gr in LOWER_LAYER_PAIRS:
        tp=tg/tr; gp=gki/gr
        item={'label':label,'touchgrass_path':tr,'gki_path':gr,'touchgrass_exists':tp.exists(),'gki_exists':gp.exists()}
        if tp.exists(): item['touchgrass_sha256']=sha(tp)
        if gp.exists(): item['gki_sha256']=sha(gp)
        if tp.exists() and gp.exists():
            t=read(tp); g=read(gp)
            item['identical']=sha(tp)==sha(gp)
            probes=['dma_sync_sg_for_device','dma_map_sg','iommu_map','iommu_unmap','iova_to_phys','arm_smmu_map','arm_smmu_attach_dev','pm_runtime','of_dma_configure']
            item['symbol_presence']={x:{'touchgrass':x in t,'gki':x in g} for x in probes}
            if is_text(tp) and is_text(gp) and tp.stat().st_size<2_000_000 and gp.stat().st_size<2_000_000:
                d=''.join(difflib.unified_diff(t.splitlines(True),g.splitlines(True),fromfile='touchgrass/'+tr,tofile='gki/'+gr,n=2))
                (out/'diffs'/(label.lower().replace(' ','-')+'.diff')).write_text(d)
        lower.append(item)
    (out/'lower-layer-fingerprint.json').write_text(json.dumps(lower,indent=2)+'\n')

    cfg=parse_config(a.gki_config)
    selected={k:v for k,v in sorted(cfg.items()) if k.startswith(CONFIG_PREFIXES)}
    (out/'phase296-critical-config.txt').write_text('\n'.join(f'{k}={v}' for k,v in selected.items())+'\n')

    # Inventory all project instrumentation/compatibility markers still present in reconstructed display tree.
    markers=[]
    for rel,p in G.items():
        if not is_text(p): continue
        for i,line in enumerate(read(p).splitlines(),1):
            if 'A52_PHASE' in line or 'P276 ' in line or 'GDM ' in line:
                markers.append({'file':rel,'line':i,'text':line.strip()})
    (out/'reconstructed-display-markers.json').write_text(json.dumps(markers,indent=2)+'\n')

    crit_changed=[x['file'] for x in critical if x['touchgrass'] and x['gki'] and not x['identical']]
    risks=[]
    def risk(rank,title,evidence,next_test): risks.append({'rank':rank,'title':title,'evidence':evidence,'next_test':next_test})
    risk(1,'DSI command-buffer DMA coherency / DMA-device semantic mismatch',
         'TouchGrass memory-fetch uses GEM-backed uncached memory, an IOVA and dma_sync_sg_for_device. The vendor display code is running on a different 5.10 DMA/IOMMU implementation; a valid-looking IOVA can coexist with wrong cache ownership or DMA-device semantics.',
         'At the target F0 5A 5A command record cmd_buffer_iova, translated PA, SG DMA address/length, aspace device name/coherent mask, first bytes before and after dma_sync, then compare device-visible memory if possible.')
    risk(2,'DSI command DMA programming or completion IRQ semantic mismatch',
         'The established boundary is memory-fetch kickoff followed by missing DMA_DONE. Register programming and IRQ acknowledge/order are therefore direct suspects even when probe/bind succeeds.',
         'Compare Golden/GKI DMA_CTRL, OFFSET, LENGTH, SW_TRIGGER, raw interrupt status, mask, clear and completion state at identical points.')
    risk(3,'Interconnect / bus vote mismatch during command fetch',
         'Probe can succeed with register access while a memory DMA fetch stalls if the path between memory and DSI is not voted or clocked equivalently. 4.19 vendor msm-bus assumptions are not the same implementation as 5.10 interconnect.',
         'Capture active bus/ICC votes and relevant clocks immediately before kickoff on Golden and GKI; test a minimally forced safe vote only if evidence shows a gap.')
    risk(4,'Runtime PM / clock / power-domain ordering mismatch',
         'A transplanted vendor driver may call the same functions while 5.10 provider/runtime-PM ordering differs. This can leave the controller register-visible but its DMA sub-block or bus path gated.',
         'Capture runtime PM usage/status plus byte/pixel/esc/core clock enabled/rate state at pre-kickoff and timeout.')
    risk(5,'Atomic handoff triggers a valid vendor path at the wrong lifecycle point',
         'Phase296 exists because probe and DRM bind are proven. If userspace opens DRM and submits atomic state at a lifecycle point different from Golden, the first panel command can expose a lower-layer latent defect.',
         'Use 296O/A/C/W/K markers to establish the caller/lifecycle boundary, then correlate it with the failing DSI transaction.')
    risk(6,'SMMU translation/root fault',
         'Still relevant structurally, but Phase279/280 were specifically designed to distinguish this and move downstream when IOVA/root/fault state are clean.',
         'Re-read retained Phase280 evidence before spending another boot; only revive this as top suspect if mapping/root/fault snapshots are abnormal.')
    (out/'risk-ranking.json').write_text(json.dumps(risks,indent=2)+'\n')

    report=[
        '# Phase299 TouchGrass vs reconstructed Phase296 GKI deep audit', '',
        '## Scope','',
        f'- TouchGrass display root: `{tgdisp}`',
        f'- Reconstructed GKI display root: `{gdisp}`',
        f'- Display files: identical={counts["identical"]}, modified={counts["modified"]}, TouchGrass-only={counts["touchgrass-only"]}, GKI-only={counts["gki-only"]}.','',
        '## Critical-path file parity',''
    ]
    for x in critical:
        state='IDENTICAL' if x['identical'] else ('MODIFIED' if x['touchgrass'] and x['gki'] else 'MISSING/EXTRA')
        report.append(f"- `{x['file']}`: **{state}**")
    report += ['', '## Highest-priority hypotheses','']
    for r in risks:
        report += [f"### {r['rank']}. {r['title']}",r['evidence'],'',f"Next discriminating test: {r['next_test']}",'']
    report += ['## Modified critical files','']
    report += [f'- `{x}`' for x in crit_changed] or ['- none']
    report += ['', '## Important interpretation','',
        'Identical vendor display source does not imply identical hardware behavior. The port changes the kernel services underneath it. A memory-fetch DSI command is a cross-subsystem event involving GEM memory, cache ownership, IOVA/SMMU translation, interconnect, clocks, controller DMA registers and IRQ completion. The audit therefore treats lower-layer semantic changes as first-class suspects.','']
    (out/'REPORT.md').write_text('\n'.join(report)+'\n')

    meta={'artifact_type':'phase299-source-audit-not-flashable','touchgrass_display_files':len(T),'gki_display_files':len(G),'counts':dict(counts),'critical_modified':crit_changed,'marker_count':len(markers)}
    (out/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    with (out/'SHA256SUMS').open('w') as f:
        for p in sorted(x for x in out.rglob('*') if x.is_file() and x.name!='SHA256SUMS'):
            f.write(f'{sha(p)}  {p.relative_to(out).as_posix()}\n')
    print(json.dumps(meta,indent=2))
    print((out/'REPORT.md').read_text())

if __name__=='__main__':
    main()
