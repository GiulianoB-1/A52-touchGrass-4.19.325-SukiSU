# Phase 234 - RSCC-focused recorder

## Static TouchGrass comparison

The Phase 233 generated Image and exact TouchGrass commit `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7` use the same display-RSC integration:

- DT compatible: `qcom,sde-rsc`
- platform driver name: `sde_rsc`
- Lagoon DT node: `qcom,sde_rscc`
- RSC revision: 3
- MMIO resources: `drv` and `wrapper`
- supply: `vdd` from `mdss_core_gdsc`
- clocks: `vsync_clk`, `gdsc_clk`, and `iface_clk`
- built objects: `sde_rsc.o`, `sde_rsc_hw.o`, and `sde_rsc_hw_v3.o`

The Image also contains the TouchGrass probe, bind, compatible, resource and clock strings. No direct RSC source, DT, Kconfig or Makefile parity mismatch was found.

## Why a new recorder is required

The inherited Phase 193 trace logs every platform-driver candidate tested against the `qcom,sde-rsc` device. Those failed matches are not probe progress. The latest capture therefore ended with unrelated candidate names such as `hi3660-*` and did not establish whether the real `sde_rsc` driver matched, reached `really_probe`, deferred on a supplier, entered `sde_rsc_probe`, or reached `component_add`.

The recorder capacity is 896 events, not 49. The observed sequence ending near 49 was the last recoverable/current event in that capture, not a fixed recorder-slot boundary.

## Phase 234 diagnostic change

Phase 234 preserves the Phase 210 R48 transport, three-copy RS48 protection and CRC32C record validation. It changes only recorder selection:

- persist `RSCC*` events
- persist recorder control records beginning with `BOOT ctl=`
- persist the identification record beginning with `BOOT rs=ready`
- suppress unrelated inherited boot, GPU and broad driver-core traffic
- suppress failed candidate-driver match records
- retain a nonzero match result and retain the exact `sde_rsc` candidate even if its match result is zero

The retained RSC path includes driver registration, RPMh child probe, exact match, supplier links, `really_probe`, pinctrl, DMA, sysfs, PM-domain activation, platform-bus probe, `sde_rsc_probe` stages, deferred-probe reason, `component_add`, component bind and final return codes.

## Non-goals and invariants

Phase 234 does not change display control flow, driver return values, DTB, DTBO, panel commands, clocks, regulators, GPU providers, firmware loading, SMMU behavior, ramdisk, boot cmdline or boot-image layout. The complete Phase 233 graphics/provider payload remains unchanged.
