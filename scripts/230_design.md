# Phase 230: KGSL driver-core and supplier-boundary recorder

## Evidence entering this phase

Phase 229 hardware data reported the same state from the first surviving GPU
snapshot through shutdown:

- the live `qcom,kgsl-3d0` node exists
- the node is enabled
- its `platform_device` exists
- the device is not bound
- both KGSL platform-driver registrations return zero
- `adreno_probe()` is never entered

Therefore Phase 230 observes the driver-core path between successful driver
registration and the Adreno callback. It does not attempt to force a bind.

## TouchGrass comparison

The inherited build compares the generated Phase 229 source with:

- repository: `micr0softstore/samsung_android_kernel_a52xq`
- commit: `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`

The artifact includes:

- every DTS/DTSI node containing `qcom,kgsl-3d0`
- the direct properties and phandle-reference set for those nodes
- focused diffs for `platform_match()`, `__device_attach_driver()`,
  `__driver_attach()`, `driver_probe_device()`, `really_probe()` and
  `device_links_check_suppliers()`

This extends the Phase 229 comparison beyond `drivers/gpu/msm` into the DT and
driver-core code that can prevent the callback from running.

## Runtime trace

`KGBPOST 230` is retained after recorder capacity and is emitted only for the
GPU consumer and the Adreno driver identified by its OF table. Each checkpoint
is one-shot, so repeated deferred-probe attempts cannot flood RAMOOPS.

The records distinguish:

- final platform-match result, direct OF result, `driver_override` presence and
  ID-table presence
- driver-centric versus device-centric attach traversal
- entry and return from `driver_probe_device()`
- entry to `really_probe()` and global probe deferral state
- firmware-node supplier waiting
- the first blocking managed supplier device, link state, flags and supplier
  state
- supplier-check result and deferred-probe reason
- pinctrl, DMA configuration, driver sysfs and PM-domain activation results
- entry and return of the platform bus probe callback

The existing Phase 229 delayed `KGPPOST 229` snapshots remain active through
approximately 220 seconds and show whether the device eventually binds.

## Preserved evidence

Phase 230 retains unchanged:

- `KGPPOST 229`
- `TRIPOST 228` including vold, odsign/odrefresh, SurfaceFlinger and KGSL state
- detailed `ODSPOST 226`
- `GFXPOST 225 ks1` and `GFXPOST 225 ks2`
- RS(255,207), 48 parity symbols, CRC32C and transport-fusion decoding

## Safety and non-goals

Phase 230 does not:

- modify DT data or a device/driver name
- change `platform_match()` or supplier-check return values
- clear `driver_override`
- remove, add or relax a device link
- force, retry or manually invoke `adreno_probe()`
- change asynchronous-probe selection
- alter pinctrl, DMA, IOMMU, clocks, regulators, power domains or firmware
- create `/dev/kgsl-3d0`
- change vold, odsign, odrefresh, SurfaceFlinger or reboot behavior
- reduce Reed-Solomon parity

The candidate is CI-audited and must still be hardware-validated.
