# A52xq Android 12 GKI 5.10 migration plan

## Target

Build and boot an Android Common Kernel / Generic Kernel Image based on the official Google release:

- GKI family: `android12-5.10`
- Release tag: `android12-5.10-2026-04_r1`
- Expected common-kernel commit: `f960ed27302b1ff8e61e152fc202554d778deccd`
- Expected base version: Linux `5.10.252`
- Architecture: `arm64`
- Device: Samsung Galaxy A52 5G, `a52xq`, `SM-A526B`

## Important boundary

A stock GKI image intentionally contains no Samsung- or Qualcomm-device-specific hardware implementation. The first GKI build is a reproducible reference kernel, not a flashable A52 kernel.

The A52 launched with a Samsung/Qualcomm 4.19 vendor kernel and an Android boot image header v2. It does not already have a matching 5.10 vendor-module set. Hardware support must therefore be forward-ported or rebuilt against the GKI 5.10 KMI.

## Milestones

### 0. Reproducible official GKI baseline

- Sync the official Android kernel manifest.
- Pin the common kernel to `android12-5.10-2026-04_r1`.
- Build `common/build.config.gki.aarch64`.
- Preserve `Image`, `Image.lz4`, `.config`, `vmlinux`, `System.map`, symbol and module metadata.
- Do not create an A52 flash package yet.

### 1. A52 hardware inventory

Compare the working Samsung 4.19 configuration and source against GKI 5.10. Classify each required component as:

- already present in GKI 5.10;
- available in another upstream or Qualcomm 5.10 tree;
- Samsung vendor code requiring forward-porting;
- proprietary module or firmware interface requiring replacement or adaptation.

The first boot-critical inventory is:

1. Qualcomm SM7225 platform support
2. clocks, pinctrl, RPMh, regulators and interconnect
3. SCM, SMMU/IOMMU and reserved memory
4. UFS storage and inline encryption
5. device tree and DTBO compatibility
6. console, ramoops and watchdog
7. USB device mode
8. display/KGSL only after storage and early userspace work

### 2. Device kernel architecture

Create an A52 device layer around GKI rather than merging the whole Samsung kernel into common:

- keep the GKI core close to the certified source;
- build hardware support as vendor modules where technically possible;
- keep only unavoidable early-boot support built in;
- maintain an explicit A52 KMI symbol list for external modules;
- preserve reproducible source and module revisions.

### 3. Early boot candidate

An early hardware candidate is allowed only when it includes at minimum:

- correct A52 DTB/DTBO handling;
- working interrupt controller, timers, clocks and regulators;
- UFS initialization;
- root filesystem and metadata-encryption prerequisites;
- ramoops and console diagnostics;
- the original boot image invariants or a deliberately validated new boot layout.

Success at this stage means reaching first-stage init or producing a useful persistent kernel log. Display and touchscreen are not required.

### 4. Android userspace and hardware enablement

Bring up, in this order:

1. storage and first-stage init
2. USB/ADB
3. display
4. input
5. power and charging
6. Wi-Fi/Bluetooth
7. audio
8. cameras and remaining Samsung peripherals

## Safety rules

- A plain official GKI `Image` or `boot.img` must not be flashed to the A52.
- Every A52 candidate must preserve a verified recovery path.
- Every flashable image must identify its exact GKI commit, device-module commit, config, DTB/DTBO inputs and boot-image source checksum.
- Build success is not hardware validation.
