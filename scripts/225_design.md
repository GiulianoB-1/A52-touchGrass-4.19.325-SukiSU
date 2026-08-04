# Phase 225: retain and re-emit KGSL registration state

## Hardware evidence

Phase 224 reaches app_process, netd and repeated SurfaceFlinger launches.
Every `/dev/kgsl-3d0` open returns `-ENOENT`. Current-boot SurfaceFlinger
tombstones abort with `no suitable EGLConfig found, giving up`.

GKI and TouchGrass expose the same KGSL MMIO resources and IRQ names, and the
KGSL/Adreno sources are compiled into the GKI Image. The missing distinction is
whether KGSL core initialization, device registration, platform probe, later
unregister, or userspace node creation removes the device.

## Recorder change

Phase 225 stores only integer/boolean state inside `kgsl.c`:

- KGSL core seen and final return
- platform probe seen and final return
- device registration seen and final return
- `device_create` seen and final return
- unregister count
- class, major and `devp[0]` presence

At the first eight late `/dev/kgsl-3d0` open attempts, `fs/open.c` asks KGSL to
emit two compact `GFXPOST 225` records. Re-emission at SurfaceFlinger time
prevents the early initialization result from being overwritten by the recorder
ring.

## Behavior and privacy

This phase does not create a device node, force probe success, alter return
codes, bypass EGL, touch GPU registers, or change power/IOMMU behavior. It
records no buffers, addresses, application data or graphics payloads.
