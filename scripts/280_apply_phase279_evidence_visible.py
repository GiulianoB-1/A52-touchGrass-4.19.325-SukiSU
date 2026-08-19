#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

SMMU = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
MARK279 = 'A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1'
MARK280 = 'A52_PHASE280_PHASE279_EVIDENCE_VISIBLE_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 match, found {count}')
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK280 in text:
        return text
    if MARK279 not in text:
        raise SystemExit('Phase279 marker missing')

    # Do not change recorder admission. Re-emit the existing Phase278/279
    # read-only evidence through the already-admitted P276 namespace.
    text = replace_one(
        text,
        '/* A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1\n',
        '/* A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1\n'
        ' * A52_PHASE280_PHASE279_EVIDENCE_VISIBLE_V1\n'
        ' * Phase280 changes only diagnostic record formatting. The Phase278/279\n'
        ' * SMMU reads, software IOVA walk, DSI call sites, ordering and control\n'
        ' * flow are unchanged. Records use the existing admitted P276 namespace\n'
        ' * and are split to fit the 73-byte persistent payload without truncation.\n',
        'Phase280 marker insertion')

    replacements = [
        ('"SMMU P278 R sid=%x sme=%d invalid=1"', '"P276 278R s=%x e=%d x=1"'),
        ('"SMMU P278 R sid=%x sme=%d cb=%u"', '"P276 278R s=%x e=%d c=%u"'),
        ('"SMMU P278 X p=%u sid=%x invalid=1"', '"P276 278X q=%u s=%x x=1"'),
        ('"SMMU P278 X p=%u sid=%x rpm=%d"', '"P276 278X q=%u s=%x r=%d"'),
        ('"SMMU P278 X p=%u noctx=1"', '"P276 278X q=%u n=1"'),
        ('"SMMU P279 X p=%u sid=%x r=2"', '"P276 279X q=%u s=%x r=2"'),
        ('"SMMU P279 X p=%u sid=%x r=3"', '"P276 279X q=%u s=%x r=3"'),
        ('"SMMU P279 X p=%u sid=%x r=4"', '"P276 279X q=%u s=%x r=4"'),
        ('"SMMU P279 X p=%u sid=%x r=5"', '"P276 279X q=%u s=%x r=5"'),
        ('"SMMU P279 X p=%u sid=%x r=6"', '"P276 279X q=%u s=%x r=6"'),
        ('"SMMU P279 X p=%u sid=%x r=7"', '"P276 279X q=%u s=%x r=7"'),
        ('"SMMU P279 X p=%u sid=0 r=1"', '"P276 279X q=%u s=0 r=1"'),
        ('"SMMU P279 X p=%u sid=0 r=8"', '"P276 279X q=%u s=0 r=8"'),
    ]
    for old, new in replacements:
        text = replace_one(text, old, new, old)

    old = '''\t\ta52_ackfr_record(\n\t\t\t"SMMU P278 C p=%u sid=%x sme=%d cb=%u a=%x sc=%x m=%u f=%x",\n\t\t\tpoint, ctx->sid, ctx->sme, ctx->cbndx, actlr, sctlr,\n\t\t\t!!(sctlr & ARM_SMMU_SCTLR_M), fsr);\n\t\ta52_ackfr_record(\n\t\t\t"SMMU P278 S p=%u s2=%x ty=%u xcb=%u smr=%x cbar=%x",\n\t\t\tpoint, s2cr, type, stream_cb, smr, cbar);\n'''
    new = '''\t\ta52_ackfr_record(\n\t\t\t"P276 278C0 q=%u s=%x e=%d c=%u a=%x",\n\t\t\tpoint, ctx->sid, ctx->sme, ctx->cbndx, actlr);\n\t\ta52_ackfr_record(\n\t\t\t"P276 278C1 q=%u s=%x x=%x m=%u f=%x",\n\t\t\tpoint, ctx->sid, sctlr,\n\t\t\t!!(sctlr & ARM_SMMU_SCTLR_M), fsr);\n\t\ta52_ackfr_record(\n\t\t\t"P276 278S0 q=%u s=%x z=%x t=%u x=%u",\n\t\t\tpoint, ctx->sid, s2cr, type, stream_cb);\n\t\ta52_ackfr_record(\n\t\t\t"P276 278S1 q=%u s=%x r=%x b=%x",\n\t\t\tpoint, ctx->sid, smr, cbar);\n'''
    text = replace_one(text, old, new, 'Phase278 state record split')

    old = '''\t\ta52_ackfr_record(\n\t\t\t"SMMU P279 I p=%u sid=%x cb=%u i=%llx l=%u e=%llx p0=%llx p1=%llx",\n\t\t\tpoint, ctx->sid, ctx->cbndx,\n\t\t\t(unsigned long long)iova, len,\n\t\t\t(unsigned long long)end,\n\t\t\t(unsigned long long)pa_first,\n\t\t\t(unsigned long long)pa_last);\n\t\ta52_ackfr_record(\n\t\t\t"SMMU P279 T p=%u sid=%x r=%d ht=%llx ct=%llx hr=%x cr=%x",\n\t\t\tpoint, ctx->sid, ret,\n\t\t\t(unsigned long long)hw_ttbr,\n\t\t\t(unsigned long long)cached_ttbr,\n\t\t\thw_tcr, cached_tcr);\n'''
    new = '''\t\ta52_ackfr_record(\n\t\t\t"P276 279I0 q=%u s=%x c=%u i=%llx n=%u",\n\t\t\tpoint, ctx->sid, ctx->cbndx,\n\t\t\t(unsigned long long)iova, len);\n\t\ta52_ackfr_record(\n\t\t\t"P276 279I1 q=%u s=%x e=%llx a=%llx",\n\t\t\tpoint, ctx->sid, (unsigned long long)end,\n\t\t\t(unsigned long long)pa_first);\n\t\ta52_ackfr_record(\n\t\t\t"P276 279I2 q=%u s=%x b=%llx",\n\t\t\tpoint, ctx->sid, (unsigned long long)pa_last);\n\t\ta52_ackfr_record(\n\t\t\t"P276 279T0 q=%u s=%x r=%d", point, ctx->sid, ret);\n\t\ta52_ackfr_record(\n\t\t\t"P276 279T1 q=%u s=%x h=%llx c=%llx",\n\t\t\tpoint, ctx->sid, (unsigned long long)hw_ttbr,\n\t\t\t(unsigned long long)cached_ttbr);\n\t\ta52_ackfr_record(\n\t\t\t"P276 279T2 q=%u s=%x h=%x c=%x",\n\t\t\tpoint, ctx->sid, hw_tcr, cached_tcr);\n'''
    text = replace_one(text, old, new, 'Phase279 IOVA/root record split')

    old = '''\t\ta52_ackfr_record(\n\t\t\t"SMMU P279 F p=%u sid=%x cb=%u fs=%x sy=%x far=%llx cfr=%x",\n\t\t\tpoint, ctx->sid, ctx->cbndx, fsr, fsynr0,\n\t\t\t(unsigned long long)far, cbfrsynra);\n'''
    new = '''\t\ta52_ackfr_record(\n\t\t\t"P276 279F0 q=%u s=%x c=%u f=%x y=%x",\n\t\t\tpoint, ctx->sid, ctx->cbndx, fsr, fsynr0);\n\t\ta52_ackfr_record(\n\t\t\t"P276 279F1 q=%u s=%x a=%llx r=%x",\n\t\t\tpoint, ctx->sid, (unsigned long long)far, cbfrsynra);\n'''
    text = replace_one(text, old, new, 'Phase279 context-fault record split')

    old = '''\t\t\ta52_ackfr_record(\n\t\t\t\t"SMMU P279 G p=%u gf=%x g0=%x g1=%x g2=%x",\n\t\t\t\tpoint, gfsr, g0, g1, g2);\n'''
    new = '''\t\t\ta52_ackfr_record(\n\t\t\t\t"P276 279G0 q=%u f=%x a=%x b=%x",\n\t\t\t\tpoint, gfsr, g0, g1);\n\t\t\ta52_ackfr_record(\n\t\t\t\t"P276 279G1 q=%u c=%x", point, g2);\n'''
    text = replace_one(text, old, new, 'Phase279 global-fault record split')
    return text


