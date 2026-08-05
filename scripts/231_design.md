# Phase 231: exact Lagoon GPU GX legacy GDSC provider

## Hardware evidence entering this phase

Phase 230 proved that the exact `qcom,kgsl-3d0` platform device matches
`kgsl-3d`, enters `really_probe()`, and is stopped before `adreno_probe()` by
`device_links_check_suppliers()` returning `-EPROBE_DEFER`.

The persistent unresolved supplier is:

- OF node: `/soc/qcom,gdsc@3d9100c`
- platform device: `3d9100c.qcom,gdsc`
- regulator name: `gpu_gx_gdsc`
- bound driver: none

The existing A52 compatibility driver matches `qcom,gdsc`, but its explicit
profile whitelist accepts only `gcc_ufs_phy_gdsc` and `mdss_core_gdsc`.
Therefore the GPU GX device reaches that driver and is rejected with
`-ENODEV`.

## Change

Extend only `drivers/regulator/a52-legacy-gdsc-regulator.c` with one exact
Lagoon GPU GX profile.

The profile is accepted only when all of these conditions hold:

- `regulator-name` is exactly `gpu_gx_gdsc`
- the MMIO resource is exactly `0x03d9100c` with size 4
- `domain-addr` resolves through syscon
- `sw-reset` resolves through syscon
- `qcom,reset-aon-logic` is present

The enable sequence follows the downstream Qualcomm GDSC contract:

1. Pulse the GPU software-reset bit.
2. Pulse the GMEM/AON reset bit.
3. Remove the GMEM I/O clamp.
4. Clear hardware control, software override, and software collapse.
5. Poll `PWR_ON`.

Disable sets software collapse, polls power-off, then restores the GMEM I/O
clamp. The regulator core retains normal `parent-supply` ordering.

## Deliberate limits

- GPU CX is not claimed.
- No unrelated `qcom,gdsc` node is claimed.
- No supplier link is removed or ignored.
- No forced KGSL bind is performed.
- No return value in driver core is changed.
- No DT, firmware, IOMMU, or userspace file is changed.
- Phase 230 driver-core tracing and late replay remain enabled.

## Expected evidence

The Phase 230 replay should show `3d9100c.qcom,gdsc` bound to
`a52-legacy-gdsc-regulator`, supplier checking returning zero, and
`adreno_probe()` being entered. A later defer or probe error is acceptable
new evidence and must not be hidden.
