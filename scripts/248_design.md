# Phase 248 - KGSL / GMU / IOMMU probe corridor

## Hardware starting point

Fresh Phase247 capture: `A52_RAW_RAMOOPS_20260810_121801.zip`, decoded with the Phase210+ R48/RS48 transport-fusion decoder.

Current contiguous boot evidence reaches sequence 855. Phase247 clears the previous blockers:

- CAMCC returns and the full traced subsys initcall level completes (`CXF246 X ... n=155`).
- GPU GX and GPU CX GDSCs both bind in the current boot.
- First KGSL probe enters `adreno_probe()` and returns `-EPROBE_DEFER` before QFPROM is bound.
- QFPROM then binds successfully.
- Deferred KGSL retry passes match, supplier gate, pinctrl, DMA configure and sysfs, then re-enters `adreno_probe()` and does not return before the current stream ends.

The first defer is consistent with TouchGrass `adreno_probe_efuse()` -> `adreno_read_speed_bin()` -> `nvmem_cell_get(..., "speed_bin")` waiting for QFPROM/NVMEM.

## Additional static evidence

The generic GPU SMMU device `3d40000.arm,smmu-kgsl` reaches the `arm-smmu` probe callback but returns `-EBUSY` and remains unbound. This is suspicious because GMU user/kernel context banks attach through the same SMMU provider, but it is not yet sufficient evidence for a functional SMMU or DT change.

The Phase247 DTB was re-parsed directly. Its stream IDs match pinned TouchGrass exactly:

- gfx3d user: 0
- gfx3d secure: 2
- GMU user: 4
- GMU kernel: 5

Therefore Phase248 does not change DT or SMMU topology.

## Phase248 change

Diagnostic only. Add critical `K248` records around:

1. `adreno_probe_efuse()`
2. `adreno_identify_gpu()`
3. `adreno_of_get_power()`
4. `gmu_core_probe()`
5. GMU core `ops->probe()`
6. `gmu_probe()` regulator, clock and IOMMU setup
7. `gmu_iommu_init()` population and each context bank
8. `iommu_domain_alloc()` and `iommu_attach_device()`
9. `kgsl_device_platform_probe()`
10. later memstore/ringbuffer/dispatcher boundaries if reached

## Guardrails

Phase248 changes no return value, probe ordering, regulator/power vote, IOMMU mapping or attach operation, DT property, `fw_devlink` setting, CAMCC behavior, GDSC behavior, or physical R48/RS48 recorder transport.

## Decisive interpretation

Examples:

- Last record `K248 A gmu in` - stop before/inside GMU core dispatch.
- `K248 G ops in` but no `ops rc` - inside GMU implementation probe.
- `K248 M iommu in` - inside GMU IOMMU setup.
- `K248 C dom ... ok=1` then `K248 C att in ...` with no attach result - `iommu_attach_device()` is the immediate blocker.
- Both GMU context banks return and `K248 A gmu rc=0`, then `K248 A plat in` with no return - blocker moves to `kgsl_device_platform_probe()`.
