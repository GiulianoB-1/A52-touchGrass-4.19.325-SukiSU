# A52 P0 boot probe

This checkpoint builds the smallest practical early-boot probe from the verified touchGrass Linux 4.19.325 source while preserving the Qualcomm Lagoon and Samsung A52 platform implementation that Android Common Kernel 4.19 lacks.

## Objective

The probe is intended only to answer whether the reduced kernel can:

1. initialize the Lagoon platform;
2. initialize clocks, regulators, pinctrl and IOMMU;
3. discover the Qualcomm UFS controller;
4. access the Android boot ramdisk and storage partitions;
5. create persistent pstore/ramoops and pmsg evidence.

It is not expected to provide display, touch, camera, audio, Wi-Fi, Bluetooth or NFC functionality.

## Preserved boot contract

The packaging contract is based on the uploaded UN1CA images:

- Android boot header version 2
- 4096-byte pages
- 96 MiB boot partition
- gzip-compressed kernel
- original ramdisk preserved
- original command line preserved
- original embedded Lagoon DTB preserved
- original `dtbo.img` preserved
- DTBO index 1 preserved for A52XQ board revision 02
- Samsung `SEANDROIDENFORCE` trailer preserved during any later repack

The workflow currently builds and audits `Image.gz` only. It does not repack `boot.img`.

## Source strategy

This is a transition checkpoint, not a claim that touchGrass is GKI. It begins from the same-version, build-verified vendor tree because the full comparison proved that untouched ACK lacks `ARCH_LAGOON`, Lagoon clocks, Lagoon pinctrl and the A52 device-tree hierarchy.

Later checkpoints can replace individual vendor subsystems with ACK implementations after each interface boundary is proven.

## Safety

All artifacts are marked non-flashable. No device write commands, Odin packages, recovery ZIPs or repacked boot images are produced.
