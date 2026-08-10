# Phase 249 - GPU SMMU -EBUSY / GMU -ENODEV root diagnostic

## Hardware evidence entering this phase

Phase248 capture `A52_RAW_RAMOOPS_20260810_144738.zip` decodes as one contiguous current-boot stream from sequence 1 through 876. The early-frozen and final 1 MiB raw snapshots are byte-identical (`8534732fca7ec2746bce388e3240291749ac5e20f1096eb6832d08b23d1fa049`).

The current boot proves:

- subsystem initcall level completes (`CXF246 X`, 155 calls),
- CAMCC remains fixed by the Phase247 dense `clk_hws` correction,
- GPU CX/GX GDSC providers bind,
- `3d40000.arm,smmu-kgsl` enters `arm-smmu` probe but returns `-EBUSY` and remains unbound,
- the first KGSL attempt defers on QFPROM/NVMEM,
- QFPROM later binds,
- the second KGSL attempt reaches GMU,
- `gmu_user` is populated,
- `iommu_domain_alloc()` succeeds,
- `iommu_attach_device()` returns `-ENODEV` for `gmu_user`,
- the final current record is `K248 M iommu rc=-19` at sequence 876.

This is a functional failure, not a stall inside `iommu_attach_device()`.

## Static topology

The TouchGrass GPU DT uses `gmu_user` with stream/context ID 4 and `gmu_kernel` with ID 5. The Phase247 DTB audit already confirmed the GKI candidate uses the same IDs, so Phase249 does not alter stream IDs or DT topology.

`iommu_attach_device()` can return `-ENODEV` because the device has no IOMMU group, or because a deeper provider `attach_dev()` path returns `-ENODEV`. The Phase248 record alone does not distinguish those cases.

## Phase249 question

1. Which exact internal operation in `arm_smmu_device_probe()` returns `-EBUSY` for `3d40000.arm,smmu-kgsl`?
2. When `gmu_user` calls `iommu_attach_device()`, does it have an IOMMU group? If it does, does the failure occur in `__iommu_attach_group()` / provider attach?

## K249 SMMU markers

Only the `3d40000` device compatible with `qcom,smmu-v2` is recorded:

- `K249 S ent`
- `K249 S dt rc=%d`
- `K249 S map in`
- `K249 S map rc=%d`
- `K249 S impl in`
- `K249 S impl rc=%d`
- `K249 S irqs n=%d g=%u c=%u`
- `K249 S clkget rc=%d`
- `K249 S clkon rc=%d`
- `K249 S cfg rc=%d`
- `K249 S irq in i=%d n=%d`
- `K249 S irq rc=%d i=%d`
- `K249 S sys rc=%d`
- `K249 S reg rc=%d`
- `K249 S bus in`
- `K249 S bus rc=%d`
- `K249 S exit rc=0`

The last successful marker immediately identifies the `-EBUSY` corridor. Examples:

- `map rc=-16` - resource/MMIO ownership conflict,
- `irq rc=-16` - IRQ request conflict,
- `sys rc=-16` - IOMMU sysfs registration conflict,
- `reg rc=-16` - IOMMU device registration conflict,
- `bus rc=-16` - bus/IOMMU registration conflict.

## K249 IOMMU-core markers

Only `gmu_user` is recorded:

- `K249 I ent`
- `K249 I grp ok=%d`
- `K249 I ret rc=%d s=nogrp`
- `K249 I g id=%d cnt=%d a=%d`
- `K249 I ag rc=%d`
- `K249 I ret rc=%d`

Interpretation:

- `grp ok=0` followed by `s=nogrp` directly explains the Phase248 `-ENODEV`: `gmu_user` never acquired an IOMMU group.
- `grp ok=1` followed by `ag rc=-19` moves the root cause deeper into the attached provider/domain path.

## Guardrails

Phase249 is diagnostic-only. It does not change:

- SMMU resources, IRQs, clocks, registration, or return codes,
- IOMMU group creation or attachment behavior,
- device tree, SMMU status, or stream IDs,
- Phase247 CAMCC correction,
- GPU GDSC behavior,
- `FW_DEVLINK_FLAGS_PERMISSIVE`,
- KGSL/GMU return values or dependency ordering,
- boot cmdline,
- R48/RS48/CRC32C recorder transport.
