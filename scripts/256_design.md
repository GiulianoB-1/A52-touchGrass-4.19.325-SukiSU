# Phase 256 — KGSL devnode + framework milestones

Phase255 hardware reached SurfaceFlinger three times, but each attempt got `-ENOENT` opening `/dev/kgsl-3d0` while KGSL core/probe/register/device_create all reported success and no unregister occurred.

Phase256 makes two evidence-based functional parity corrections against TouchGrass commit `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`:

1. Restore `CONFIG_TMPFS_XATTR=y` and `CONFIG_TMPFS_POSIX_ACL=y`, matching the golden tmpfs `/dev` metadata/labeling prerequisites used by Android ueventd.
2. Restore the downstream KGSL Kconfig/devfreq contract: `QCOM_KGSL`, `QCOM_KGSL_IOMMU`, Adreno TZ governor, GPUBW governor and default governor `msm-adreno-tz`. Existing proven 5.10 KGSL adaptations are retained; only missing devfreq governor sources are imported from the pinned golden tree.

Phase256 does **not** enable devtmpfs or the legacy uevent helper, does not manually create `/dev/kgsl-3d0`, does not weaken SELinux, and does not change DT or ramdisk.

The retained `F256` recorder stream adds:
- `F256 da`: `kgsl-3d0` device-add dev_t
- `F256 ue`: kernel KOBJ_ADD/uevent return
- `F256 rn`: task rename milestones for zygote, zygote64, system_server, SystemUI, Samsung launcher, bootanimation and SurfaceFlinger
- `F256 ex`: exits for the same framework processes

This makes the next hardware capture able to distinguish successful KGSL node exposure from later zygote/system_server/SystemUI/launcher blockers without another blind diagnostic cycle.
