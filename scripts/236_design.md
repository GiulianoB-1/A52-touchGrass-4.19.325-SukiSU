# Phase 236 - MSM DRM display-init recorder

## Question

Phase 235 hardware capture contained only the Phase 235 R48 identity and
`BOOT_BEGIN`; no `DRMCOMP`, bounded `COMP`, or `RSCC` records were persisted.
Phase 236 moves the observation boundary earlier:

1. How far through the existing kernel initcall milestones does this boot reach?
2. Does `msm_drm_register()` execute?
3. Do the SMMU/DSI/eDP/HDMI registration checkpoints complete?
4. Does `platform_driver_register(&msm_platform_driver)` return success?
5. Does `msm_pdev_probe()` execute for the SDE/MDSS platform device?
6. If probe executes, the inherited Phase 235 `DRMCOMP`/`COMP`/`RSCC` records
   remain available to diagnose component-master assembly.

## Recorder transport

Unchanged from Phase 210 and Phase 235:

- ASCII prefix: `R48`
- shortened RS(255,207), 48 parity bytes
- CRC32C mandatory
- three persistent copies: record, console, ftrace
- transport block: 255 bytes
- data bytes: 141

## Persisted classes

Phase 236 admits only:

- `DISPINIT*` - MSM DRM registration and platform probe checkpoints
- `BOOT phase=*` - already-existing bounded initcall milestones
- `BOOT ctl=*`
- `BOOT rs=ready*`
- `RSCC*`
- `DRMCOMP*`
- bounded display-scoped `COMP *`

The generic `RSCCCORE` candidate-driver flood remains suppressed.

## New instrumentation

`drivers/a52_display/msm/msm_drv.c`:

- `DISPINIT register enter modeset=%u`
- `DISPINIT register disabled rc=%d`
- `DISPINIT smmu-register done`
- `DISPINIT dsi-register done`
- `DISPINIT edp-register done`
- `DISPINIT hdmi-register done`
- `DISPINIT platform-register enter`
- `DISPINIT platform-register exit rc=%d`
- `DISPINIT probe enter dev=%s sde=%u mdss=%u`

The recorder API buffers accepted events before the RS backend is ready and
flushes missing buffered events when persistence becomes available, so an early
`DISPINIT` call is still recoverable.

## Interpretation

- No `BOOT phase=device` and no `DISPINIT register enter`: failure is before the
  normal device-initcall stage. Move earlier in boot/initcall ordering.
- `DISPINIT register enter` but no later checkpoint: the last registration
  checkpoint identifies the boundary.
- `DISPINIT platform-register exit rc!=0`: MSM DRM platform-driver registration
  itself failed.
- platform register returns 0 but no `DISPINIT probe enter`: driver registered,
  but no matching platform device reached this probe path. Next target is the
  targeted `msm_drm` device/driver match boundary and DT availability.
- `DISPINIT probe enter` appears: continue directly into inherited `DRMCOMP`,
  `COMP`, and `RSCC` records to identify the component-master state.

Phase 236 changes recorder/instrumentation behavior only. It does not change
MSM DRM return values, DT contents, component topology, provider behavior, or
graphics policy.
