# Phase 232: accept the exact Lagoon GPU GX DT profile

## Hardware evidence

The Phase 231 hardware capture retained 1,028 valid RS(255,207) records over
approximately 140 seconds. At both the 150-second and 180-second Phase 230
replays:

- `qcom,kgsl-3d0` matched `kgsl-3d` and entered `really_probe()`.
- `device_links_check_suppliers()` returned `-EPROBE_DEFER` (`-517`).
- The exact supplier device `3d9100c.qcom,gdsc` existed but had no bound
  driver.
- `adreno_probe()` was not entered.

The flashed DTB describes `/soc/qcom,gdsc@3d9100c` as:

- compatible `qcom,gdsc`
- regulator name `gpu_gx_gdsc`
- MMIO `0x03d9100c`, size 4
- `sw-reset` present
- `domain-addr` present
- `parent-supply` present
- `qcom,reset-aon-logic` absent

Phase 231 incorrectly treated the absent `qcom,reset-aon-logic` property as a
fatal profile mismatch and returned `-EINVAL` before regulator registration.

## Exact TouchGrass behavior

The pinned TouchGrass Qualcomm GDSC helper performs the AON/GMEM reset pulse
only when the `AON_RESET` flag is set. AON reset is optional; it is not a
requirement for the GDSC to exist or bind.

## Phase 232 change

Phase 232 changes only the Phase 231 GPU GX profile:

- keep the exact `gpu_gx_gdsc` name guard
- keep the exact `0x03d9100c + 0x4` resource guard
- keep mandatory `sw-reset` and `domain-addr` syscons
- keep parent-regulator ordering
- make `qcom,reset-aon-logic` optional
- pulse GMEM/AON reset only when the property is present
- retain clamp removal, software-collapse control, and `PWR_ON` polling
- emit the retained marker `A52GDSC GPU_GX_PROFILE_V2 ... aon=<0|1>`

## Non-goals and safety limits

Phase 232 does not:

- claim `gpu_cx_gdsc`
- claim unrelated `qcom,gdsc` nodes
- bypass `device_links_check_suppliers()`
- force KGSL binding
- alter driver-core return values
- change the device tree, firmware, IOMMU, clocks, or userspace
- remove Phase 230 driver-core tracing and late replay

## Expected next hardware result

The Phase 230 late replay should show `3d9100c.qcom,gdsc` bound to
`a52-legacy-gdsc-regulator`. KGSL should then either enter `adreno_probe()` or
identify the next supplier or in-probe blocker precisely.
