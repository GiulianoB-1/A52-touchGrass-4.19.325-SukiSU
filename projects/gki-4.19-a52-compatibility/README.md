# A52 Android 4.19 GKI compatibility checkpoint

## Inputs

This checkpoint compares two real, reproducible builds:

1. Official Android Common Kernel 4.19 GKI, pinned to commit `a8bf86a0e0fa05070897a210d706d5c4d83c26ac` and built from `gki_defconfig`.
2. The reviewed touchGrass Linux 4.19.200 A52 build reconstructed by the existing checkpoint scripts, with the same ReSukiSU safe integration used by the verified build artifact.

The workflow is intentionally non-flashable.

## Initial config-only finding

A direct comparison of the already verified artifacts found:

- 4,086 parsed GKI config options
- 5,428 parsed touchGrass config options
- 2,081 device-related options with different values after excluding `CONFIG_KSU*` and `CONFIG_SUSFS*`
- 845 options enabled in touchGrass but absent or disabled in GKI
- the GKI `Image` is 24,646,144 bytes
- the touchGrass 4.19.200 `Image` is 50,837,520 bytes

The size difference is expected because the A52 kernel contains Samsung and Qualcomm platform drivers that the generic image does not contain.

## First 24 early-boot gaps

The initial comparison identifies these as the first built-in compatibility candidates:

- `CONFIG_QCOM_SCM`
- `CONFIG_QCOM_RPMH`
- `CONFIG_QCOM_SMEM`
- `CONFIG_QCOM_SMP2P`
- `CONFIG_QCOM_GLINK`
- `CONFIG_QRTR`
- `CONFIG_QCOM_COMMAND_DB`
- `CONFIG_COMMON_CLK_QCOM`
- `CONFIG_SDM_GCC_LAGOON`
- `CONFIG_QCOM_LLCC`
- `CONFIG_QCOM_LAGOON_LLCC`
- `CONFIG_QCOM_GDSC`
- `CONFIG_PINCTRL_LAGOON`
- `CONFIG_PINCTRL_QCOM_SPMI_PMIC`
- `CONFIG_REGULATOR_QCOM_RPMH`
- `CONFIG_REGULATOR_QCOM_SPMI`
- `CONFIG_QTI_IOMMU_SUPPORT`
- `CONFIG_SCSI_UFS_QCOM`
- `CONFIG_PHY_QCOM_UFS`
- `CONFIG_MMC_SDHCI_MSM`
- `CONFIG_MMC_CQHCI`
- `CONFIG_QCOM_GENI_SE`
- `CONFIG_SERIAL_MSM_GENI`
- `CONFIG_PSTORE_PMSG`

The stock GKI configuration already provides some related core functions, but several are modules instead of built-ins. For example, `CONFIG_ARM_SMMU=m` and `CONFIG_SCSI_UFSHCD=m` in GKI while the working A52 kernel uses them built in. Because this phone has the older Samsung boot layout and no prepared GKI vendor-module environment, the first hybrid boot probe must not assume those modules can load early enough.

## Workflow output

`31 - GKI 4.19 versus A52 compatibility inventory` rebuilds both sides and generates:

- complete config differences
- prioritized touchGrass-enabled options missing from GKI
- module lists
- exported-symbol differences
- CRC mismatches for symbols shared by both kernels
- touchGrass DTB and DTBO output list
- image metadata and checksums
- a Markdown compatibility report

## Custom ROM contribution

The custom ROM is useful for determining the userspace-facing side of compatibility. Its ZIP or extracted images can reveal:

- boot header version and kernel command line
- ramdisk compression and first-stage init behavior
- embedded DTB and separate DTBO requirements
- `vendor`, `vendor_dlkm`, and `system_dlkm` module inventories
- module load order and dependencies
- fstab, AVB, dm-verity, VNDK, API-level, and SELinux expectations

The ROM does not replace the touchGrass source as the hardware-driver reference. It complements it by showing exactly what the installed Android userspace expects.

## Stop condition

Do not package or flash the generic GKI output. The next flashable candidate can only be considered after a hybrid kernel reaches an observable early-boot signal through GENI serial or ramoops and its boot image passes size and layout checks.
