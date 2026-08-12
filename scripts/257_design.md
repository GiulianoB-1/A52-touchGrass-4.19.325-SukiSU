# Phase 257: KGSL publication-pipeline recorder

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

## Complete boundary under test

One Phase257 hardware boot must answer the whole publication sequence without
requiring another speculative phase:

1. Did initial `device_add()` publish `kgsl-3d0` with the expected `devt`,
   `state_in_sysfs`, and successful `KOBJ_ADD`?
2. Did userspace later write to the device's sysfs `uevent` attribute, and was
   that write an `add` coldboot replay from the expected process?
3. During initial and replayed uevents, did generic `dev_uevent()` produce a
   non-empty `DEVNAME` for the same major/minor?
4. Did userspace call `mknod`/`mknodat` for basename `kgsl-3d0`, with what mode,
   major/minor, caller PID/TGID/comm, timestamp, and final return code?
5. Was `kgsl-3d0` later targeted by `unlink`/`unlinkat`, and with what caller,
   timestamp, and return code?
6. What retained state is still true when SurfaceFlinger later receives
   `-ENOENT` opening `/dev/kgsl-3d0`?

## Recorder implementation

`drivers/base/core.c` retains:

- initial add count, return code, major/minor, `state_in_sysfs`, and timestamp;
- sysfs `uevent_store()` replay count, first/last timestamp, action-is-add,
  PID/TGID, comm, major/minor, `state_in_sysfs`, and
  `kobject_synth_uevent()` return code;
- generic `dev_uevent()` metadata count, replay metadata count, and DEVNAME.

`fs/namei.c` retains only operations whose basename is exactly `kgsl-3d0`:

- final `mknod`/`mknodat` result, requested mode, decoded major/minor,
  PID/TGID/comm, count and timestamp;
- final `unlink`/`unlinkat` result, PID/TGID/comm, count and timestamp.

Immediate records use `F257 add`, `addx`, `wr`, `wrx`, `md`, `mk`, and `ul`.
The existing Phase225 late `/dev/kgsl-3d0` open hook additionally emits retained
`F257 s1` through `F257 s5` snapshots for the first twelve opens. This preserves
early coldboot and node-creation evidence even if the corresponding original
recorder sequences have already fallen out of the ramoops window.

## Interpretation matrix

- `wc=0`: ueventd coldboot never wrote this KGSL device's sysfs `uevent` file.
  First divergence is coldboot enumeration/replay.
- `wc>0`, `a=1`, replay return nonzero: kernel synthetic uevent replay failed.
- replay succeeds but `dn=0`: generic kernel uevent metadata did not provide a
  DEVNAME for the replay.
- replay succeeds, DEVNAME is `kgsl-3d0`, but `kc=0`: no userspace mknod attempt
  for this node reached the syscall path. First divergence is downstream ueventd
  device handling/rules.
- `kc>0`, `kr!=0`: userspace attempted node creation and the exact mknod return
  code identifies the rejected creation boundary.
- `kc>0`, `kr=0`, `uc>0`, `ur=0`: the node was created and later successfully
  removed.
- `kc>0`, `kr=0`, `uc=0`, but late open is still `-ENOENT`: node creation
  succeeded without a recorded removal, strongly pointing to a path or mount
  namespace discrepancy rather than KGSL registration itself.

## Guardrails

Phase257 is diagnostic only. It does not manually create `/dev/kgsl-3d0`, enable
devtmpfs or the legacy uevent helper, weaken SELinux, alter DT/ramdisk/ueventd
rules, change major/minor assignment, force success, or change KGSL/GPU/IOMMU
semantics. The node hooks only observe operations whose basename is exactly
`kgsl-3d0` and do not alter their control flow or return values.
