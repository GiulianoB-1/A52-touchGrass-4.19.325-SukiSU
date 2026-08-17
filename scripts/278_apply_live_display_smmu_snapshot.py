#!/usr/bin/env python3
from __future__ import annotations
import argparse
import shutil
import tempfile
from pathlib import Path

SMMU = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1'
DECL = 'extern void a52_p278_display_smmu_snapshot(unsigned int point);\n'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    c = text.count(old)
    if c != 1:
        raise SystemExit(f'{label}: expected 1 match, found {c}')
    return text.replace(old, new, 1)


def patch_smmu(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1' not in text:
        raise SystemExit('Phase277 ACTLR marker missing')

    anchor = '''/* A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1\n'''
    helper = r'''/* A52_PHASE278_LIVE_DISPLAY_SMMU_SNAPSHOT_V1
 * Diagnostic-only runtime correlation for the Phase277 display ACTLR experiment.
 * Remember the exact display SME/context selected during attach, then expose a
 * read-only snapshot callable from the already-proven DSI DMA failure boundary.
 * No SMMU stream, context, ACTLR, S2CR, SMR, page-table or TBU state is changed.
 */
struct a52_p278_display_ctx {
	struct arm_smmu_device *smmu;
	u16 sid;
	s16 sme;
	u8 cbndx;
	bool valid;
};

static struct a52_p278_display_ctx a52_p278_display_ctx[2];

static void a52_p278_remember_display_context(
		struct arm_smmu_domain *smmu_domain,
		struct iommu_fwspec *fwspec, struct device *dev)
{
	struct arm_smmu_master_cfg *cfg = dev_iommu_priv_get(dev);
	struct arm_smmu_device *smmu = smmu_domain->smmu;
	int i;

	if (!cfg || !smmu || !fwspec)
		return;

	for (i = 0; i < fwspec->num_ids; i++) {
		u16 sid = FIELD_GET(ARM_SMMU_SMR_ID, fwspec->ids[i]);
		int slot, sme;

		if (sid != 0x800 && sid != 0x801)
			continue;
		slot = sid - 0x800;
		sme = cfg->smendx[i];
		if (sme == INVALID_SMENDX || sme < 0 ||
		    sme >= smmu->num_mapping_groups) {
			a52_ackfr_record("SMMU P278 R sid=%x sme=%d invalid=1",
					  sid, sme);
			continue;
		}

		a52_p278_display_ctx[slot].smmu = smmu;
		a52_p278_display_ctx[slot].sid = sid;
		a52_p278_display_ctx[slot].sme = sme;
		a52_p278_display_ctx[slot].cbndx = smmu_domain->cfg.cbndx;
		a52_p278_display_ctx[slot].valid = true;
		a52_ackfr_record("SMMU P278 R sid=%x sme=%d cb=%u",
				  sid, sme, smmu_domain->cfg.cbndx);
	}
}

void a52_p278_display_smmu_snapshot(unsigned int point)
{
	bool any = false;
	int slot;

	for (slot = 0; slot < ARRAY_SIZE(a52_p278_display_ctx); slot++) {
		struct a52_p278_display_ctx *ctx = &a52_p278_display_ctx[slot];
		struct arm_smmu_device *smmu;
		u32 actlr, sctlr, fsr, s2cr, smr, cbar;
		u32 type, stream_cb;
		int ret;

		if (!ctx->valid)
			continue;
		any = true;
		smmu = ctx->smmu;
		if (!smmu || ctx->sme < 0 ||
		    ctx->sme >= smmu->num_mapping_groups ||
		    ctx->cbndx >= smmu->num_context_banks) {
			a52_ackfr_record("SMMU P278 X p=%u sid=%x invalid=1",
					  point, ctx->sid);
			continue;
		}

		ret = arm_smmu_rpm_get(smmu);
		if (ret < 0) {
			a52_ackfr_record("SMMU P278 X p=%u sid=%x rpm=%d",
					  point, ctx->sid, ret);
			continue;
		}

		actlr = arm_smmu_cb_read(smmu, ctx->cbndx, ARM_SMMU_CB_ACTLR);
		sctlr = arm_smmu_cb_read(smmu, ctx->cbndx, ARM_SMMU_CB_SCTLR);
		fsr = arm_smmu_cb_read(smmu, ctx->cbndx, ARM_SMMU_CB_FSR);
		s2cr = arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_S2CR(ctx->sme));
		smr = smmu->smrs ?
			arm_smmu_gr0_read(smmu, ARM_SMMU_GR0_SMR(ctx->sme)) :
			0xffffffff;
		cbar = arm_smmu_gr1_read(smmu, ARM_SMMU_GR1_CBAR(ctx->cbndx));
		type = FIELD_GET(ARM_SMMU_S2CR_TYPE, s2cr);
		stream_cb = FIELD_GET(ARM_SMMU_S2CR_CBNDX, s2cr);

		a52_ackfr_record(
			"SMMU P278 C p=%u sid=%x sme=%d cb=%u a=%x sc=%x m=%u f=%x",
			point, ctx->sid, ctx->sme, ctx->cbndx, actlr, sctlr,
			!!(sctlr & ARM_SMMU_SCTLR_M), fsr);
		a52_ackfr_record(
			"SMMU P278 S p=%u s2=%x ty=%u xcb=%u smr=%x cbar=%x",
			point, s2cr, type, stream_cb, smr, cbar);

		arm_smmu_rpm_put(smmu);
	}

	if (!any)
		a52_ackfr_record("SMMU P278 X p=%u noctx=1", point);
}
EXPORT_SYMBOL_GPL(a52_p278_display_smmu_snapshot);

'''
    text = replace_one(text, anchor, helper + anchor, 'Phase278 SMMU helper insertion')

    attach_old = '''\tret = arm_smmu_domain_add_master(smmu_domain, cfg, fwspec);\n\tif (!ret)\n\t\tret = a52_arm_smmu_apply_display_actlr(smmu_domain, fwspec, dev);\n'''
    attach_new = '''\tret = arm_smmu_domain_add_master(smmu_domain, cfg, fwspec);\n\tif (!ret) {\n\t\ta52_p278_remember_display_context(smmu_domain, fwspec, dev);\n\t\tret = a52_arm_smmu_apply_display_actlr(smmu_domain, fwspec, dev);\n\t}\n'''
    text = replace_one(text, attach_old, attach_new,
                       'Phase278 remember display context after master attach')
    return text


def patch_dsi(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE276R_FINAL_DMA_ROOT_CAUSE_RECORDER_V5' not in text:
        raise SystemExit('Phase276R V5 marker missing from dsi_ctrl.c')

    deep_decl = 'extern bool a52_p276r_deep_active(void);\n'
    text = replace_one(text, deep_decl,
                       deep_decl + DECL + '/* ' + MARK + ' */\n',
                       'Phase278 DSI declaration')

    before = '''\t\t\t} else {\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H K o=%llx l=%u h=%x",\n'''
    before_new = '''\t\t\t} else {\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p278_display_smmu_snapshot(0);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H K o=%llx l=%u h=%x",\n'''
    text = replace_one(text, before, before_new, 'Phase278 pre-kickoff snapshot')

    after = '''\t\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=4 p=1");\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x",\n'''
    after_new = '''\t\t\t\tif (a52_p276r_deep_active()) a52_ackfr_record("P276 D K s=4 p=1");\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_p278_display_smmu_snapshot(1);\n\t\t\t\tif (a52_p276r_deep_active())\n\t\t\t\t\ta52_ackfr_record("P276 H R c=%x s=%x d=%x i=%x k=%x q=%x o=%x l=%x",\n'''
    text = replace_one(text, after, after_new, 'Phase278 post-kickoff snapshot')

    timeout = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n'''
    timeout_new = '''\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_p278_display_smmu_snapshot(2);\n\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n'''
    text = replace_one(text, timeout, timeout_new, 'Phase278 timeout snapshot')
    return text


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
            raise SystemExit('Phase278 patch is not idempotent')
        joined = '\n'.join(once)
        required = [
            MARK,
            'a52_p278_remember_display_context(smmu_domain, fwspec, dev);',
            'if (!ret) {\n\t\ta52_p278_remember_display_context(smmu_domain, fwspec, dev);',
            'SMMU P278 X p=%u noctx=1',
            'ARM_SMMU_CB_ACTLR', 'ARM_SMMU_CB_SCTLR', 'ARM_SMMU_CB_FSR',
            'ARM_SMMU_GR0_S2CR(ctx->sme)', 'ARM_SMMU_GR0_SMR(ctx->sme)',
            'ARM_SMMU_GR1_CBAR(ctx->cbndx)',
            'SMMU P278 C p=%u sid=%x sme=%d cb=%u a=%x sc=%x m=%u f=%x',
            'SMMU P278 S p=%u s2=%x ty=%u xcb=%u smr=%x cbar=%x',
            'a52_p278_display_smmu_snapshot(0);',
            'a52_p278_display_smmu_snapshot(1);',
            'a52_p278_display_smmu_snapshot(2);',
        ]
        for token in required:
            if token not in joined:
                raise SystemExit('Phase278 self-test marker missing: ' + token)
        if joined.count('a52_p278_display_smmu_snapshot(0);') != 1:
            raise SystemExit('Phase278 pre-kickoff snapshot count wrong')
        if joined.count('a52_p278_display_smmu_snapshot(1);') != 1:
            raise SystemExit('Phase278 post-kickoff snapshot count wrong')
        if joined.count('a52_p278_display_smmu_snapshot(2);') != 1:
            raise SystemExit('Phase278 timeout snapshot count wrong')
    print('Phase278 live display SMMU snapshot self-test: PASS')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('Phase278 live display SMMU snapshot applied')

if __name__ == '__main__':
    main()
