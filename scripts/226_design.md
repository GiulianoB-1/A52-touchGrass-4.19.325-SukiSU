# Phase 226: retain the odsign post-fs-data gate

## Hardware evidence

The Phase 225 capture completes `vold`, `vold_prepare_subdirs`, and the first
`apexd` stage. Init executes `/system/bin/odsign` successfully at about 19.15 s,
but the capture contains no odsign exit and never reaches the later `apexd`
transition, app_process, or SurfaceFlinger. The kernel remains alive and later
Keymaster/QSEE ioctls return success.

A previous boot with the same lineage executed odsign at the same point and
reached the later `apexd` transition at about 141.44 s, followed by zygote and
SurfaceFlinger. Therefore the useful boundary is a timing-sensitive
post-fs-data/odsign gate, not a deterministic odsign failure.

## Phase 226 observation

This phase records both outcomes without bypassing odsign:

- dedicated odsign and odrefresh exec and exit records
- bounded categorized opens for odsign, ART/APEX, dalvik-cache and Binder paths
- bounded ioctl metadata for odsign and odrefresh
- bounded socket-connect metadata for odsign and odrefresh
- retained task-state snapshots at 20, 30, 45, 60, 90, 120, 150 and 180 s
- sparse heartbeats after 120 s so a successful 141 s transition remains visible
- the complete Phase 225 KGSL retained-state trace if boot reaches SurfaceFlinger

## Safety and privacy

The patch does not change odsign, odrefresh, fs-verity, Keystore, ART, APEX,
property-service, init, or KGSL return values. It does not publish completion
properties, skip verification, alter files, read buffers, capture Binder
payloads, capture signing material, or log arbitrary paths. Open records use
fixed numeric path classes only. Task snapshots contain scheduling state and
booleans, not kernel addresses.

## Decision

- odrefresh exec plus long-lived odrefresh state moves the next fix into
  odrefresh/ART compilation or its storage dependency
- repeated Binder ioctls or connects with no exit moves the next fix toward
  Keystore/property-service dependency analysis
- I/O-wait task state plus repeated ART/data path opens moves the next fix toward
  storage/fs-verity behavior
- odsign exit followed by later apexd proves the run passed this gate and keeps
  KGSL as the next active blocker
- odsign present with no child, no I/O and a stable sleeping state justifies a
  narrower wait/futex trace in the next phase
