#!/usr/bin/env python3
from pathlib import Path
import argparse
import shutil
import tempfile

SMMU_C = Path('drivers/iommu/arm/arm-smmu/arm-smmu.c')
MARKER = 'A52_PHASE278_QSMMUV500_TBU_CHILDREN_PARITY_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def patch_smmu_c(text: str) -> str:
    if MARKER in text:
        return text

    include_old = '#include <linux/of_iommu.h>\n'
    include_new = include_old + '#include <linux/of_platform.h>\n'
    text = replace_one(text, include_old, include_new,
                       'Phase278 of_platform include')

    anchor = '''static inline int arm_smmu_rpm_get(struct arm_smmu_device *smmu)\n{\n'''
    helper = r'''/* A52_PHASE278_QSMMUV500_TBU_CHILDREN_PARITY_V1
 * Golden qcom,qsmmu-v500 creates its DT children, requires each TBU child to
 * bind before parent initialization completes, and associates the bound child
 * with the parent SMMU.  Keep Phase278 to that lifecycle contract only: map
 * the two resources Golden's TBU probe consumes and record stream-id-range.
 * Debug/testbus/capturebus/ECATS and TBU power-management parity are deferred.
 */
struct a52_qsmmuv500_tbu {
	struct device *dev;
	struct arm_smmu_device *smmu;
	void __iomem *base;
	void __iomem *status_reg;
	u32 sid_start;
	u32 num_sids;
};

struct a52_qsmmuv500_tbu_bind_ctx {
	struct arm_smmu_device *smmu;
	u32 bound;
};

static const struct of_device_id a52_qsmmuv500_tbu_of_match[] = {
	{ .compatible = "qcom,qsmmuv500-tbu" },
	{ },
};
MODULE_DEVICE_TABLE(of, a52_qsmmuv500_tbu_of_match);

static int a52_qsmmuv500_tbu_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct a52_qsmmuv500_tbu *tbu;
	struct resource *res;
	u32 range[2];
	int ret;

	tbu = devm_kzalloc(dev, sizeof(*tbu), GFP_KERNEL);
	if (!tbu)
		return -ENOMEM;

	tbu->dev = dev;

	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "base");
	if (!res)
		return -EINVAL;
	tbu->base = devm_ioremap_resource(dev, res);
	if (IS_ERR(tbu->base))
		return PTR_ERR(tbu->base);

	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, "status-reg");
	if (!res)
		return -EINVAL;
	tbu->status_reg = devm_ioremap_resource(dev, res);
	if (IS_ERR(tbu->status_reg))
		return PTR_ERR(tbu->status_reg);

	ret = of_property_read_u32_array(dev->of_node,
					 "qcom,stream-id-range", range, 2);
	if (ret)
		return ret;
	if (!range[1])
		return -EINVAL;

	tbu->sid_start = range[0];
	tbu->num_sids = range[1];
	platform_set_drvdata(pdev, tbu);

	a52_ackfr_record("SMMU P278 TBU probe dev=%s sid=%x count=%x",
			  dev_name(dev), tbu->sid_start, tbu->num_sids);
	return 0;
}

static struct platform_driver a52_qsmmuv500_tbu_driver = {
	.driver = {
		.name = "qsmmuv500-tbu",
		.of_match_table = a52_qsmmuv500_tbu_of_match,
		.suppress_bind_attrs = true,
	},
	.probe = a52_qsmmuv500_tbu_probe,
};

static int a52_qsmmuv500_bind_tbu_child(struct device *dev, void *cookie)
{
	struct a52_qsmmuv500_tbu_bind_ctx *ctx = cookie;
	struct a52_qsmmuv500_tbu *tbu;

	if (!dev->of_node ||
	    !of_device_is_compatible(dev->of_node, "qcom,qsmmuv500-tbu"))
		return 0;

	/* Golden treats any instantiated-but-unbound TBU as parent-defer. */
	if (!dev->driver) {
		a52_ackfr_record("SMMU P278 TBU unbound dev=%s",
				  dev_name(dev));
		return -EINVAL;
	}

	tbu = dev_get_drvdata(dev);
	if (!tbu) {
		a52_ackfr_record("SMMU P278 TBU no-drvdata dev=%s driver=%s",
				  dev_name(dev), dev->driver->name);
		return -EINVAL;
	}

	tbu->smmu = ctx->smmu;
	ctx->bound++;
	a52_ackfr_record("SMMU P278 TBU link dev=%s parent=%s sid=%x count=%x",
			  dev_name(dev), dev_name(ctx->smmu->dev),
			  tbu->sid_start, tbu->num_sids);
	return 0;
}

static int a52_qsmmuv500_populate_tbus(struct arm_smmu_device *smmu)
{
	struct a52_qsmmuv500_tbu_bind_ctx ctx = { .smmu = smmu };
	int ret;

	if (!a52_apps_smmu(smmu->dev))
		return 0;

	ret = of_platform_populate(smmu->dev->of_node, NULL, NULL, smmu->dev);
	if (ret) {
		a52_ackfr_record("SMMU P278 TBU populate parent=%s rc=%d",
				  dev_name(smmu->dev), ret);
		return ret;
	}

	ret = device_for_each_child(smmu->dev, &ctx,
				    a52_qsmmuv500_bind_tbu_child);
	if (ret) {
		a52_ackfr_record("SMMU P278 TBU defer parent=%s rc=%d",
				  dev_name(smmu->dev), ret);
		return -EPROBE_DEFER;
	}

	a52_ackfr_record("SMMU P278 TBU ready parent=%s bound=%u",
			  dev_name(smmu->dev), ctx.bound);
	return 0;
}

'''
    text = replace_one(text, anchor, helper + anchor,
                       'Phase278 TBU helper insertion')

    parent_old = '''\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-impl rc=0 impl=%d", !!smmu->impl);\n\n\tnum_irqs = 0;\n'''
    parent_new = '''\tif (trace)\n\t\ta52_ackfr_record("SMMU parent-impl rc=0 impl=%d", !!smmu->impl);\n\n\terr = a52_qsmmuv500_populate_tbus(smmu);\n\tif (trace)\n\t\ta52_ackfr_record("SMMU P278 TBU parent rc=%d", err);\n\tif (err)\n\t\treturn err;\n\n\tnum_irqs = 0;\n'''
    text = replace_one(text, parent_old, parent_new,
                       'Phase278 A52 parent TBU lifecycle')

    register_old = '''module_platform_driver(arm_smmu_driver);\n'''
    register_new = r'''static int __init a52_arm_smmu_driver_init(void)
{
	int ret;

	/* Golden ordering: TBU child driver must exist before parent probing. */
	ret = platform_driver_register(&a52_qsmmuv500_tbu_driver);
	if (ret)
		return ret;

	ret = platform_driver_register(&arm_smmu_driver);
	if (ret)
		platform_driver_unregister(&a52_qsmmuv500_tbu_driver);
	return ret;
}

static void __exit a52_arm_smmu_driver_exit(void)
{
	platform_driver_unregister(&arm_smmu_driver);
	platform_driver_unregister(&a52_qsmmuv500_tbu_driver);
}

module_init(a52_arm_smmu_driver_init);
module_exit(a52_arm_smmu_driver_exit);
'''
    text = replace_one(text, register_old, register_new,
                       'Phase278 driver registration ordering')
    return text


