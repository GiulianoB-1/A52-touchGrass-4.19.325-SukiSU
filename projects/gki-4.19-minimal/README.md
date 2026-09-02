# Minimal Android 4.19 GKI project for a52xq

## Goal

Start from the smallest official Android Common Kernel GKI configuration that is remotely compatible with the Samsung Galaxy A52 5G generation, then add device support only after the stock GKI kernel builds cleanly.

The known working rollback remains the existing touchGrass Linux 4.19.200 flashable package. This project does not modify or replace it.

## Why the first target is Android 4.19 GKI

The Galaxy A52 5G launched with a Samsung and Qualcomm Linux 4.19 vendor kernel. Its current layout is non-A/B, uses Android boot header version 2, embeds the base DTB in `boot.img`, and has no `vendor_boot` or `init_boot` partition.

A modern GKI 2.0 kernel such as 5.10 or 6.1 expects a different kernel and vendor-module architecture. Moving directly to it would require porting the SM7125 platform, rebuilding the complete vendor module set, and changing boot integration. That is not a simple kernel update.

Android's older GKI 1.0 work included a 4.19 GKI configuration. This makes official ACK 4.19 the correct minimal experiment, although the A52 still cannot boot the untouched generic image.

## Checkpoint 0: stock GKI build probe

Workflow:

`30 - Minimal Android 4.19 GKI probe`

Source:

- Repository: `https://android.googlesource.com/kernel/common`
- Branch: `deprecated/android-4.19-stable`
- Pinned commit: `a8bf86a0e0fa05070897a210d706d5c4d83c26ac`
- Defconfig: `gki_defconfig`
- Architecture: `arm64`

This checkpoint builds:

- `Image`
- `vmlinux`
- `System.map`
- `Module.symvers`
- the expanded and minimal GKI configurations
- all modules produced by the generic configuration
- compiler, source, checksum, and build logs

## Safety gate

The output from Checkpoint 0 is not flashable.

It intentionally contains no Samsung A52 board support, no SM7125 device tree, no Samsung boot-image packaging, no SukiSU, no SUSFS, and no AnyKernel3 package. The workflow also writes a flashing warning into the artifact.

Do not copy the generated `Image` into the existing flashable ZIP.

## Success criteria

Checkpoint 0 passes only when:

1. The pinned ACK commit is checked out exactly.
2. The official arm64 `gki_defconfig` is generated without manual device changes.
3. `Image` and modules build successfully with LLVM.
4. The workflow uploads the complete diagnostic artifact.

A clean compile proves only that the generic kernel source and toolchain are usable. It does not prove that the kernel can boot on a52xq.

## Planned checkpoints

### Checkpoint 1: compatibility inventory

Compare the stock GKI configuration and exported symbols with the known working touchGrass 4.19.200 build. Classify each A52 dependency as:

- available in GKI core
- available as a GKI module
- missing and suitable for a vendor module
- missing and required before module loading
- Samsung or Qualcomm code that must remain built into the kernel

### Checkpoint 2: early-boot platform skeleton

Add only the minimum SM7125 support required to reach early console or ramoops. Keep display, camera, audio, sensors, Wi-Fi, and root modifications out of scope.

### Checkpoint 3: boot-layout integration

Determine whether a hybrid header-v2 image can be used for development or whether a true GKI-style header-v3 and vendor ramdisk arrangement is possible with the Samsung bootloader and partition table.

### Checkpoint 4: controlled hardware bring-up

Add hardware groups one at a time and retain a rollback image for every device test.

## Stop conditions

Stop before packaging or flashing when any of the following is true:

- the stock GKI build is not reproducible
- the required early-boot drivers cannot be separated from the old vendor core
- the Samsung bootloader rejects the required boot format
- there is no reliable ramoops or recovery path
- a generated image exceeds the known boot partition limits
