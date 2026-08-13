# Phase 258 — Phase257 no-namei A/B

## Purpose

Phase257 proved that KGSL publication reaches userspace successfully: initial KOBJ_ADD succeeds, Android coldboot replays `add`, `DEVNAME=kgsl-3d0` is present, and ueventd's character-device `mknod(237,0)` returns 0. However both Phase257 hardware boots stop progressing before odsign/zygote/SurfaceFlinger, unlike Phase256.

Phase258 is a controlled A/B regression-isolation build. It keeps the Phase257 recorder admission/retention plus the KGSL publication/coldboot/DEVNAME instrumentation in `drivers/base/core.c` and the late publication snapshot in `fs/open.c`, while removing the new Phase257 `fs/namei.c` mknod/unlink syscall instrumentation and its s4/s5 snapshot call.

## Invariant

The Phase258 overlay itself must leave the generated `fs/namei.c` byte-for-byte unchanged from the pre-Phase257 generated source. The compiled Image must not contain the Phase257 node-syscall format strings:

- `F257 mk`
- `F257 ul`
- `F257 s4`
- `F257 s5`

The retained Phase257 publication strings must remain:

- `F257 add` / `addx`
- `F257 wr` / `wrx`
- `F257 md`
- `F257 s1` / `s2` / `s3`

No KGSL/GPU/IOMMU behavior, return value, devtmpfs setting, SELinux setting, ramdisk, ueventd rule, DT, or major/minor assignment is changed.

## Interpretation

- If Phase258 returns to the Phase256 boot progression (`odsign/odrefresh` -> zygote -> SurfaceFlinger), the Phase257 regression is isolated to the removed namei syscall instrumentation (or its corresponding late node-snapshot dependency).
- If Phase258 still stalls before odsign/zygote, the regression is not caused by the namei hook and the next A/B must split the remaining Phase257 core/open publication instrumentation.
- Only after boot progression is restored should we resume diagnosing what happens to `/dev/kgsl-3d0` at the late SurfaceFlinger open boundary.
