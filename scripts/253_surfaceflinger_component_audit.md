# Phase 253 boot-critical comparison through SurfaceFlinger

## Hardware boundary

Phase252 capture: `A52_RAW_RAMOOPS_20260811_162821.zip`.

The Phase252 hardware path reaches:

- GPU SMMU provider registration and GMU context-bank attaches
- GMU regulators, clocks, MMIO and IRQ acquisition
- legacy GPU and CNOC MSM-bus tables/clients
- RPMh vote construction and all recorded bus operations with rc=0
- `gmu_core_probe()` completion with rc=0

The first permanent failure is then:

```text
K248 A plat in
K248 A plat rc=-19
```

The failed call is `kgsl_device_platform_probe()`. The same `-ENODEV` state remains latched for more than 200 seconds, so it is not a deferred-probe timing issue.

## Root cause

The A52 KGSL IOMMU node enables per-process pagetables because it does not set `qcom,global_pt`.
The pinned TouchGrass `kgsl_iommu.c` therefore creates the default pagetable with this fatal sequence:

1. allocate unmanaged IOMMU domain
2. set `DOMAIN_ATTR_PROCID`
3. attach the real/global KGSL context bank
4. read `DOMAIN_ATTR_CONTEXT_BANK`
5. read `DOMAIN_ATTR_TTBR0`
6. read `DOMAIN_ATTR_CONTEXTIDR`

The Phase252 ACK ARM-SMMU port already contains the enum values but has no Qualcomm semantics for these attributes. Its unmanaged-domain default returns `-ENODEV`.

The actual Phase252 Image proves the KGSL IOMMU backend is compiled and selected before NOMMU fallback. Backend probe succeeds; the failure therefore occurs in backend MMU initialization/default pagetable creation, matching the `DOMAIN_ATTR_PROCID` failure chain exactly.

## Why the fix must include dynamic domains

Fixing only `PROCID` would move the failure forward but would not be enough for SurfaceFlinger.

KGSL creates each process pagetable by:

1. setting `DOMAIN_ATTR_DYNAMIC=1`
2. selecting the already-programmed global context bank with `DOMAIN_ATTR_CONTEXT_BANK`
3. setting the process ID with `DOMAIN_ATTR_PROCID`
4. creating a unique ASID/page table
5. attaching the dynamic domain only to finalise its software/page-table state
6. reading TTBR0 and CONTEXTIDR for GPU-side pagetable switching

TouchGrass does **not** redirect the gfx stream when a dynamic domain is attached. The live stream remains on the global KGSL context bank; per-process switching is performed by TTBR/ASID state.

Phase253 therefore ports this complete contract rather than forcing domain-attribute calls to return success.

## Stage 3: KGSL and `/dev/kgsl-3d0`

Static comparison against pinned TouchGrass shows the core later KGSL implementation is already highly converged:

- `kgsl_iommu.c` is byte-identical in the pinned Phase229 comparison
- `kgsl_mmu.c` is byte-identical
- `kgsl_reclaim.c` is byte-identical
- `kgsl_pwrscale.c` is byte-identical
- `adreno_iommu.c` is byte-identical
- `adreno_dispatch.c` is byte-identical
- `adreno_ioctl.c` is byte-identical

The current deterministic Stage-3 blocker is therefore the SMMU domain contract beneath KGSL, not a separate KGSL algorithm mismatch.

Expected progress after Phase253:

```text
K248 A plat rc=0
K248 A mem in
K248 A mem rc=0
K248 A rb rc=0
K248 A dsp rc=0
/dev/kgsl-3d0 created
```

## Stage 4: GPU start and SurfaceFlinger EGL

Pinned comparison shows these major components are already identical or have previously audited 5.10 API-only adaptations:

- A6xx core startup
- A6xx GMU path
- HFI
- preemption
- dispatcher
- ringbuffer logic

The authenticated `a615_zap` fallback is already compiled into the current lineage, and Phase252 has the required QCOM SCM/MDT support.

ION, DMA-BUF, sync-file and Binder support are enabled. Earlier hardware from this lineage already reached SurfaceFlinger userspace; the previous graphics failure was the absence of a functioning KGSL device/EGL path.

No second deterministic Stage-4 blocker was found statically.

Hardware still must prove:

- successful Adreno/GMU/HFI start after platform probe
- `/dev/kgsl-3d0` open from graphics userspace
- gralloc allocations and KGSL process pagetable creation
- EGL display/config/context initialisation
- SurfaceFlinger remains alive

## Stage 5: DRM/SDE first real commit

Earlier phases have already hardware-reached the basic display-provider corridor:

- display SMMU
- DRM master registration
- SDE/KMS registration
- DSI/panel setup
- continuous splash ownership path

The remaining unproven boundary is a real GPU-backed SurfaceFlinger composition and atomic commit.

No static missing display provider or deterministic first-commit blocker was found in the current cumulative tree. Existing display/GFX recorders should expose the first runtime divergence if one remains.

## Phase253 correction scope

Phase253 changes only the missing KGSL-required ARM-SMMU contract:

- `DOMAIN_ATTR_PROCID`
- `DOMAIN_ATTR_DYNAMIC`
- `DOMAIN_ATTR_CONTEXT_BANK`
- `DOMAIN_ATTR_TTBR0`
- `DOMAIN_ATTR_CONTEXTIDR`
- stored io-pgtable configuration for TTBR readback
- dynamic-domain ASID allocation
- dynamic-domain context-bank sharing without stream-map rewrites
- matching dynamic cleanup

It deliberately does **not**:

- fabricate IOMMU groups
- bypass `iommu_attach_device()` errors
- force attribute calls to return success without semantics
- change GPU stream IDs
- replace the entire ACK ARM-SMMU driver with the 4.19 driver
- alter DT
- alter existing display/Apps-SMMU domains which do not use KGSL PROCID/DYNAMIC semantics
- remove Phase250 SMMU power handling
- remove Phase252 MSM-bus/RPMh support

## Next hardware success criterion

The Phase253 boot is useful only if it moves beyond the Phase252 `K248 A plat rc=-19` boundary.
The desired complete milestone is:

```text
kgsl_device_platform_probe = 0
KGSL default pagetable = OK
KGSL dynamic process pagetable = OK
Adreno/GMU/HFI start = OK
/dev/kgsl-3d0 exists
SurfaceFlinger opens KGSL
EGL/gralloc init succeeds
SurfaceFlinger remains alive
first DRM/SDE atomic commit occurs
```

If a new blocker appears before SurfaceFlinger, the next comparison starts at that exact new boundary against the same TouchGrass golden trace.
