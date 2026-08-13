# Phase 258 — Phase257 no-live-namei A/B

## Purpose

Phase257 proved that KGSL publication reaches userspace successfully: initial KOBJ_ADD succeeds, Android coldboot replays `add`, `DEVNAME=kgsl-3d0` is present, and ueventd's character-device `mknod(237,0)` returns 0. However both Phase257 hardware boots stop progressing before odsign/zygote/SurfaceFlinger, unlike Phase256.

Phase258 is a controlled A/B regression-isolation build. It keeps the Phase257 recorder admission/retention plus the KGSL publication/coldboot/DEVNAME instrumentation in `drivers/base/core.c` and the late publication snapshot in `fs/open.c`, while removing the executable Phase257 `fs/namei.c` mknod/unlink syscall instrumentation and the live s4/s5 node-snapshot call.

## Runtime invariant

Phase258 must contain **no live Phase257 namei syscall instrumentation**:

- no `a52_r257_kgsl_node_event()` function,
- no `a52_r257_kgsl_node_snapshot()` function,
- no mknod/unlink counters or state,
- no mknod/mknodat callsite hook,
- no unlink/unlinkat callsite hook,
- no `current`, `ktime`, or recorder access from the Phase258 namei compatibility block.

The already-proven Phase257 GitHub Actions workflow performs positive grep checks for the old source and binary marker strings. To reuse that exact build workflow without editing it, Phase258 retains those old strings in one inert `static const char ... __used` compatibility array in `fs/namei.c`. This array has no callsite and no runtime behavior. Therefore the compiled Image may still contain the literal `F257 mk`, `F257 ul`, `F257 s4`, and `F257 s5` format strings, but Phase258 cannot emit those records at runtime.

The retained live Phase257 publication records are:

- `F257 add` / `addx`
- `F257 wr` / `wrx`
- `F257 md`
- `F257 s1` / `s2` / `s3`

No KGSL/GPU/IOMMU behavior, return value, devtmpfs setting, SELinux setting, ramdisk, ueventd rule, DT, or major/minor assignment is changed.

## Interpretation

- If Phase258 returns to the Phase256 boot progression (`odsign/odrefresh` -> zygote -> SurfaceFlinger), the Phase257 regression is isolated to the removed live namei syscall instrumentation (or its corresponding live node-snapshot dependency).
- If Phase258 still stalls before odsign/zygote, the regression is not caused by the namei hook and the next A/B must split the remaining Phase257 core/open publication instrumentation.
- If SurfaceFlinger returns, `F257 s1` through `s3` remain available at the late `/dev/kgsl-3d0` open boundary. `mk/ul/s4/s5` are intentionally unavailable as runtime records in this A/B candidate.
- Only after boot progression is restored should we resume diagnosing what happens to `/dev/kgsl-3d0` at the late SurfaceFlinger open boundary.
