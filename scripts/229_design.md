# Phase 229: KGSL TouchGrass comparison and platform-binding recorder

## Purpose

Phase 228 showed that the KGSL core, class and major number exist, while the
Adreno platform probe, KGSL device registration and `/dev/kgsl-3d0` creation
never appear. SurfaceFlinger consequently aborts because no suitable EGLConfig
is available.

Phase 229 narrows the missing boundary without changing graphics, service,
security or reboot behavior.

## Preserved evidence

Phase 229 retains all Phase 228 recording unchanged:

- `TRIPOST 228` cumulative vold, odsign/odrefresh, SurfaceFlinger and KGSL state
- detailed `ODSPOST 226` records after recorder capacity
- detailed `GFXPOST 225 ks1` and `GFXPOST 225 ks2` records
- RS(255,207), 48 parity symbols, CRC32C and transport-fusion decoding

## Exact TouchGrass comparison

The build compares the generated pre-Phase-229 `drivers/gpu/msm` tree against:

- repository: `micr0softstore/samsung_android_kernel_a52xq`
- commit: `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`

The artifact contains a file-by-file SHA-256 manifest and focused unified diffs
for `adreno.c`, `kgsl.c`, their key headers, Kconfig and Makefile.

## Runtime trace

The TouchGrass driver registers `kgsl_bus_platform_driver` followed by
`adreno_platform_driver`. Its OF table matches `qcom,kgsl-3d0`. Phase 229 adds
bounded metadata-only `KGPPOST 229` snapshots containing:

- live compatible-node presence
- `status`/availability result
- populated `platform_device` presence
- whether that platform device is bound to a driver
- bus-monitor and Adreno platform-driver registration return values
- Adreno probe-entry, OF-match and probe-return state

An immediate snapshot is recorded during driver registration and probe return.
A delayed read-only snapshot repeats every two seconds, capped at 110 records
(about 220 seconds), so a late surviving record retains the earlier binding
state even when the recorder ring has wrapped.

## Safety and non-goals

Phase 229 does not:

- add or modify a DT node
- force a platform match or probe
- retry or defer a probe
- change a return value
- alter power, regulator, clock, IOMMU or firmware behavior
- create `/dev/kgsl-3d0` manually
- change vold, odsign, odrefresh or SurfaceFlinger behavior
- reduce RS parity

The candidate is CI-audited and must still be hardware-validated.
