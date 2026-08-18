#!/usr/bin/env python3
from __future__ import annotations
import argparse
import shutil
import tempfile
from pathlib import Path

SMMU = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1'
DECL278 = 'extern void a52_p278_display_smmu_snapshot(unsigned int point);\n'
DECL279 = ('extern void a52_p279_display_iova_snapshot(unsigned int point,\n'
           '\t\tdma_addr_t iova, u32 len);\n'
           'extern void a52_p279_display_fault_snapshot(unsigned int point);\n')


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected 1 match, found {count}')
    return text.replace(old, new, 1)


def patch_smmu(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1' not in text:
        raise SystemExit('Phase278 live SMMU marker missing')

    anchor = 'EXPORT_SYMBOL_GPL(a52_p278_display_smmu_snapshot);\n'
    helper = r'''

/* A52_PHASE279_BROAD_DISPLAY_FAILURE_SNAPSHOT_V1
 * Broad but read-only cross-section at the already-proven DSI DMA failure
 * boundary. The command IOVA is translated through the software io-pgtable
 * walk only; this deliberately avoids arm_smmu_iova_to_phys_hard()/ATS1PR.
 * Context/global fault registers are read but never cleared here. TTBR0/TCR
 * are read back only to correlate the active hardware root with cached state.
 */
void a52_p279_display_iova_snapshot(unsigned int point,
		dma_addr_t iova, u32 len)
{
	bool any = false;
	int slot;

	for (slot = 0; slot < ARRAY_SIZE(a52_p278_display_ctx); slot++) {
		struct a52_p278_display_ctx *ctx = &a52_p278_display_ctx[slot];
		struct arm_smmu_device *smmu;
		struct arm_smmu_cb *cb;
		struct arm_smmu_cfg *cfg;
		struct arm_smmu_domain *smmu_domain;
		struct io_pgtable_ops *ops;
		dma_addr_t end = iova;
		phys_addr_t pa_first, pa_last;
		u64 hw_ttbr = 0, cached_ttbr;
		u32 hw_tcr = 0, cached_tcr;
		int ret;

		if (!ctx->valid)
			continue;
		any = true;
		smmu = ctx->smmu;
		if (!smmu || ctx->cbndx >= smmu->num_context_banks) {
			a52_ackfr_record("SMMU P279 X p=%u sid=%x r=2",
					  point, ctx->sid);
			continue;
		}

		cb = &smmu->cbs[ctx->cbndx];
		cfg = cb->cfg;
		if (!cfg) {
			a52_ackfr_record("SMMU P279 X p=%u sid=%x r=3",
					  point, ctx->sid);
			continue;
		}
		smmu_domain = cfg_to_smmu_domain(cfg);
		ops = smmu_domain->pgtbl_ops;
		if (!ops || !ops->iova_to_phys) {
			a52_ackfr_record("SMMU P279 X p=%u sid=%x r=4",
					  point, ctx->sid);
			continue;
		}

		if (len) {
			end = iova + (dma_addr_t)len - 1;
			if (end < iova) {
				a52_ackfr_record("SMMU P279 X p=%u sid=%x r=5",
						  point, ctx->sid);
				continue;
			}
		}

		pa_first = ops->iova_to_phys(ops, iova);
		pa_last = ops->iova_to_phys(ops, end);
		cached_ttbr = cb->ttbr[0];
		cached_tcr = cb->tcr[0];

		ret = arm_smmu_rpm_get(smmu);
		if (ret >= 0) {
			if (cfg->fmt == ARM_SMMU_CTX_FMT_AARCH32_S)
				hw_ttbr = arm_smmu_cb_read(smmu, ctx->cbndx,
							 ARM_SMMU_CB_TTBR0);
			else
				hw_ttbr = arm_smmu_cb_readq(smmu, ctx->cbndx,
							  ARM_SMMU_CB_TTBR0);
			hw_tcr = arm_smmu_cb_read(smmu, ctx->cbndx,
						 ARM_SMMU_CB_TCR);
			arm_smmu_rpm_put(smmu);
		}

		a52_ackfr_record(
			"SMMU P279 I p=%u sid=%x cb=%u i=%llx l=%u e=%llx p0=%llx p1=%llx",
			point, ctx->sid, ctx->cbndx,
			(unsigned long long)iova, len,
			(unsigned long long)end,
			(unsigned long long)pa_first,
			(unsigned long long)pa_last);
		a52_ackfr_record(
			"SMMU P279 T p=%u sid=%x r=%d ht=%llx ct=%llx hr=%x cr=%x",
			point, ctx->sid, ret,
			(unsigned long long)hw_ttbr,
			(unsigned long long)cached_ttbr,
			hw_tcr, cached_tcr);
	}

	if (!any)
		a52_ackfr_record("SMMU P279 X p=%u sid=0 r=1", point);
}
EXPORT_SYMBOL_GPL(a52_p279_display_iova_snapshot);

void a52_p279_display_fault_snapshot(unsigned int point)
{
	struct arm_smmu_device *global_smmu = NULL;
	bool any = false;
	int slot;

	for (slot = 0; slot < ARRAY_SIZE(a52_p278_display_ctx); slot++) {
		struct a52_p278_display_ctx *ctx = &a52_p278_display_ctx[slot];
		struct arm_smmu_device *smmu;
		u32 fsr, fsynr0, cbfrsynra;
		u64 far;
		int ret;

		if (!ctx->valid)
			continue;
		any = true;
		smmu = ctx->smmu;
		if (!smmu || ctx->cbndx >= smmu->num_context_banks) {
			a52_ackfr_record("SMMU P279 X p=%u sid=%x r=6",
					  point, ctx->sid);
			continue;
		}

		ret = arm_smmu_rpm_get(smmu);
		if (ret < 0) {
			a52_ackfr_record("SMMU P279 X p=%u sid=%x r=7",
					  point, ctx->sid);
			continue;
		}

		fsr = arm_smmu_cb_read(smmu, ctx->cbndx, ARM_SMMU_CB_FSR);
		fsynr0 = arm_smmu_cb_read(smmu, ctx->cbndx, ARM_SMMU_CB_FSYNR0);
		far = arm_smmu_cb_readq(smmu, ctx->cbndx, ARM_SMMU_CB_FAR);
		cbfrsynra = arm_smmu_gr1_read(smmu,
					 ARM_SMMU_GR1_CBFRSYNRA(ctx->cbndx));
		a52_ackfr_record(
			"SMMU P279 F p=%u sid=%x cb=%u fs=%x sy=%x far=%llx cfr=%x",
			point, ctx->sid, ctx->cbndx, fsr, fsynr0,
			(unsigned long long)far, cbfrsynra);

		if (global_smmu != smmu) {
			u32 gfsr, g0, g1, g2;

			gfsr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSR);
			g0 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR0);
			g1 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR1);
			g2 = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_sGFSYNR2);
			a52_ackfr_record(
				"SMMU P279 G p=%u gf=%x g0=%x g1=%x g2=%x",
				point, gfsr, g0, g1, g2);
			global_smmu = smmu;
		}

		arm_smmu_rpm_put(smmu);
	}

	if (!any)
		a52_ackfr_record("SMMU P279 X p=%u sid=0 r=8", point);
}
EXPORT_SYMBOL_GPL(a52_p279_display_fault_snapshot);
'''
    return replace_one(text, anchor, anchor + helper,
                       'Phase279 SMMU broad snapshot helper insertion')


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1' not in text:
        raise SystemExit('Phase278 live SMMU marker missing from dsi_ctrl.c')

    text = replace_one(
        text, DECL278,
        DECL278 + DECL279 + '/* ' + MARK + ' */\n',
        'Phase279 DSI declarations')

    old = '''\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p278_display_smmu_snapshot(0);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H K o=%llx l=%u h=%x",\n'''
    new = '''\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p278_display_smmu_snapshot(0);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p279_display_iova_snapshot(0, cmd_mem->offset,\n\t\t\t\t\t\tcmd_mem->length);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p279_display_fault_snapshot(0);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H K o=%llx l=%u h=%x",\n'''
    text = replace_one(text, old, new, 'Phase279 pre-kickoff broad snapshot')

    old = '''\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p278_display_smmu_snapshot(1);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x",\n'''
    new = '''\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p278_display_smmu_snapshot(1);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p279_display_fault_snapshot(1);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x",\n'''
    text = replace_one(text, old, new, 'Phase279 post-kickoff fault snapshot')

    old = '''\t\tif (a52_p276r_deep_active())\n\t\t\ta52_p278_display_smmu_snapshot(2);\n\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n'''
    new = '''\t\tif (a52_p276r_deep_active())\n\t\t\ta52_p278_display_smmu_snapshot(2);\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_p279_display_fault_snapshot(2);\n\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n'''
    return replace_one(text, old, new, 'Phase279 timeout fault snapshot')


def apply(root: Path) -> None:
    smmu = root / SMMU
    dsi = root / DSI
    if not smmu.is_file() or not dsi.is_file():
        raise SystemExit(f'missing reconstructed source: {smmu} / {dsi}')
    smmu.write_text(patch_smmu(smmu.read_text()))
    dsi.write_text(patch_dsi(dsi.read_text()))


def self_test(root: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for rel in (SMMU, DSI):
            src = root / rel
            if not src.is_file():
                raise SystemExit(f'missing source for self-test: {src}')
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        apply(tmp)
        once = [(tmp / rel).read_text() for rel in (SMMU, DSI)]
        apply(tmp)
        twice = [(tmp / rel).read_text() for rel in (SMMU, DSI)]
        if once != twice:
            raise SystemExit('Phase279 patch is not idempotent')
        joined = '\n'.join(once)
        required = [
            MARK,
            'ops->iova_to_phys(ops, iova)',
            'ops->iova_to_phys(ops, end)',
            'ARM_SMMU_CB_TTBR0', 'ARM_SMMU_CB_TCR',
            'ARM_SMMU_CB_FSYNR0', 'ARM_SMMU_CB_FAR',
            'ARM_SMMU_GR1_CBFRSYNRA(ctx->cbndx)',
            'ARM_SMMU_GR0_sGFSR', 'ARM_SMMU_GR0_sGFSYNR0',
            'ARM_SMMU_GR0_sGFSYNR1', 'ARM_SMMU_GR0_sGFSYNR2',
            'SMMU P279 I p=%u sid=%x cb=%u i=%llx l=%u e=%llx p0=%llx p1=%llx',
            'SMMU P279 T p=%u sid=%x r=%d ht=%llx ct=%llx hr=%x cr=%x',
            'SMMU P279 F p=%u sid=%x cb=%u fs=%x sy=%x far=%llx cfr=%x',
            'SMMU P279 G p=%u gf=%x g0=%x g1=%x g2=%x',
            'a52_p279_display_iova_snapshot(0, cmd_mem->offset,',
            'a52_p279_display_fault_snapshot(0);',
            'a52_p279_display_fault_snapshot(1);',
            'a52_p279_display_fault_snapshot(2);',
            'a52_p278_display_smmu_snapshot(0);',
            'a52_p278_display_smmu_snapshot(1);',
            'a52_p278_display_smmu_snapshot(2);',
        ]
        for token in required:
            if token not in joined:
                raise SystemExit('Phase279 self-test marker missing: ' + token)
        helper = joined.split('/* ' + MARK, 1)[1].split(
            'EXPORT_SYMBOL_GPL(a52_p279_display_fault_snapshot);', 1)[0]
        forbidden = [
            'ARM_SMMU_CB_ATS1PR', 'arm_smmu_iova_to_phys_hard(',
            'arm_smmu_cb_write(', 'arm_smmu_cb_writeq(',
            'arm_smmu_gr0_write(', 'arm_smmu_gr1_write(',
            'tlb_flush', 'ops->map(', 'ops->unmap(',
            'map_pages(', 'unmap_pages(',
        ]
        for token in forbidden:
            if token in helper:
                raise SystemExit('Phase279 helper contains forbidden mutator: ' + token)
        if joined.count('a52_p279_display_iova_snapshot(0, cmd_mem->offset,') != 1:
            raise SystemExit('Phase279 kickoff IOVA snapshot count wrong')
        for point in (0, 1, 2):
            if joined.count(f'a52_p279_display_fault_snapshot({point});') != 1:
                raise SystemExit(f'Phase279 fault snapshot point {point} count wrong')
    print('Phase279 broad display failure snapshot self-test: PASS')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('Phase279 broad display failure recorder applied')


if __name__ == '__main__':
    main()
