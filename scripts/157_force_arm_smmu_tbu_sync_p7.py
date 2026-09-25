#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
smmu = kernel / "drivers/iommu/arm-smmu.c"
if not smmu.is_file():
    raise SystemExit(f"missing required source: {smmu}")

s = smmu.read_text()
marker = "A52 P157"
if marker in s:
    print("A52 P157 ARM-SMMU/TBU synchronous exception already applied")
    raise SystemExit(0)

def replace_once(old: str, new: str, label: str) -> None:
    global s
    count = s.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    s = s.replace(old, new, 1)

replace_once(
    """static struct platform_driver arm_smmu_driver = {
	.driver	= {
		.name		= "arm-smmu",
		.of_match_table	= of_match_ptr(arm_smmu_of_match),
		.pm		= &arm_smmu_pm_ops,
		.suppress_bind_attrs = true,
	},
""",
    """static struct platform_driver arm_smmu_driver = {
	.driver	= {
		.name		= "arm-smmu",
		.of_match_table	= of_match_ptr(arm_smmu_of_match),
		.pm		= &arm_smmu_pm_ops,
		.probe_type	= PROBE_FORCE_SYNCHRONOUS, /* A52 P157 */
		.suppress_bind_attrs = true,
	},
""",
    "arm-smmu synchronous provider",
)

replace_once(
    """static struct platform_driver qsmmuv500_tbu_driver = {
	.driver	= {
		.name		= "qsmmuv500-tbu",
		.of_match_table	= of_match_ptr(qsmmuv500_tbu_of_match),
	},
""",
    """static struct platform_driver qsmmuv500_tbu_driver = {
	.driver	= {
		.name		= "qsmmuv500-tbu",
		.of_match_table	= of_match_ptr(qsmmuv500_tbu_of_match),
		.probe_type	= PROBE_FORCE_SYNCHRONOUS, /* A52 P157 */
	},
""",
    "qsmmuv500 TBU synchronous child",
)

for token in (
    'name\t\t= "arm-smmu"',
    'name\t\t= "qsmmuv500-tbu"',
    "PROBE_FORCE_SYNCHRONOUS, /* A52 P157 */",
    "device_for_each_child(dev, smmu, qsmmuv500_tbu_register)",
    "dev_get_drvdata(dev)",
):
    if token not in s:
        raise SystemExit(f"P157 structural audit missing: {token}")

if s.count("PROBE_FORCE_SYNCHRONOUS, /* A52 P157 */") != 2:
    raise SystemExit("P157 expected exactly two synchronous exceptions")

smmu.write_text(s)

print("A52 P157: ARM-SMMU/TBU ordering exception applied")
print("  synchronous provider: arm-smmu")
print("  synchronous child: qsmmuv500-tbu")
print("  reason: parent immediately consumes TBU drvdata after of_platform_populate()")
print("  P153 global async-default remains active for all other ordinary drivers")
