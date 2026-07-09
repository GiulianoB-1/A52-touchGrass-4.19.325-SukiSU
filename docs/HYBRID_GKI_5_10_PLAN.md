# A52XQ Hybrid GKI 5.10 Experiment

## Purpose

This branch is an isolated research track for bringing an Android Common Kernel 5.10-derived kernel to the Samsung Galaxy A52 5G (`a52xq`, Qualcomm SM7225).

It must never replace the proven Linux 4.19.153 SukiSU + SUSFS branch until a 5.10 image reaches repeatable device boot and recovery tests.

## What "hybrid GKI" means here

The first target is not a certified drop-in Google GKI image. The first target is:

- Android Common Kernel 5.10 core
- SM7225 and A52XQ platform support integrated directly when necessary
- existing Samsung/Qualcomm drivers built in during early bring-up
- current A52XQ boot partition layout retained initially
- no requirement to split every driver into vendor modules at the start

Only after the device boots reliably should drivers be separated into GKI-style vendor modules and a stable KMI policy be considered.

## Known constraints

- Existing A52XQ and other public SM7225 Android kernels are Linux 4.19-based.
- Linux 4.19 vendor modules cannot load into Linux 5.10.
- Every boot-critical driver must be ported, replaced, or rebuilt for 5.10.
- A certified GKI image contains no SoC- or board-specific implementation.
- A52XQ may use a legacy boot image layout without `vendor_boot`; the device audit decides this.
- Modem, camera, display, audio, fingerprint, Wi-Fi and power management depend on substantial downstream Qualcomm and Samsung code.

## Safety rules

1. Keep the working 4.19.153 flashable ZIP available at all times.
2. Never test an experimental image without a verified recovery path.
3. Do not touch the working branch or release artifacts.
4. Do not add SukiSU, SUSFS, KPM or concealment features until the base 5.10 kernel can boot Android.
5. Prefer temporary boot or recovery-assisted testing where the bootloader supports it.
6. Add ramoops/pstore before the first device test.
7. Every test image must include a build ID and source commit in `uname -r`.

## Phase 0: Device architecture audit

Run `scripts/20_collect_hybrid_gki_device_audit.sh` from Termux with root.

Required outputs:

- active boot, dtbo, recovery and optional vendor_boot images
- boot header version
- active slot and partition layout
- current kernel configuration
- DT model and compatibility strings
- module locations and module load lists
- current command line and bootconfig
- pstore/ramoops availability
- boot-critical driver logs

Decision gates:

- boot header version and bootloader loading model
- whether DTB is appended to Image, stored in boot, or supplied separately
- whether vendor_boot exists
- whether essential drivers are built in or modular
- whether a practical early console or ramoops path exists

## Phase 1: Select the 5.10 base

Preferred base:

- Android Common Kernel `android12-5.10`

Additional source donors:

- current A52XQ Samsung Linux 4.19 source for board-specific behavior
- public SM7225 Linux 4.19 trees for cross-OEM Qualcomm implementation comparison
- upstream/mainline SM6350-family support where hardware blocks match
- Qualcomm downstream 5.10 code only when its provenance and compatibility can be verified

Do not blindly transplant entire 4.19 subsystems. Port one dependency chain at a time and keep upstream 5.10 interfaces where possible.

## Phase 2: First executable image

Goal: the bootloader enters the 5.10 kernel and the kernel writes a persistent crash log.

Minimum features:

- ARM64 entry and decompression
- A52XQ DTB accepted by the kernel
- interrupt controller
- architected timer
- PSCI/SMP basics
- Qualcomm clocks and regulators required for early boot
- serial or persistent ramoops logging
- panic-on-failure instrumentation

Success does not require display or Android init.

## Phase 3: Storage and ramdisk

Goal: mount the initial ramdisk and reach first-stage init.

Required areas:

- UFS controller and PHY
- SCSI/UFS glue
- block partitions
- dm-verity and device mapper requirements
- crypto requirements
- ext4/f2fs support used by the ROM
- boot image command line compatibility

Success marker:

- `/init` executes and logs are retained.

## Phase 4: USB and interactive debugging

Goal: obtain ADB or another reliable interactive channel.

Required areas:

- Qualcomm DWC3
- PHY and role switching
- configfs gadget
- Android USB functions
- charger interaction sufficient to keep the device powered

## Phase 5: Display and input

Goal: visible boot progress and touchscreen input.

Required areas:

- Qualcomm DRM/MSM display stack
- DSI controller and PHY
- Samsung panel driver
- reserved memory and IOMMU paths
- touchscreen and input regulators

## Phase 6: Android userspace compatibility

Goal: boot to the Android UI with basic stability.

Required areas:

- binder and binderfs
- Android shared memory compatibility used by the ROM
- cgroups and freezer behavior
- SELinux hooks expected by userspace
- BPF and networking requirements
- fscrypt and key management
- scheduler and power HAL expectations

## Phase 7: Remaining hardware

Port and validate separately:

- Wi-Fi and Bluetooth
- audio DSP and sound card
- modem and data interfaces
- camera and media
- fingerprint
- sensors
- charging, battery and thermal management
- suspend/resume

## Phase 8: Move toward real GKI structure

After the hybrid kernel is stable:

- identify drivers that can become loadable vendor modules
- define symbol allowlists
- move modules into an appropriate vendor ramdisk or vendor partition
- introduce KMI checking
- minimize SoC/board code in the core kernel
- evaluate compatibility with an official GKI core image

## Phase 9: Root features

Only after stable Android boot:

1. integrate SukiSU
2. validate SELinux and RCU behavior on 5.10
3. add SUSFS using the native 5.10 integration path
4. retest BPF, modules, Zygisk providers and recovery

## First milestone

The immediate milestone is not "boot Android 5.10." It is:

> Produce a complete A52XQ boot architecture report and select a technically defensible 5.10 source base with a concrete DT, UFS and logging strategy.
