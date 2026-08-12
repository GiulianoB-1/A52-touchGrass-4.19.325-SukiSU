# Phase 257: KGSL publication and ueventd coldboot recorder

## Evidence that defines this phase

Phase256 hardware proves that KGSL core initialization, platform probe, character
device registration, class creation and `device_create()` all succeed. The
registered device remains present while late SurfaceFlinger opens of
`/dev/kgsl-3d0` return `-ENOENT`.

The pinned TouchGrass reference creates the KGSL class device at roughly 1.06 s,
while `ueventd` starts only around 3.03 s. TouchGrass also has devtmpfs disabled
and uses a tmpfs `/dev`. Therefore an early KGSL `KOBJ_ADD` is normal in the
working kernel: Android must later recover the already-existing device during
ueventd coldboot.

## Exact boundary under test

Phase257 answers three questions without changing behavior:

1. Did the initial `device_add()` publish `kgsl-3d0` with valid `devt` and a
   successful `KOBJ_ADD`?
2. Did userspace later write to the device's sysfs `uevent` attribute, and was
   that write an `add` coldboot replay from the expected process?
3. During initial and replayed uevents, did the generic device core produce a
   non-empty `DEVNAME` for the same major/minor?

## Recorder implementation

`drivers/base/core.c` retains:

- initial add count, return code, major/minor, `state_in_sysfs`, and timestamp;
- sysfs `uevent_store()` replay count, first/last timestamp, action-is-add,
  PID/TGID, comm, major/minor, `state_in_sysfs`, and
  `kobject_synth_uevent()` return code;
- generic `dev_uevent()` metadata count, replay metadata count, and DEVNAME.

Immediate records use `F257 add`, `F257 wr`, `F257 wrx`, and `F257 md`.
The existing Phase225 late `/dev/kgsl-3d0` open hook additionally emits retained
`F257 s1`, `F257 s2`, and `F257 s3` snapshots for the first twelve opens, so the
early coldboot state remains observable even if the first recorder sequences are
no longer in the ramoops window.

## Interpretation

- `wc=0`: ueventd coldboot never wrote this KGSL device's sysfs `uevent` file.
  The first direct divergence is coldboot enumeration/replay.
- `wc>0`, `a=1`, replay return nonzero: the kernel synthetic uevent path failed.
- `wc>0`, replay return 0, `dn=0`: generic device metadata did not produce
  DEVNAME for the replay.
- `wc>0`, replay return 0, `dn=1`, DEVNAME `kgsl-3d0`, but late open remains
  `-ENOENT`: the first divergence is downstream in userspace ueventd node
  creation/rules/security/filesystem handling. A following phase should then
  trace mknod/unlink rather than speculate.

## Guardrails

Phase257 is diagnostic only. It does not manually create `/dev/kgsl-3d0`, enable
devtmpfs or the legacy uevent helper, weaken SELinux, alter DT/ramdisk/ueventd
rules, change major/minor assignment, force success, or change KGSL/GPU/IOMMU
semantics. The mknod syscall is intentionally not instrumented yet because the
coldboot replay result is the earlier unresolved boundary.
