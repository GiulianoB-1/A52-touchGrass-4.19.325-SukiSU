# Phase 230: KGSL platform-match and driver-core path recorder

## Evidence entering this phase

Phase 229 proves that the live `qcom,kgsl-3d0` node is enabled, its exact
`platform_device` exists, and both KGSL platform drivers register successfully.
The GPU device nevertheless remains unbound for the full surviving trace and
`adreno_probe()` is never entered.

The exact source comparison also shows that the ported KGSL tree retains the
TouchGrass registration and OF-match contract. The remaining boundary is in
platform matching, driver/device attachment, or the pre-callback supplier gate.

## Observation scope

Phase 230 runs Phase 229 unchanged, then adds bounded `KGPPOST 230` metadata for
only the exact `qcom,kgsl-3d0` consumer and `kgsl-3d` driver pair.

It records:

- `driver_attach()` traversal for the registering Adreno driver
- `__driver_attach()` and `__device_attach_driver()` match results
- `platform_match()` route, final result, `driver_override`, platform name and
  OF-table presence
- `driver_probe_device()` entry and return
- `really_probe()` entry and every pre-callback stage
- `device_links_check_suppliers()` result and deferred-probe reason
- unresolved firmware suppliers, including supplier fwnode, device and driver
- managed device-link supplier name, OF path, driver, status and flags
- deferred-list add, retry and delete activity
- pinctrl, DMA, driver-sysfs, PM-domain and platform-probe callback boundaries

The trace is capped independently in `platform.c`, `dd.c`, and `core.c` to avoid
unbounded recorder pressure. Existing Phase 229 snapshots, Phase 228 cumulative
state, detailed `ODSPOST 226`, KGSL late-state records, and RS(255,207) with 48
parity symbols are retained.

## Important attach-path correction

A platform driver registering after its device normally enters the driver-led
path through `driver_attach()` and `__driver_attach()`. A device appearing after
the driver uses `__device_attach_driver()`. Phase 230 traces both directions so
the initial Adreno attempt cannot be missed.

## Safety and non-goals

Phase 230 does not:

- force a platform match or bind
- modify `driver_override`
- remove, relax, or synthesize supplier links
- change `fw_devlink`
- retry a probe manually
- alter a return value or deferred-probe decision
- modify DT, clocks, regulators, power domains, IOMMU, firmware or GPU power
- create `/dev/kgsl-3d0` manually
- change vold, odsign, odrefresh, SurfaceFlinger, shutdown or reboot behavior

The candidate is CI-audited and still requires hardware validation.

## Hardware-capture retention correction

The first Phase 230 hardware capture contained 1,028 CRC-valid RS(255,207)
records, but its surviving window began around 139 seconds. The driver-core
records are one-shot early-boot events and had already been overwritten.

The revised recorder journals the first 96 original `KGPPOST 230` messages in
a bounded static metadata buffer and re-emits them unchanged at heartbeat ticks
150 and 180, bracketed by `replay-begin` and `replay-end` records. Replay does
not rerun matching or probing and does not modify any driver-core state.
