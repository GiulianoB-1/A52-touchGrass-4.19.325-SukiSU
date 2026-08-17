#!/usr/bin/env python3
from pathlib import Path
import argparse
import shutil
import tempfile

SMMU_C = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
MARKER = 'A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def patch_smmu_c(text: str) -> str:
    if MARKER in text:
        return text

    attach = '''static int arm_smmu_attach_dev(struct iommu_domain *domain, struct device *dev)\n{\n'''
    helper = r'''/* A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1
 * Golden qcom,qsmmu-v500 parses qcom,actlr as SID/mask/ACTLR triples,
 * programs the matching context-bank ACTLR, then immediately flushes the
 * domain TLB.  Keep this experiment display-only: SID 0x800/0x801.
 */
static int a52_arm_smmu_apply_display_actlr(
		struct arm_smmu_domain *smmu_domain,
		struct iommu_fwspec *fwspec, struct device *dev)
{
	struct arm_smmu_device *smmu = smmu_domain->smmu;
	struct device_node *np = smmu->dev->of_node;
	u32 chosen_actlr = 0, chosen_sid = 0;
	u32 before, after;
	bool has_display_sid = false, matched = false;
	int count, i, j, ret;

	if (!a52_apps_smmu(smmu->dev) || !np)
		return 0;

	for (i = 0; i < fwspec->num_ids; i++) {
		u16 sid = FIELD_GET(ARM_SMMU_SMR_ID, fwspec->ids[i]);

		if (sid == 0x800 || sid == 0x801) {
			has_display_sid = true;
			break;
		}
	}
	if (!has_display_sid)
		return 0;

	count = of_property_count_u32_elems(np, "qcom,actlr");
	if (count < 0) {
		a52_ackfr_record("SMMU P277 ACTLR missing dev=%s rc=%d",
				  dev_name(dev), count);
		return count;
	}
	if (!count || count % 3) {
		a52_ackfr_record("SMMU P277 ACTLR malformed dev=%s cells=%d",
				  dev_name(dev), count);
		return -EINVAL;
	}

	for (i = 0; i < fwspec->num_ids; i++) {
		u16 sid = FIELD_GET(ARM_SMMU_SMR_ID, fwspec->ids[i]);
		u16 mask = FIELD_GET(ARM_SMMU_SMR_MASK, fwspec->ids[i]);

		if (sid != 0x800 && sid != 0x801)
			continue;

		for (j = 0; j < count; j += 3) {
			u32 table_sid, table_mask, table_actlr;

			ret = of_property_read_u32_index(np, "qcom,actlr", j,
							 &table_sid);
			if (ret)
				return ret;
			ret = of_property_read_u32_index(np, "qcom,actlr", j + 1,
							 &table_mask);
			if (ret)
				return ret;
			ret = of_property_read_u32_index(np, "qcom,actlr", j + 2,
							 &table_actlr);
			if (ret)
				return ret;

			if (table_sid > 0xffff || table_mask > 0xffff)
				return -EINVAL;

			/* Same overlap test used by Golden's SMR ACTLR matcher. */
			if ((sid ^ table_sid) & ~((u32)mask | table_mask))
				continue;

			if (matched && chosen_actlr != table_actlr) {
				a52_ackfr_record(
					"SMMU P277 ACTLR conflict sid=%x old=%x new=%x",
					sid, chosen_actlr, table_actlr);
				return -EINVAL;
			}

			matched = true;
			chosen_actlr = table_actlr;
			chosen_sid = sid;
		}
	}

	if (!matched || !chosen_actlr) {
		a52_ackfr_record("SMMU P277 ACTLR no-match dev=%s",
				  dev_name(dev));
		return -ENOENT;
	}
	if (smmu_domain->stage != ARM_SMMU_DOMAIN_S1 ||
	    !smmu_domain->flush_ops ||
	    !smmu_domain->flush_ops->tlb_flush_all)
		return -EINVAL;

	before = arm_smmu_cb_read(smmu, smmu_domain->cfg.cbndx,
				  ARM_SMMU_CB_ACTLR);
	arm_smmu_cb_write(smmu, smmu_domain->cfg.cbndx,
			  ARM_SMMU_CB_ACTLR, chosen_actlr);
	/* Golden ordering: ACTLR write, then full context/domain TLB flush. */
	smmu_domain->flush_ops->tlb_flush_all(smmu_domain);
	after = arm_smmu_cb_read(smmu, smmu_domain->cfg.cbndx,
				 ARM_SMMU_CB_ACTLR);

	a52_ackfr_record(
		"SMMU P277 ACTLR sid=%x cb=%u actlr=%x before=%x after=%x tlb=1",
		chosen_sid, smmu_domain->cfg.cbndx, chosen_actlr, before, after);

	if (after != chosen_actlr)
		return -EIO;

	return 0;
}

'''
    text = replace_one(text, attach, helper + attach,
                       'Phase277 ACTLR helper insertion')

    old = '''\tif (a52_unported_secure_display(dev))\n\t\tret = a52_arm_smmu_attach_fault(dev, cfg, fwspec);\n\telse\n\t\tret = arm_smmu_domain_add_master(smmu_domain, cfg, fwspec);\n'''
    new = old + '''\tif (!ret)\n\t\tret = a52_arm_smmu_apply_display_actlr(smmu_domain, fwspec, dev);\n'''
    text = replace_one(text, old, new,
                       'Phase277 post-master ACTLR programming')
    return text


def apply(root: Path) -> None:
    path = root / SMMU_C
    if not path.is_file():
        raise SystemExit(f'missing reconstructed source: {path}')
    original = path.read_text()
    patched = patch_smmu_c(original)
    path.write_text(patched)


def self_test(root: Path) -> None:
    source = root / SMMU_C
    if not source.is_file():
        raise SystemExit(f'missing reconstructed source for self-test: {source}')
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        dst = tmp / SMMU_C
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dst)
        apply(tmp)
        once = dst.read_text()
        apply(tmp)
        twice = dst.read_text()
        if once != twice:
            raise SystemExit('Phase277 patch is not idempotent')
        required = (
            MARKER,
            'of_property_count_u32_elems(np, "qcom,actlr")',
            'sid != 0x800 && sid != 0x801',
            'ARM_SMMU_CB_ACTLR, chosen_actlr',
            'smmu_domain->flush_ops->tlb_flush_all(smmu_domain);',
            'SMMU P277 ACTLR sid=%x cb=%u actlr=%x before=%x after=%x tlb=1',
        )
        for marker in required:
            if marker not in once:
                raise SystemExit(f'Phase277 self-test marker missing: {marker}')
    print('Phase277 QSMMUv500 display ACTLR parity self-test: PASS')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('Phase277 QSMMUv500 display ACTLR parity applied')


if __name__ == '__main__':
    main()
