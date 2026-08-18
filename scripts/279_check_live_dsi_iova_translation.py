#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

SMMU = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1'


def fail(msg: str) -> None:
    raise SystemExit('Phase279 check FAIL: ' + msg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    args = ap.parse_args()
    smmu_path = args.root / SMMU
    dsi_path = args.root / DSI
    if not smmu_path.is_file() or not dsi_path.is_file():
        fail('reconstructed source missing')
    smmu = smmu_path.read_text()
    dsi = dsi_path.read_text()
    joined = smmu + '\n' + dsi

    required = [
        'A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1',
        'A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1',
        MARK,
        'void a52_p279_display_iova_snapshot(unsigned int point,',
        'void a52_p279_display_fault_snapshot(unsigned int point)',
        'cfg = cb->cfg;',
        'smmu_domain = cfg_to_smmu_domain(cfg);',
        'ops = smmu_domain->pgtbl_ops;',
        'pa_first = ops->iova_to_phys(ops, iova);',
        'pa_last = ops->iova_to_phys(ops, end);',
        'end = iova + (dma_addr_t)len - 1;',
        'cached_ttbr = cb->ttbr[0];',
        'cached_tcr = cb->tcr[0];',
        'ARM_SMMU_CB_TTBR0', 'ARM_SMMU_CB_TCR',
        'ARM_SMMU_CB_FSR', 'ARM_SMMU_CB_FSYNR0', 'ARM_SMMU_CB_FAR',
        'ARM_SMMU_GR1_CBFRSYNRA(ctx->cbndx)',
        'ARM_SMMU_GR0_sGFSR', 'ARM_SMMU_GR0_sGFSYNR0',
        'ARM_SMMU_GR0_sGFSYNR1', 'ARM_SMMU_GR0_sGFSYNR2',
        'SMMU P279 I p=%u sid=%x cb=%u i=%llx l=%u e=%llx p0=%llx p1=%llx',
        'SMMU P279 T p=%u sid=%x r=%d ht=%llx ct=%llx hr=%x cr=%x',
        'SMMU P279 F p=%u sid=%x cb=%u fs=%x sy=%x far=%llx cfr=%x',
        'SMMU P279 G p=%u gf=%x g0=%x g1=%x g2=%x',
        'EXPORT_SYMBOL_GPL(a52_p279_display_iova_snapshot);',
        'EXPORT_SYMBOL_GPL(a52_p279_display_fault_snapshot);',
        'a52_p279_display_iova_snapshot(0, cmd_mem->offset,',
        'a52_p279_display_fault_snapshot(0);',
        'a52_p279_display_fault_snapshot(1);',
        'a52_p279_display_fault_snapshot(2);',
        'P276 H K o=%llx l=%u h=%x',
    ]
    for token in required:
        if token not in joined:
            fail('missing token: ' + token)

    if smmu.count(MARK) != 1 or dsi.count(MARK) != 1:
        fail(f'marker count smmu={smmu.count(MARK)} dsi={dsi.count(MARK)}, expected 1/1')
    if dsi.count('a52_p279_display_iova_snapshot(0, cmd_mem->offset,') != 1:
        fail('DSI point-0 translation snapshot count is not exactly one')
    for point in (0, 1, 2):
        if dsi.count(f'a52_p279_display_fault_snapshot({point});') != 1:
            fail(f'Phase279 fault snapshot point {point} is not exactly one')
        if dsi.count(f'a52_p278_display_smmu_snapshot({point});') != 1:
            fail(f'Phase278 snapshot point {point} was not preserved exactly once')

    start = smmu.find('/* ' + MARK)
    end = smmu.find('EXPORT_SYMBOL_GPL(a52_p279_display_fault_snapshot);', start)
    if start < 0 or end < 0:
        fail('cannot isolate Phase279 SMMU helpers')
    helper = smmu[start:end]
    forbidden = [
        'ARM_SMMU_CB_ATS1PR',
        'arm_smmu_iova_to_phys_hard(',
        'arm_smmu_cb_write(', 'arm_smmu_cb_writeq(',
        'arm_smmu_gr0_write(', 'arm_smmu_gr1_write(',
        'tlb_flush', 'ops->map(', 'ops->unmap(',
        'map_pages(', 'unmap_pages(',
    ]
    for token in forbidden:
        if token in helper:
            fail('recorder helper contains state-changing/hardware-translation token: ' + token)

    p278_0 = dsi.find('a52_p278_display_smmu_snapshot(0);')
    p279_i = dsi.find('a52_p279_display_iova_snapshot(0, cmd_mem->offset,')
    p279_f0 = dsi.find('a52_p279_display_fault_snapshot(0);')
    prog = dsi.find('a52_ackfr_record("P276 H K o=%llx l=%u h=%x"', p279_f0)
    kick = dsi.find('dsi_hw_ops.kickoff_command(', p279_f0)
    if min(p278_0, p279_i, p279_f0, prog, kick) < 0 or not (
            p278_0 < p279_i < p279_f0 < prog < kick):
        fail('pre-kickoff ordering is not P278 -> mapping/root -> fault -> P276 program -> kickoff')

    p278_1 = dsi.find('a52_p278_display_smmu_snapshot(1);')
    p279_f1 = dsi.find('a52_p279_display_fault_snapshot(1);')
    readback = dsi.find('a52_ackfr_record("P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x"', p279_f1)
    if min(p278_1, p279_f1, readback) < 0 or not (p278_1 < p279_f1 < readback):
        fail('post-kickoff fault ordering is not preserved')

    p278_2 = dsi.find('a52_p278_display_smmu_snapshot(2);')
    p279_f2 = dsi.find('a52_p279_display_fault_snapshot(2);')
    status = dsi.find('status = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);', p279_f2)
    if min(p278_2, p279_f2, status) < 0 or not (p278_2 < p279_f2 < status):
        fail('timeout fault ordering is not preserved')

    if helper.count('ops->iova_to_phys(') != 2:
        fail('expected exactly two software io-pgtable translation calls')
    if helper.count('ARM_SMMU_GR0_sGFSR') != 1:
        fail('expected exactly one global fault-status read token in helper')

    print('Phase279 broad display failure snapshot checker: PASS')


if __name__ == '__main__':
    main()
