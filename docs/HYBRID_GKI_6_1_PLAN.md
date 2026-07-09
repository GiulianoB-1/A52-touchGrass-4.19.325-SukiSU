# A52XQ Hybrid GKI 6.1 Plan

## Active target

The active experimental target is Android Common Kernel `android14-6.1` for the Samsung Galaxy A52 5G (`a52xq`, SM7225/Lagoon).

This is a hybrid bring-up, not an immediate certified GKI conversion:

- Android Common 6.1 kernel core
- SM6350/SM7225 platform support built into the image
- Samsung boot header v2 retained
- current Samsung ramdisk retained
- current embedded base DTB retained initially
- current DTBO partition retained initially
- no Linux 4.19 vendor modules loaded

The working Linux 4.19.153 SukiSU + SUSFS branch remains the recovery and daily-use kernel.

## Why 6.1 replaced 5.10

Android Common 5.10 does not contain an SM6350/Lagoon device tree or the complete SoC support chain. Native upstream SM6350 support arrived after 5.10.

Linux 6.1 already contains:

- SM6350 SoC DTSI
- SM6350 GCC
- SM6350 TLMM/pinctrl
- SM6350 RPMh interconnect
- Qualcomm UFS host support
- Qualcomm QMP UFS PHY support
- RPMh, command DB, SMEM and SCM infrastructure

Starting from 6.1 is less risky than backporting an entire later Qualcomm platform stack into 5.10.

## Confirmed device boot geometry

- non-A/B boot chain
- Android boot header version 2
- 4096-byte boot pages
- kernel address `0x8000`
- ramdisk address `0x02000000`
- DTB address `0x01f00000`
- separate DTBO partition
- selected base DTB index 0
- selected DTBO index 1
- no `vendor_boot`
- no `init_boot`

The selected A52XQ overlay provides a 1 MiB ramoops region at `0xB1B00000`.

## Milestone 1: CI source and compile probe

Workflow:

```text
.github/workflows/20-hybrid-gki-6.1-probe.yml
```

Build script:

```text
scripts/21_build_ack_6_1_probe.sh
```

The workflow:

1. clones official Android Common Kernel branch `android14-6.1`
2. records the exact source commit
3. verifies SM6350 platform source files
4. starts from ARM64 `gki_defconfig`
5. builds required Qualcomm platform drivers directly into the kernel
6. disables loadable modules
7. enables pstore and ramoops
8. inserts a deliberate late-init panic probe
9. builds `Image`, `Image.gz` and Qualcomm DTBs
10. uploads images, config, hashes and logs

This workflow does not create a flashable package.

## Milestone 2: Static image audit

Before any package is produced, verify:

- final kernel identifies as Linux 6.1
- probe marker exists in the image
- pstore and ramoops are built in
- GICv3 and ARM architectural timer are built in
- SM6350 GCC and TLMM are built in
- SM6350 interconnect is built in
- Qualcomm GENI serial is built in
- Qualcomm UFS and QMP UFS PHY are built in
- no modules are required
- image size fits the existing boot partition after gzip compression

## Milestone 3: First device probe

The first package will reuse the proven AnyKernel3 installer and replace only the kernel. It will preserve:

- current ramdisk
- current boot command line
- current embedded base DTB
- current DTBO partition

The probe kernel deliberately panics at late init with marker:

```text
A52XQ_HYBRID_GKI_6_1_PROBE_REACHED_LATE_INIT
```

It sets an eight-second panic timeout. The purpose is to prove that the kernel reached late init and that the selected A52XQ ramoops node works with Linux 6.1.

Expected first-test sequence:

1. flash probe package from recovery
2. allow deliberate panic and reboot
3. return to recovery
4. flash the known-working 4.19.153 SukiSU + SUSFS package
5. boot Android
6. read `/sys/fs/pstore`
7. confirm the probe marker

The probe is not expected to boot Android.

## Milestone 4: Remove deliberate panic

After ramoops is proven:

- remove the panic probe
- retain a unique boot marker
- identify the earliest failure after late init
- attempt execution of the existing ramdisk `/init`
- inspect pstore after each test

## Milestone 5: Storage

Adapt the current Samsung DT and 6.1 drivers for:

- legacy `qcom,ufshc` binding
- legacy `qcom,ufs-phy-qmp-v3` binding
- board regulator supplies
- SM6350 GCC clock IDs
- resets and PHY linkage
- UFS interconnect requirements

Success means UFS probes and the kernel sees the boot block device.

## Milestone 6: Android first-stage init

Required compatibility areas:

- boot header v2 ramdisk handling
- binder and binderfs
- device mapper and dm-verity
- ext4, f2fs, erofs and fscrypt
- SELinux
- cgroup behavior expected by Android 16 userspace
- firmware loading paths
- Android boot properties and command line

Success means `/init` executes and leaves logs.

## Milestone 7: USB debugging

Bring up:

- DWC3
- Qualcomm USB PHY
- configfs gadget
- FunctionFS ADB
- Type-C and charging interaction

Success means ADB becomes available from the experimental kernel.

## Milestone 8: Display and input

Bring up:

- MSM DRM/SDE
- DSI controller and PHY
- Samsung S6E3FC3 panel
- continuous splash handoff or clean modeset
- touchscreen

## Milestone 9: Remaining hardware

Validate separately:

- Wi-Fi
- Bluetooth
- modem and mobile data
- audio DSP and sound card
- camera
- fingerprint
- sensors
- charging and battery
- thermal control
- suspend and resume

## Milestone 10: GKI restructuring

Only after the hybrid image is stable:

- move suitable drivers to vendor modules
- define a KMI symbol list
- create a compatible vendor module layout
- minimize board code in the common image
- test replacement of the hybrid core with an official GKI-compatible core

## Milestone 11: Root features

Only after stable Android boot:

1. integrate SukiSU using its native 6.1 path
2. validate SELinux behavior
3. add SUSFS using a 6.1-compatible implementation
4. retest BPF and Android networking
5. retest NeoZygisk and Vector

## Safety rule

No CI artifact is flashable merely because it compiled. A package becomes testable only after its image, config, boot geometry and recovery procedure have been reviewed for that exact build.