def apply(root: Path) -> None:
    path = root / SMMU_C
    if not path.is_file():
        raise SystemExit(f'missing reconstructed source: {path}')
    path.write_text(patch_smmu_c(path.read_text()))


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
            raise SystemExit('Phase278 patch is not idempotent')
        required = (
            MARKER,
            '#include <linux/of_platform.h>',
            '.compatible = "qcom,qsmmuv500-tbu"',
            'platform_get_resource_byname(pdev, IORESOURCE_MEM, "base")',
            'platform_get_resource_byname(pdev, IORESOURCE_MEM, "status-reg")',
            '"qcom,stream-id-range", range, 2',
            'of_platform_populate(smmu->dev->of_node, NULL, NULL, smmu->dev)',
            'device_for_each_child(smmu->dev, &ctx,',
            'return -EPROBE_DEFER;',
            'tbu->smmu = ctx->smmu;',
            'platform_driver_register(&a52_qsmmuv500_tbu_driver);',
            'platform_driver_register(&arm_smmu_driver);',
            'A52_PHASE277_QSMMUV500_DISPLAY_ACTLR_PARITY_V1',
        )
        for marker in required:
            if marker not in once:
                raise SystemExit(f'Phase278 self-test marker missing: {marker}')
        if once.index('platform_driver_register(&a52_qsmmuv500_tbu_driver);') > \
           once.index('platform_driver_register(&arm_smmu_driver);'):
            raise SystemExit('Phase278 TBU driver registration is not before parent')
    print('Phase278 QSMMUv500 TBU child lifecycle self-test: PASS')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()
    if args.self_test:
        self_test(args.root)
    else:
        apply(args.root)
        print('Phase278 QSMMUv500 TBU child lifecycle parity applied')


if __name__ == '__main__':
    main()