def validate(text: str) -> None:
    required = [
        MARK279, MARK280,
        'P276 278R s=%x e=%d c=%u',
        'P276 278C0 q=%u s=%x e=%d c=%u a=%x',
        'P276 278C1 q=%u s=%x x=%x m=%u f=%x',
        'P276 278S0 q=%u s=%x z=%x t=%u x=%u',
        'P276 278S1 q=%u s=%x r=%x b=%x',
        'P276 279I0 q=%u s=%x c=%u i=%llx n=%u',
        'P276 279I1 q=%u s=%x e=%llx a=%llx',
        'P276 279I2 q=%u s=%x b=%llx',
        'P276 279T0 q=%u s=%x r=%d',
        'P276 279T1 q=%u s=%x h=%llx c=%llx',
        'P276 279T2 q=%u s=%x h=%x c=%x',
        'P276 279F0 q=%u s=%x c=%u f=%x y=%x',
        'P276 279F1 q=%u s=%x a=%llx r=%x',
        'P276 279G0 q=%u f=%x a=%x b=%x',
        'P276 279G1 q=%u c=%x',
    ]
    for token in required:
        if token not in text:
            raise SystemExit('Phase280 required marker missing: ' + token)
    for token in ['"SMMU P278 ', '"SMMU P279 ']:
        if token in text:
            raise SystemExit('Phase280 still contains recorder-rejected runtime prefix: ' + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    path = args.root / SMMU
    if not path.is_file():
        raise SystemExit(f'missing source: {path}')
    text = path.read_text()
    if not args.check_only:
        text = patch(text)
        path.write_text(text)
    validate(text)
    print('Phase280 Phase279-evidence visibility patch: PASS')

if __name__ == '__main__':
    main()
