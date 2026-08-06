# Phase 233: final TouchGrass graphics-provider parity

## Hardware boundary

The Phase 232 capture proves that the exact GPU GX supplier at
`3d9100c.qcom,gdsc` now binds to `a52-legacy-gdsc-regulator`. KGSL then
advances to the next direct supplier and remains deferred on
`3d9106c.qcom,gdsc`, the Lagoon `gpu_cx_gdsc` regulator.

## Full remaining-path comparison

The pinned TouchGrass A52 source was compared across the remaining UI boot
path: KGSL DT dependencies, GDSCs, GPUCC and GCC clocks, IOMMU/SMMU, nvmem,
firmware/PIL, bus scaling, MDSS/DISPCC, panel power, splash takeover and the
userspace graphics boundary.

Two predictable kernel-provider gaps remain before `adreno_probe()` can make
normal progress:

1. The existing A52 legacy GDSC bridge does not claim the exact GPU CX node at
   `0x03d9106c`.
2. The generated GKI config leaves the already-present Lagoon GPU clock
   controller disabled as `# CONFIG_GPU_CC_LAGOON is not set`, while the KGSL
   DT consumes `GPU_CC_GX_GFX3D_CLK`, `GPU_CC_CXO_CLK` and
   `GPU_CC_CX_GMU_CLK`.

The display and splash path is intentionally not replaced. Existing phases
already carry display SMMU contracts, DISPCC, MDSS GDSC, AMOLED power and
splash/DRM startup instrumentation. TouchGrass itself also keeps DRM-MSM and
FB-MSM disabled for this Samsung display stack.

## Phase 233 changes

- Add one exact `gpu_cx_gdsc` profile to the existing compatibility driver.
- Require MMIO `0x03d9106c`, size 4.
- Preserve parent-supply regulator ordering.
- Apply `qcom,clk-dis-wait-val` to GDSCR bits 15:12.
- Honor `qcom,gds-timeout` for status polling.
- Honor `qcom,no-status-check-on-disable` exactly on disable.
- Use normal software-collapse enable semantics and retain the Phase 232 GPU GX
  implementation unchanged.
- Change only `CONFIG_GPU_CC_LAGOON` from disabled to built-in before
  `olddefconfig`.

## Guardrails

- No unrelated GDSC is claimed.
- GPU CX must match the exact regulator name and exact MMIO resource.
- No device link is removed or bypassed.
- KGSL binding is not forced and return values are not altered.
- No DT, firmware, IOMMU or userspace file is modified.
- `CONFIG_DRM_MSM`, `CONFIG_FB_MSM`, panel and DISPCC parity are audited and
  must remain unchanged.
- Phase 230 late replay, Phase 232 GPU GX and all inherited recorder evidence
  remain enabled.

## Expected hardware result

The retained records should show both GPU regulators bound, the Lagoon GPUCC
provider registered, supplier checks returning zero and `adreno_probe()` being
entered. Any remaining failure should therefore be inside the real Adreno
probe or later graphics initialization, not another predictable missing direct
provider.
