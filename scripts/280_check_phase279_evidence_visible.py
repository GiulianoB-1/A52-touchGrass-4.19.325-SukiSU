#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = 'A52_PHASE280_PHASE279_EVIDENCE_VISIBLE_V1'


def strip_ackfr_calls(text: str) -> str:
    out = []
    skipping = False
    depth = 0
    for line in text.splitlines(True):
        if MARK in line:
            continue
        if any(x in line for x in (
            'Phase280 changes only diagnostic record formatting.',
            'SMMU reads, software IOVA walk, DSI call sites, ordering and control',
            'flow are unchanged. Records use the existing admitted P276 namespace',
            'and are split to fit the 73-byte persistent payload without truncation.',
        )):
            continue
        if not skipping and 'a52_ackfr_record(' in line:
            skipping = True
            depth = line.count('(') - line.count(')')
            if depth <= 0 and ';' in line:
                skipping = False
            continue
        if skipping:
            depth += line.count('(') - line.count(')')
            if depth <= 0 and ';' in line:
                skipping = False
            continue
        out.append(line)
    return ''.join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--before-smmu', type=Path, required=True)
    ap.add_argument('--after-smmu', type=Path, required=True)
    ap.add_argument('--before-dsi', type=Path, required=True)
    ap.add_argument('--after-dsi', type=Path, required=True)
    ap.add_argument('--before-recorder', type=Path, required=True)
    ap.add_argument('--after-recorder', type=Path, required=True)
    args = ap.parse_args()

    before = args.before_smmu.read_text()
    after = args.after_smmu.read_text()
    if MARK not in after:
        raise SystemExit('Phase280 marker missing')
    if strip_ackfr_calls(before) != strip_ackfr_calls(after):
        raise SystemExit('Phase280 changed non-recorder-call SMMU source')
    if args.before_dsi.read_bytes() != args.after_dsi.read_bytes():
        raise SystemExit('Phase280 changed dsi_ctrl.c')
    if args.before_recorder.read_bytes() != args.after_recorder.read_bytes():
        raise SystemExit('Phase280 changed TouchGrass recorder implementation')

    required = [
        'ops->iova_to_phys(ops, iova)', 'ops->iova_to_phys(ops, end)',
        'ARM_SMMU_CB_TTBR0', 'ARM_SMMU_CB_TCR',
        'ARM_SMMU_CB_FSYNR0', 'ARM_SMMU_CB_FAR',
        'ARM_SMMU_GR0_sGFSR', 'ARM_SMMU_GR0_sGFSYNR0',
        'P276 278C0 ', 'P276 278S0 ',
        'P276 279I0 ', 'P276 279I1 ', 'P276 279I2 ',
        'P276 279T0 ', 'P276 279T1 ', 'P276 279T2 ',
        'P276 279F0 ', 'P276 279F1 ', 'P276 279G0 ', 'P276 279G1 ',
    ]
    for token in required:
        if token not in after:
            raise SystemExit('Phase280 required source token missing: ' + token)

    forbidden = [
        'ARM_SMMU_CB_ATS1PR', 'arm_smmu_iova_to_phys_hard(',
        'arm_smmu_cb_write(', 'arm_smmu_cb_writeq(',
        'arm_smmu_gr0_write(', 'arm_smmu_gr1_write(',
        'ops->map(', 'ops->unmap(', 'map_pages(', 'unmap_pages(',
    ]
    region = after.split('/* A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1', 1)[1]
    region = region.split('/* A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1', 1)[0]
    for token in forbidden:
        if token in region:
            raise SystemExit('Phase280 diagnostic region contains forbidden mutator: ' + token)

    recorder = args.after_recorder.read_text()
    if 'strncmp(fmt, "P276", 4)' not in recorder:
        raise SystemExit('Existing recorder does not admit P276 format namespace')
    if 'return !strncmp(message, "P276 ", 5)' not in recorder:
        raise SystemExit('Existing recorder does not retain P276 messages as critical')

    print('Phase280 non-perturbation and existing-recorder admission audit: PASS')


if __name__ == '__main__':
    main()
