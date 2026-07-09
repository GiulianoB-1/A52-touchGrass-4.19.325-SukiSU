# A52XQ Hybrid GKI Device Audit Results

Audit captured on 2026-07-09 from the running Samsung Galaxy A52 5G (`a52xq`).

## Active system

- Android release: 16
- Android SDK: 36
- First API level: 30
- Vendor API level: 30
- SoC platform reported by Android: `lito`
- Device-tree platform: `qcom,lagoon`
- Device overlay model: `Samsung A52XQ PROJECT (board-id,02)`
- Active kernel: `4.19.153-touchGrassKernel+`
- Architecture: AArch64, 4 KiB pages

The product-facing properties are modified by the ROM and are not reliable for hardware identity. The vendor device property, DT model and bootloader command line identify the device as A52XQ / SM-A526B.

## Boot architecture

The device uses a legacy, non-A/B Samsung boot layout:

- no active slot suffix
- no `vendor_boot` partition
- no `init_boot` partition
- separate `boot`, `recovery`, `dtbo`, `vbmeta` and `vbmeta_system` partitions
- dynamic Android logical partitions are present, but the boot chain itself is not slotted

Partition sizes observed:

| Partition | Size |
|---|---:|
| boot | 100,663,296 bytes |
| recovery | 81,788,928 bytes |
| dtbo | 8,388,608 bytes |
| vbmeta | 131,072 bytes |
| vbmeta_system | 65,536 bytes |

### Boot image header

The active boot image uses Android boot header version 2:

- page size: 4096
- kernel load address: `0x00008000`
- ramdisk load address: `0x02000000`
- DTB load address: `0x01f00000`
- header size: 1660 bytes
- compressed kernel size: 19,769,461 bytes
- compressed ramdisk size: 722,518 bytes
- embedded base DTB size: 401,068 bytes

The kernel payload is gzip-compressed. It expands to 50,845,712 bytes and has SHA-256:

```text
28651e9532dc17c1fcb74f236c3042b75d4a91602890030ae1875909555b8e97
```

That hash exactly matches the known working Linux 4.19.153 SukiSU + SUSFS kernel image. This confirms that the audit captured the active boot partition.

The existing AnyKernel3 installer replaces only the kernel and preserves the ramdisk, embedded base DTB and external DTBO partition. This is suitable for controlled hybrid-kernel experiments.

## Device-tree arrangement

The boot image contains one base DTB:

- base model: `Qualcomm Technologies, Inc. Lagoon SoC`
- base compatible: `qcom,lagoon`
- 2,424 nodes
- base UFS controller: `/soc/ufshc@1d84000`
- base UFS PHY: `/soc/ufsphy_mem@1d87000`
- GICv3: `/soc/interrupt-controller@17a00000`
- RPMh RSC: `/soc/rsc@18200000`
- TLMM: `/soc/pinctrl@f100000`

The bootloader command line selects:

```text
androidboot.dtb_idx=0
androidboot.dtbo_idx=1
```

The DTBO partition contains four entries. Entry 1 is the A52XQ board overlay and has model:

```text
Samsung A52XQ PROJECT (board-id,02)
```

Important selected-overlay changes include:

- enabling the UFS QMP PHY with legacy compatible `qcom,ufs-phy-qmp-v3`
- enabling the UFS controller and supplying board regulators
- adding the Samsung high-speed GENI UART at `0x98c000`
- adding Samsung panel, touchscreen, camera, audio, battery and peripheral definitions
- adding a persistent ramoops region

### Persistent crash log

The selected overlay defines:

```text
ramoops base: 0xB1B00000
ramoops size: 0x00100000 (1 MiB)
record size: 0x00040000
console size: 0x00040000
ftrace size: 0x00040000
pmsg size: 0x00040000
```

The working kernel enables `CONFIG_PSTORE`, `CONFIG_PSTORE_CONSOLE`, `CONFIG_PSTORE_PMSG` and `CONFIG_PSTORE_RAM`. `/sys/fs/pstore` is mounted. It was empty because the captured boot did not crash.

This gives the first experimental kernel a practical persistent logging path without requiring display, USB or physical UART.

## Kernel modules

The running audit showed no loaded modules in `/proc/modules`.

Only six optional vendor modules were present under `/vendor/lib/modules`:

- `llcc_perfmon.ko`
- `mpq-adapter.ko`
- `mpq-dmx-hw-plugin.ko`
- `rmnet_shs.ko`
- `rmnet_perf.ko`
- `rdbg.ko`

These are Linux 4.19 modules and cannot be loaded into Linux 6.1. However, none is required for the earliest CPU, timer, GIC, pstore or UFS bring-up milestone. Early hybrid builds will keep required platform drivers built into the kernel.

## Important current-kernel configuration

The running kernel confirms:

- `CONFIG_ARM64=y`
- `CONFIG_ARM64_4K_PAGES=y`
- `CONFIG_ARCH_QCOM=y`
- `CONFIG_OF=y`
- `CONFIG_SMP=y`
- `CONFIG_PREEMPT=y`
- `CONFIG_SCSI_UFS_QCOM=y`
- `CONFIG_PHY_QCOM_UFS=y`
- `CONFIG_SERIAL_MSM_GENI=y`
- `CONFIG_USB_DWC3=y`
- `CONFIG_USB_DWC3_QCOM=y`
- `CONFIG_ANDROID_BINDERFS=y`
- `CONFIG_PSTORE=y`
- `CONFIG_PSTORE_RAM=y`
- `CONFIG_MODULES=y`
- `CONFIG_MODVERSIONS=y`

## Source-base decision

Android Common Kernel 5.10 was initially considered, but its Qualcomm DT build list does not include SM6350/Lagoon. Upstream SM6350 platform support arrived after 5.10.

Linux 6.1 contains native SM6350 support, including:

- `arch/arm64/boot/dts/qcom/sm6350.dtsi`
- SM6350 GCC driver
- SM6350 TLMM/pinctrl driver
- SM6350 RPMh interconnect driver
- Qualcomm UFS and QMP PHY infrastructure

Therefore the experiment moves to Android Common Kernel 6.1. Starting with 6.1 avoids backporting an entire post-5.10 SoC platform stack into an older kernel.

This remains a hybrid port, not a drop-in GKI conversion. The Android 11-era vendor image and its Linux 4.19 modules do not satisfy a 6.1 GKI KMI. Board and SoC support will be built into the probe kernel first.

## First 6.1 probe objective

The first test kernel will:

1. retain Samsung's existing boot-header-v2 container
2. retain the current ramdisk
3. retain the current base DTB and DTBO partition
4. use an Android Common 6.1-derived ARM64 kernel
5. build SM6350 boot-critical support directly into the image
6. enable pstore/ramoops and printk timestamps
7. emit a unique probe signature
8. deliberately panic during late init
9. reboot after the panic

Success means the restored working kernel exposes a pstore console containing the unique 6.1 probe signature. It does not require Android init, UFS, USB or display.

## Safety implications

This device is non-A/B, so there is no inactive boot slot for risk-free testing. Every probe requires:

- working recovery access
- the known-working Linux 4.19.153 SukiSU + SUSFS ZIP stored externally
- a charged battery
- no automatic flashing from CI
- manual restoration of the working kernel after each probe

No experimental 6.1 package should be flashed until CI has produced and audited the image and a recovery procedure has been restated for that exact build.
