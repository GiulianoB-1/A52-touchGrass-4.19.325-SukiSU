# Phase 190 hardware result

Capture: 2026-07-31 11:46 Asia/Jerusalem

The early frozen and later raw RAMOOPS images are both 1,048,576 bytes and are byte-for-byte identical. The mirrored recorder decoded 816 records, with the final heartbeat at 61,468.096 ms.

## GPIO result

The phase-190 reservation is active:

`PINCTRL Lagoon reserved secure=13-16`

The scan completed GPIO 12 and resumed at GPIO 17. No reads were issued for GPIOs 13-16. GPIOs 86 and 87 both returned normally.

GPIO and TLMM registration completed:

- `GPIOCORE dir-scan exit`
- `GPIOCORE add success`
- `PINCTRL gpio chip-add exit rc=0`
- `PINCTRL msm probe exit rc=0`
- `PINCTRL Lagoon probe exit rc=0 bound=lagoon-pinctrl`

Phase 190 therefore fixes the GPIO 13 boot blocker.

## New hardware state

The device no longer enters the previous early crash/reset state. It remains alive for at least 61 seconds, reaches `BOOT_READY`, and records Android userspace QSEE/ION activity. The user observed an initially distorted Samsung logo, followed by a black screen after about 20 seconds, and then manually forced a restart.

There is no panic, watchdog reset, synchronous abort, or kernel hang in the capture.

## Display finding

Display platform probes completed, but none of the existing lifecycle scopes for the aggregate DRM pipeline were recorded, including:

- `msm_drm_bind`
- `msm_drm_init`
- `dsi_display_bind`
- `dsi_display_drm_bridge_init`
- `dsi_bridge_attach`
- `sde_kms_init`
- `sde_kms_prepare_commit`
- `sde_kms_commit`
- `dsi_panel_drv_init`
- `ss_panel_init`

The platform devices probed, but the DRM component master did not assemble and call its bind callback. The next phase must trace component-match construction and the component framework. It should not change panel commands, reset timing, clocks, regulators, backlight, or display modes yet.
