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
marker = "A52 P158: qsmmuv500 async TBU drvdata readiness guard"
if marker in s:
    print("A52 P158 QSMMUv500 async TBU race fix already applied")
    raise SystemExit(0)

old = """	tbu = dev_get_drvdata(dev);

	INIT_LIST_HEAD(&tbu->list);
	tbu->smmu = smmu;
	list_add(&tbu->list, &data->tbus);
	return 0;
"""
new = """	tbu = dev_get_drvdata(dev);
	/*
	 * A52 P158: qsmmuv500 async TBU drvdata readiness guard.
	 *
	 * With global asynchronous probing, dev->driver is assigned before
	 * qsmmuv500_tbu_probe() has necessarily reached dev_set_drvdata().
	 * The old code treated dev->driver != NULL as proof that the child
	 * probe had completed and unconditionally dereferenced a NULL tbu.
	 *
	 * Tell the parent SMMU probe to defer until the child has published
	 * its private data. qsmmuv500_arch_init() already converts any child
	 * registration failure into -EPROBE_DEFER.
	 */
	if (!tbu) {
		dev_dbg(dev, "A52 P158: TBU drvdata not ready, defer parent SMMU probe\\n");
		return -EPROBE_DEFER;
	}

	INIT_LIST_HEAD(&tbu->list);
	tbu->smmu = smmu;
	list_add(&tbu->list, &data->tbus);
	return 0;
"""
if s.count(old) != 1:
    raise SystemExit(f"qsmmuv500_tbu_register anchor count={s.count(old)}")

s = s.replace(old, new, 1)

for token in (
    marker,
    "static int qsmmuv500_tbu_register(struct device *dev, void *cookie)",
    "tbu = dev_get_drvdata(dev);",
    "if (!tbu) {",
    'dev_dbg(dev, "A52 P158: TBU drvdata not ready, defer parent SMMU probe',
    "return -EPROBE_DEFER;",
    "ret = device_for_each_child(dev, smmu, qsmmuv500_tbu_register);",
):
    if token not in s:
        raise SystemExit(f"P158 structural audit missing: {token}")

smmu.write_text(s)

print("A52 P158: fixed QSMMUv500 parent/child async probe race")
print("  dev->driver alone is no longer treated as proof of completed TBU probe")
print("  NULL child drvdata now cleanly returns -EPROBE_DEFER")
print("  P153 global async-default policy can remain enabled")
