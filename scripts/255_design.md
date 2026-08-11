# Phase 255: restore the post-BOOT_READY visibility corridor

## Hardware evidence entering Phase 255

Phase 254 is a major functional success. The latest hardware ramoops proves:

- the exact `qcom,kgsl-3d0` platform device binds to `kgsl-3d`
- `adreno_probe()` completes with return code 0
- the four required explicit GMU/KGSL SMMU domains obtain valid context banks
  and unique ASIDs
- GMU, HFI, bandwidth, RPMh, ringbuffer, dispatcher and DCVS initialization
  proceed
- the kernel reaches `BOOT_READY`
- the successful KGSL binding remains present in the later retained state

What Phase 254 does **not** prove is the actual Android stopping point after
`BOOT_READY`.

## Why the boundary became invisible

Phase 234 intentionally focused the finite persistent recorder on RSCC and
control records. Later GPU phases extended that admission gate with their Kxxx
diagnostic prefixes.

The older post-boot probes were retained in the source lineage, but their
messages were rejected by the Phase 234 admission gate before sequence
allocation. Therefore a lack of `BOOTPOST`, `USRPOST`, `ODSPOST`, `GFXPOST` or
`TRIPOST` in Phase 254 is not evidence that those code paths were never reached.

## Phase 255 change

Phase 255 is diagnostic only. It restores recorder admission and critical
post-capacity retention for five already-existing metadata prefixes:

- `BOOTPOST` - Android exec/exit/service milestones, including SurfaceFlinger
- `USRPOST` - the vdc/vold userspace boundary
- `ODSPOST` - odsign/odrefresh state
- `GFXPOST` - late KGSL userspace/open state
- `TRIPOST` - the Phase 228 cumulative checkpoint

`TRIPOST 228` is the key survival record. Every two heartbeat seconds it repeats
the latest state of vold, odsign/odrefresh, SurfaceFlinger and KGSL. Restoring
the four feeder prefixes is necessary so that cumulative checkpoint reflects
the current boot instead of stale zero/default state.

## Non-goals

Phase 255 does not:

- modify the Phase 254 `qcom,iommu-dma="disabled"` behavior
- modify SMMU context-bank or ASID allocation
- change KGSL, GMU, HFI, clocks, regulators, RPMh or firmware
- alter DT/DTBO or the ramdisk
- force a service to start
- modify service order, retry policy, timeout, signal, scheduling or return value
- bypass odsign, fs-verity, Binder, Keystore or any security decision
- capture application buffers, Binder payloads, keys, tokens or process memory
- change the frozen 1 MiB persistent-memory layout
- change RS(255,207), the 48 parity symbols or CRC32C

## Hardware decision

The next Phase 255 ramoops should be interpreted as a milestone ladder, not as
a new functional experiment.

Start at the already-proven Phase 254 `BOOT_READY`, then use the newest
`TRIPOST 228` plus detailed feeder records to establish the furthest reached
current-boot state. Only after that status is proven should a Phase 256
functional hypothesis be considered.
