# Phase 250 GPU initialization audit

Reference: successful TouchGrass 4.19.200 ReSukiSU-safe recorder trace `TOUCHGRASS_GPU_TRACE_20260810_210342.zip`, 8192 records, all recorded return codes zero.

ACK hardware baseline: Phase249, where `3d40000.arm,smmu-kgsl` obtains one clock and `clk_bulk_prepare_enable()` returns `-EBUSY`; the later `gmu_user` attach has no IOMMU group and returns `-ENODEV`.

## Component-by-component result

### Device tree / stream IDs

TouchGrass and the active A52 topology use the same GPU SMMU/GMU/KGSL relationships. The critical context IDs remain gfx user 0, gfx secure 2, GMU user 4, GMU kernel 5. No Phase250 DT or SID change is justified.

**Action:** none.

### GPU CX/GX GDSCs

Phase249 hardware already proves the GPU CX and GX GDSC providers bind. The CX supplier exists and is the `vdd-supply` referenced by the GPU SMMU. The failure occurs after provider binding, when ARM-SMMU tries its branch clock without consuming that supply.

**Action:** do not alter GDSC provider code or force CX on.

### ARM-SMMU power resources

TouchGrass successful order at about 414 ms is:

1. power-on entry
2. optional bus request, rc=0
3. `vdd` regulator/GDSC enable, rc=0
4. clock prepare, rc=0
5. clock enable, rc=0
6. hardware configuration

The a52xq GPU SMMU has no bus-scaling table, so the downstream bus request is a successful no-op. The meaningful missing operation in ACK is the DT-declared regulator/GDSC acquisition and enable before `gcc_gpu_memnoc_gfx_clk`.

ACK Phase249 instead does:

1. `devm_clk_bulk_get_all()` -> one clock
2. `clk_bulk_prepare_enable()` -> `-EBUSY`
3. probe abort

**Action:** Phase250 adds support for `qcom,regulator-names` to ACK ARM-SMMU. It acquires the existing regulator(s), enables them before ACK's combined clock prepare/enable operation, disables them after clocks during remove/runtime suspend, and re-enables them before clocks during runtime resume. Real regulator and clock errors are preserved.

### IOMMU provider / group creation

Phase249 shows `gmu_user` has no group only because the SMMU provider probe aborted earlier. TouchGrass shows successful provider setup followed by successful GMU and KGSL attaches.

**Action:** no group fabrication, no attach bypass, no IOMMU-core return rewriting. The Phase249 diagnostics remain to prove the group appears naturally after the provider registers.

### GMU

TouchGrass records successful GMU domain allocation and both context-bank attaches. ACK reaches the same GMU allocation/attach corridor but is blocked by the missing SMMU provider/group.

**Action:** no GMU semantic change in Phase250.

### KGSL IOMMU

TouchGrass records successful `KGSLI:INIT_PT` and attach operations after the provider exists. No evidence identifies a separate KGSL-IOMMU defect before that point.

**Action:** no KGSL-IOMMU semantic change in Phase250.

### HFI

TouchGrass proceeds through HFI start, GMU init, and command traffic only after GMU/IOMMU setup succeeds. Phase249 cannot reach this stage because of the earlier SMMU failure.

**Action:** no HFI change without evidence of a post-SMMU divergence.

### Adreno / A6xx

The known source differences include required Linux 5.10 API adaptations such as CPU-latency QoS APIs, mmap locking, kernel file I/O, and VM-fault signatures. These are not evidence of GPU initialization semantic corruption. TouchGrass reaches `A6XX:INIT`, `ADRENO:START`, `GMU:START`, and HFI successfully.

**Action:** retain the 5.10 compatibility adaptations. Do not overwrite them with incompatible 4.19 APIs.

## Phase250 expected hardware sequence

The decisive new Phase250 evidence should be:

```text
K250 S gdscget rc=0 n=1
K250 S regon rc=0 n=1
K249 S clkon rc=0
K250 S clkon rc=0
K249 S cfg rc=0
K249 S reg rc=0
...
K249 I grp ok=1
...
K248 C att rc=0 n=gmu_user
```

If the first divergence moves later than this sequence, the next correction must be based on that new exact divergence rather than on a speculative change to later GPU components.
