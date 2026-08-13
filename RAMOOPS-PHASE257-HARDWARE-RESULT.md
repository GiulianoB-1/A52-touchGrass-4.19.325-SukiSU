# Phase 257 hardware result

Capture: `A52_RAW_RAMOOPS_20260813_110929.zip`

Decoder contract: Phase 210+ R48 ASCII transport with record/ftrace transport fusion, shortened RS(255,207) with 48 parity symbols, mandatory CRC32C. The obsolete recorder-v3 decoder must not be used.

## Recovered Phase 257 evidence

```text
  1449.338 ms  F257 add  n=1 M=237 m=0 s=1
  1449.451 ms  F257 md   r=0 dn=kgsl-3d0 M=237 m=0
  1449.667 ms  F257 addx n=1 rc=0 dn=1 mc=1
 10167.698 ms  F257 wr   n=1 a=add p=224 g=224 M=237 m=0 s=1
 10167.953 ms  F257 md   r=1 dn=kgsl-3d0 M=237 m=0
 10168.212 ms  F257 wrx  n=1 rc=0 dn=1 mc=1
 11062.139 ms  F257 mk   n=1 rc=0 p=272 g=272 mo=20666 M=237 m=0 c=ueventd
```

## Interpretation

The KGSL publication pipeline succeeds through userspace device-node creation:

1. The initial KGSL device is present in sysfs and uses major/minor 237:0.
2. Generic `dev_uevent()` supplies `DEVNAME=kgsl-3d0`.
3. The initial KOBJ_ADD succeeds.
4. Android `ueventd` later performs an `add` coldboot replay for the same device.
5. The synthetic replay succeeds and again supplies `DEVNAME=kgsl-3d0`.
6. A ueventd worker calls mknod for the KGSL character device and the syscall returns 0 with major/minor 237:0 and mode 020666.

Therefore the first divergence is no longer KGSL registration, KOBJ_ADD, coldboot replay, DEVNAME generation, or the mknod syscall itself.

## Why this capture is not yet sufficient for the final Phase 257 decision

The recovered Phase 257 timeline ends at approximately 130.620 seconds.

The immediately previous Phase 256 hardware capture follows the same periodic boot milestones at 130.620 seconds, but its first SurfaceFlinger launch occurs only at approximately 156.879 seconds. Phase 225 then records `/dev/kgsl-3d0` open failures shortly afterward.

Therefore this Phase 257 capture ended before SurfaceFlinger reached the late-open hook. The retained `F257 s1` through `F257 s5` snapshots were not emitted, so the test cannot yet distinguish between:

- node remains present and the later open succeeds,
- node was removed later,
- `/dev` was replaced or overmounted,
- mount/path namespace identity differs at the later opener.

## Required next hardware test

Do not patch the kernel yet. Reflash or keep the exact successful Phase 257 Run 13 boot image and repeat the hardware boot. Let the failing Android boot run for at least 190 seconds from kernel start, or until it reboots by itself if that happens first, then enter recovery and collect the same raw ramoops package immediately.

The next capture should contain the first SurfaceFlinger launches plus the retained `F257 s1` through `F257 s5` records. Those records are the evidence required before selecting the next patch.
