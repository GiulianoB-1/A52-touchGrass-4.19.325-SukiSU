#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

root = Path(sys.argv[1]).resolve()
smmu = root / "drivers/iommu/arm-smmu.c"
pon = root / "drivers/input/misc/qpnp-power-on.c"
gkeys = root / "drivers/input/keyboard/gpio_keys.c"

for p in (smmu, pon, gkeys):
    if not p.is_file():
        raise SystemExit(f"missing required source: {p}")

def replace_once(path: Path, old: str, new: str, label: str):
    s = path.read_text()
    if new in s:
        print(f"{label}: already applied")
        return
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one anchor in {path}, found {n}")
    path.write_text(s.replace(old, new, 1))
    print(f"{label}: applied")

# -------------------------------------------------------------------------
# 1) Proven P153 crash: parent ARM SMMU iterates its child TBUs while async
#    TBU probe can still have drvdata == NULL. Keep the complete pair sync.
# -------------------------------------------------------------------------
replace_once(
    smmu,
    '''static struct platform_driver arm_smmu_driver = {
	.driver	= {
		.name		= "arm-smmu",
		.of_match_table	= of_match_ptr(arm_smmu_of_match),
		.pm		= &arm_smmu_pm_ops,
		.suppress_bind_attrs = true,
	},
''',
    '''static struct platform_driver arm_smmu_driver = {
	.driver	= {
		.name		= "arm-smmu",
		.of_match_table	= of_match_ptr(arm_smmu_of_match),
		.pm		= &arm_smmu_pm_ops,
		.suppress_bind_attrs = true,
		.probe_type	= PROBE_FORCE_SYNCHRONOUS, /* A52 P154: SMMU ordering */
	},
''',
    "ARM SMMU forced synchronous",
)

replace_once(
    smmu,
    '''static struct platform_driver qsmmuv500_tbu_driver = {
	.driver	= {
		.name		= "qsmmuv500-tbu",
		.of_match_table	= of_match_ptr(qsmmuv500_tbu_of_match),
	},
''',
    '''static struct platform_driver qsmmuv500_tbu_driver = {
	.driver	= {
		.name		= "qsmmuv500-tbu",
		.of_match_table	= of_match_ptr(qsmmuv500_tbu_of_match),
		.probe_type	= PROBE_FORCE_SYNCHRONOUS, /* A52 P154: SMMU ordering */
	},
''',
    "QSMMUv500 TBU forced synchronous",
)

# -------------------------------------------------------------------------
# 2) Rescue-path providers must not depend on the global async experiment.
# -------------------------------------------------------------------------
replace_once(
    pon,
    '''static struct platform_driver qpnp_pon_driver = {
	.driver = {
		.name = "qcom,qpnp-power-on",
		.of_match_table = qpnp_pon_match_table,
	},
''',
    '''static struct platform_driver qpnp_pon_driver = {
	.driver = {
		.name = "qcom,qpnp-power-on",
		.of_match_table = qpnp_pon_match_table,
		.probe_type = PROBE_FORCE_SYNCHRONOUS, /* A52 P154: rescue key */
	},
''',
    "QPNP power-key forced synchronous",
)

replace_once(
    gkeys,
    '''static struct platform_driver gpio_keys_device_driver = {
	.probe		= gpio_keys_probe,
	.driver		= {
		.name	= "gpio-keys",
		.pm	= &gpio_keys_pm_ops,
		.of_match_table = gpio_keys_of_match,
	}
};
''',
    '''static struct platform_driver gpio_keys_device_driver = {
	.probe		= gpio_keys_probe,
	.driver		= {
		.name	= "gpio-keys",
		.pm	= &gpio_keys_pm_ops,
		.of_match_table = gpio_keys_of_match,
		.probe_type = PROBE_FORCE_SYNCHRONOUS, /* A52 P154: rescue key */
	}
};
''',
    "GPIO keys forced synchronous",
)

# gpio-keys normally reports held GPIO state only from ->open(), which may be
# much later than the five-second rescue window. Report once immediately after
# input registration so a Volume-Up key held from power-on reaches P137.
replace_once(
    gkeys,
    '''	error = input_register_device(input);
	if (error) {
		dev_err(dev, "Unable to register input device, error: %d\\n",
			error);
		return error;
	}

	sec_key = sec_device_create(ddata, "sec_key");
''',
    '''	error = input_register_device(input);
	if (error) {
		dev_err(dev, "Unable to register input device, error: %d\\n",
			error);
		return error;
	}

	/*
	 * A52 P154 rescue hardening: report already-held GPIO keys during
	 * probe. P137 listens in input_handle_event(), so Volume Up held from
	 * power-on is visible before userspace opens the input device.
	 */
	gpio_keys_report_state(ddata);

	sec_key = sec_device_create(ddata, "sec_key");
''',
    "GPIO held-state rescue report",
)

# Structural audits.
st = smmu.read_text()
pt = pon.read_text()
gt = gkeys.read_text()

if st.count("PROBE_FORCE_SYNCHRONOUS, /* A52 P154: SMMU ordering */") != 2:
    raise SystemExit("P154 SMMU forced-sync audit failed")
if pt.count("PROBE_FORCE_SYNCHRONOUS, /* A52 P154: rescue key */") != 1:
    raise SystemExit("P154 QPNP rescue forced-sync audit failed")
if gt.count("PROBE_FORCE_SYNCHRONOUS, /* A52 P154: rescue key */") != 1:
    raise SystemExit("P154 GPIO rescue forced-sync audit failed")
if gt.count("A52 P154 rescue hardening") != 1:
    raise SystemExit("P154 GPIO held-state report audit failed")

print("A52 P154 applied")
print("  forced sync: arm-smmu")
print("  forced sync: qsmmuv500-tbu")
print("  forced sync: qcom,qpnp-power-on")
print("  forced sync: gpio-keys")
print("  rescue: gpio-keys reports held state immediately at probe")
print("  all other unspecified drivers remain async under P153")
