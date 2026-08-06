# Phase 233: final TouchGrass graphics and UI boot-path parity

## Hardware boundary

The Phase 232 capture proves that `gpu_gx_gdsc` at `0x03d9100c` now binds.
KGSL then advances to the next direct supplier and remains deferred on
`gpu_cx_gdsc` at `0x03d9106c`.

## Completed TouchGrass comparison

The remaining graphics/UI path was compared against pinned TouchGrass commit
`6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`, covering:

- KGSL direct device links and regulator supplies
- GPU GX and GPU CX GDSC behavior
- GPUCC and GCC clock providers consumed by KGSL
- IOMMU/SMMU and LLCC dependencies
- nvmem speed and gaming bins
- GMU and A615 ZAP firmware authentication
- KGSL bus scaling dependencies
- MDSS, DISPCC, display SMMU, AMOLED panel power and splash handoff
- the `/dev/kgsl-3d0` and SurfaceFlinger boundary

The display, panel and splash implementation is already present through the
existing compatibility phases. TouchGrass also keeps `DRM_MSM` and `FB_MSM`
disabled for this Samsung display stack, so Phase 233 does not enable or replace
them.

## Predictable gaps found

1. The A52 legacy GDSC bridge does not claim the exact GPU CX node.
2. The generated kernel config disables the already-present Lagoon GPUCC
   driver, although KGSL consumes three GPUCC clocks.
3. The bridge ignores TouchGrass's `qcom,skip-disable-before-sw-enable`
   contract on GPU GX. TouchGrass reports GX disabled and makes the first
   software enable a no-op when that flag is present.
4. A6xx requests `a615_zap` through `subsystem_get()`, but
   `CONFIG_MSM_SUBSYSTEM_RESTART` is disabled. The imported TouchGrass header
   therefore compiles `subsystem_get()` as an inline function that always
   returns `NULL`. This would deterministically fail ringbuffer startup after
   KGSL finally probes.

## Phase 233 changes

### Exact GPU CX GDSC

- Claim only regulator name `gpu_cx_gdsc`.
- Require MMIO `0x03d9106c`, size 4.
- Preserve parent-supply ordering.
- Apply `qcom,clk-dis-wait-val` to GDSCR bits 15:12.
- Honor `qcom,gds-timeout`.
- Honor `qcom,no-status-check-on-disable` only on disable.

### Exact GPU GX skip-enable behavior

- Parse `qcom,skip-disable-before-sw-enable`.
- Return disabled from `is_enabled()` when the flag exists.
- Make the software enable call a recorded no-op, matching TouchGrass.
- Keep the existing reset/clamp implementation available for profiles without
  the flag and keep the normal disable behavior unchanged.

### Lagoon GPUCC

- Enable the existing `CONFIG_GPU_CC_LAGOON` driver.
- Audit the final Image for `qcom,lagoon-gpucc` and the KGSL clock names.

### Authenticated A615 ZAP loader

- Keep the legacy subsystem-restart path when it is genuinely enabled.
- Otherwise load only exact firmware `a615_zap.mdt` using PAS ID 13.
- Use the existing Qualcomm MDT parser and SCM PAS APIs:
  `qcom_mdt_get_size()`, `qcom_mdt_load()` and
  `qcom_scm_pas_auth_and_reset()`.
- Select the hidden `QCOM_MDT_LOADER` helper through Kconfig only when
  `QCOM_KGSL` is enabled.
- Retain the authenticated allocation after successful load, matching the
  lifetime of TouchGrass's unreleased `subsystem_get()` vote.

## Guardrails

- No unrelated GDSC is claimed.
- No device link is removed or bypassed.
- KGSL probe success is not forced and return codes are not rewritten.
- ZAP authentication is not bypassed and unsigned firmware is not accepted.
- Only PAS ID 13 and firmware name `a615_zap.mdt` are accepted by the fallback.
- No DT, IOMMU mapping, userspace service or panel sequence is modified.
- `CONFIG_DRM_MSM` and `CONFIG_FB_MSM` must remain disabled.
- `CONFIG_DISP_CC_LAGOON` and `CONFIG_DRM_PANEL` must remain enabled.
- Phase 230 late replay and all inherited evidence remain present.

## Expected hardware result

Both GPU GDSCs should bind, GPUCC should register, supplier checking should
return zero and `adreno_probe()` should execute. When SurfaceFlinger starts the
GPU, the capture should also contain an `A52ZAP 233 load` result. Any remaining
failure will be a real runtime Adreno, GMU, firmware, IOMMU or userspace issue,
not one of the statically predictable missing TouchGrass providers above.
