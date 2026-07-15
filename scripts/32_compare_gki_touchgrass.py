#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path

ROOT_ONLY_PREFIXES = ("CONFIG_KSU", "CONFIG_SUSFS")

CATEGORY_PATTERNS = {
    "boot-diagnostics": ("PSTORE", "RAMOOPS", "SERIAL", "PRINTK", "CMDLINE", "DEVTMPFS", "BLK_DEV_INITRD"),
    "qcom-core": ("ARCH_QCOM", "QCOM_", "MSM_", "QRTR", "GLINK", "SMP2P", "SMEM", "SPMI", "PMIC", "RPMH"),
    "clock-power": ("COMMON_CLK", "SDM_GCC", "SDM_GPUCC", "SDM_DISPCC", "SDM_CAMCC", "REGULATOR", "GDSC", "CPU_FREQ", "DEVFREQ", "THERMAL"),
    "storage": ("SCSI_UFS", "UFS", "MMC", "SDHCI", "EXT4", "F2FS", "EROFS", "DM_VERITY", "FS_ENCRYPTION", "FS_VERITY"),
    "iommu-memory": ("IOMMU", "SMMU", "ION", "DMA_HEAP", "CMA", "INTERCONNECT"),
    "android-security": ("ANDROID", "BINDER", "ASHMEM", "SELINUX", "SECURITY", "SECCOMP", "AUDIT", "CGROUP", "BPF"),
    "usb": ("USB", "DWC3", "TYPEC"),
    "display-gpu": ("DRM", "FB_", "MDSS", "MIPI", "DSI", "KGSL", "ADRENO", "GPU"),
    "input-touch": ("INPUT", "TOUCHSCREEN", "HID", "HALL"),
    "audio": ("SND", "SOUND", "AUDIO", "WCD", "SLIMBUS"),
    "camera-media": ("CAMERA", "SPECTRA", "MEDIA", "VIDEO", "V4L2"),
    "network-modem": ("WLAN", "WIRELESS", "CFG80211", "MAC80211", "RMNET", "IPA", "QRTR", "NETFILTER"),
    "sensors-misc": ("SENSOR", "IIO", "HWMON", "LEDS", "VIBRATOR", "NFC", "FINGERPRINT"),
}

CRITICAL_CATEGORIES = {
    "boot-diagnostics", "qcom-core", "clock-power", "storage", "iommu-memory", "android-security"
}

CURATED_BOOT_OPTIONS = (
    "CONFIG_QCOM_SCM", "CONFIG_QCOM_RPMH", "CONFIG_QCOM_SMEM", "CONFIG_QCOM_SMP2P",
    "CONFIG_QCOM_GLINK", "CONFIG_QRTR", "CONFIG_QCOM_COMMAND_DB", "CONFIG_COMMON_CLK_QCOM",
    "CONFIG_SDM_GCC_LAGOON", "CONFIG_QCOM_LLCC", "CONFIG_QCOM_LAGOON_LLCC", "CONFIG_QCOM_GDSC",
    "CONFIG_PINCTRL_LAGOON", "CONFIG_PINCTRL_QCOM_SPMI_PMIC", "CONFIG_REGULATOR_QCOM_RPMH",
    "CONFIG_REGULATOR_QCOM_SPMI", "CONFIG_ARM_SMMU", "CONFIG_QTI_IOMMU_SUPPORT",
    "CONFIG_SCSI_UFSHCD", "CONFIG_SCSI_UFS_QCOM", "CONFIG_PHY_QCOM_UFS",
    "CONFIG_MMC_SDHCI_MSM", "CONFIG_MMC_CQHCI", "CONFIG_QCOM_GENI_SE", "CONFIG_SERIAL_MSM_GENI",
    "CONFIG_PSTORE", "CONFIG_PSTORE_RAM", "CONFIG_PSTORE_PMSG", "CONFIG_ANDROID_BINDER_IPC",
    "CONFIG_SECURITY_SELINUX", "CONFIG_DM_VERITY", "CONFIG_EXT4_FS", "CONFIG_F2FS_FS",
)


def parse_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line[2:-11]] = "n"
    return values


def enabled(value: str | None) -> bool:
    return value not in (None, "n")


def category_for(option: str) -> str:
    for category, patterns in CATEGORY_PATTERNS.items():
        if any(pattern in option for pattern in patterns):
            return category
    return "other"


def parse_symvers(path: Path) -> dict[str, tuple[str, str]]:
    symbols: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return symbols
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) >= 3:
            symbols[fields[1]] = (fields[0], fields[2])
    return symbols


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(errors="replace").splitlines() if line.strip()]


def read_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                metadata[key] = value
    return metadata


def write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    gki = parse_config(args.gki / "config")
    touchgrass = parse_config(args.touchgrass / "config")

    all_options = sorted(set(gki) | set(touchgrass))
    different = [option for option in all_options if gki.get(option) != touchgrass.get(option)]
    device_differences = [option for option in different if not option.startswith(ROOT_ONLY_PREFIXES)]
    touchgrass_enabled = [
        option for option in device_differences
        if enabled(touchgrass.get(option)) and not enabled(gki.get(option))
    ]
    gki_enabled = [
        option for option in device_differences
        if enabled(gki.get(option)) and not enabled(touchgrass.get(option))
    ]

    rows: list[list[str]] = []
    for option in touchgrass_enabled:
        category = category_for(option)
        tg_value = touchgrass.get(option, "missing")
        gki_value = gki.get(option, "missing")
        if option in CURATED_BOOT_OPTIONS:
            priority = "P0-boot"
        elif category in CRITICAL_CATEGORIES and tg_value == "y":
            priority = "P1-platform"
        else:
            priority = "P2-hardware"
        mismatch = "missing"
        if gki_value == "m" and tg_value == "y":
            mismatch = "gki-module-vs-touchgrass-built-in"
        rows.append([priority, category, option, tg_value, gki_value, mismatch])

    priority_order = {"P0-boot": 0, "P1-platform": 1, "P2-hardware": 2}
    rows.sort(key=lambda row: (priority_order[row[0]], row[1], row[2]))
    write_csv(
        args.out / "touchgrass-enabled-not-in-gki.csv",
        ["priority", "category", "option", "touchgrass", "gki", "difference"],
        rows,
    )

    write_csv(
        args.out / "all-config-differences.csv",
        ["option", "category", "touchgrass", "gki"],
        [[option, category_for(option), touchgrass.get(option, "missing"), gki.get(option, "missing")]
         for option in device_differences],
    )

    write_csv(
        args.out / "gki-enabled-not-in-touchgrass.csv",
        ["option", "category", "gki", "touchgrass"],
        [[option, category_for(option), gki.get(option, "missing"), touchgrass.get(option, "missing")]
         for option in gki_enabled],
    )

    gki_symbols = parse_symvers(args.gki / "Module.symvers")
    tg_symbols = parse_symvers(args.touchgrass / "Module.symvers")
    common_symbols = sorted(set(gki_symbols) & set(tg_symbols))
    tg_only_symbols = sorted(set(tg_symbols) - set(gki_symbols))
    gki_only_symbols = sorted(set(gki_symbols) - set(tg_symbols))
    crc_mismatches = sorted(
        symbol for symbol in common_symbols if gki_symbols[symbol][0] != tg_symbols[symbol][0]
    )

    write_csv(
        args.out / "touchgrass-only-exported-symbols.csv",
        ["symbol", "crc", "source"],
        [[symbol, tg_symbols[symbol][0], tg_symbols[symbol][1]] for symbol in tg_only_symbols],
    )
    write_csv(
        args.out / "gki-only-exported-symbols.csv",
        ["symbol", "crc", "source"],
        [[symbol, gki_symbols[symbol][0], gki_symbols[symbol][1]] for symbol in gki_only_symbols],
    )
    write_csv(
        args.out / "common-symbol-crc-mismatches.csv",
        ["symbol", "touchgrass_crc", "gki_crc", "touchgrass_source", "gki_source"],
        [[symbol, tg_symbols[symbol][0], gki_symbols[symbol][0], tg_symbols[symbol][1], gki_symbols[symbol][1]]
         for symbol in crc_mismatches],
    )

    categories = Counter(category_for(option) for option in touchgrass_enabled)
    priorities = Counter(row[0] for row in rows)
    gki_modules = read_lines(args.gki / "modules.list")
    tg_modules = read_lines(args.touchgrass / "modules.list")
    device_trees = read_lines(args.touchgrass / "device-trees.list")
    gki_meta = read_metadata(args.gki / "metadata.txt")
    tg_meta = read_metadata(args.touchgrass / "metadata.txt")

    curated_rows = []
    for option in CURATED_BOOT_OPTIONS:
        curated_rows.append(
            f"| `{option}` | `{touchgrass.get(option, 'missing')}` | `{gki.get(option, 'missing')}` |"
        )

    category_rows = "\n".join(
        f"| {category} | {count} |" for category, count in categories.most_common()
    )
    p0_options = [row[2] for row in rows if row[0] == "P0-boot"]

    report = f"""# A52 GKI 4.19 compatibility inventory

## Compared builds

| Build | Kernel release | Image bytes | Modules | Exported symbols |
|---|---:|---:|---:|---:|
| Android Common Kernel GKI | `{gki_meta.get('kernel_release', 'unknown')}` | {gki_meta.get('image_bytes', 'unknown')} | {len(gki_modules)} | {len(gki_symbols)} |
| touchGrass A52 baseline | `{tg_meta.get('kernel_release', 'unknown')}` | {tg_meta.get('image_bytes', 'unknown')} | {len(tg_modules)} | {len(tg_symbols)} |

The touchGrass side is the reviewed Linux 4.19.200 + ReSukiSU safe build. Root-only `CONFIG_KSU*` and `CONFIG_SUSFS*` options are excluded from the device compatibility counts.

## Configuration result

- Total parsed GKI options: **{len(gki)}**
- Total parsed touchGrass options: **{len(touchgrass)}**
- Device-related options with different values: **{len(device_differences)}**
- Enabled in touchGrass but absent or disabled in GKI: **{len(touchgrass_enabled)}**
- P0 early-boot candidates: **{priorities.get('P0-boot', 0)}**
- P1 platform candidates: **{priorities.get('P1-platform', 0)}**
- P2 later hardware candidates: **{priorities.get('P2-hardware', 0)}**

| Category | touchGrass-enabled options missing from GKI |
|---|---:|
{category_rows}

## Curated early-boot comparison

| Option | touchGrass | GKI |
|---|---:|---:|
{chr(10).join(curated_rows)}

## Immediate blockers

The untouched GKI image cannot boot the A52 because the generic configuration lacks the Lagoon/SM7125 platform chain. The first bring-up kernel must keep the following groups built in until module loading is proven:

1. Qualcomm SCM, RPMh, SMEM, SMP2P, GLINK, QRTR and command database.
2. Lagoon clocks, LLCC, GDSC, pinctrl and RPMh/SPMI regulators.
3. GENI serial for early console and ramoops/pstore for failure capture.
4. UFS core, Qualcomm UFS glue, UFS PHY, SMMU/IOMMU and the storage filesystems needed for first-stage mount.
5. Android binder, SELinux and dm-verity compatibility required by the ROM userspace.

P0 options identified by the automated comparison:

{chr(10).join(f'- `{option}`' for option in p0_options)}

## Symbol and module result

- Common exported symbols: **{len(common_symbols)}**
- touchGrass-only exported symbols: **{len(tg_only_symbols)}**
- GKI-only exported symbols: **{len(gki_only_symbols)}**
- Common symbols with different CRCs: **{len(crc_mismatches)}**
- GKI modules: **{len(gki_modules)}**
- touchGrass modules: **{len(tg_modules)}**
- touchGrass DTB/DTBO outputs: **{len(device_trees)}**

A CRC mismatch means a touchGrass-built vendor module cannot be assumed to load into the stock GKI kernel, even when the symbol name exists. The generated CSV files are the input for deciding which drivers can become vendor modules and which code must be rebuilt against the GKI ABI.

## Next engineering gate

Do not package or flash the stock GKI `Image`. The next kernel should be a hybrid development image with only P0 platform support added. Its success condition is early console or ramoops output, not a complete Android boot.
"""
    (args.out / "COMPATIBILITY-REPORT.md").write_text(report)

    rom_checklist = """# Custom ROM intake checklist

A custom ROM ZIP is useful because it shows what userspace expects from the kernel and boot images. Inspect or extract these items when available:

- `boot.img`, `dtbo.img`, `vendor_boot.img`, `init_boot.img` and `vbmeta*.img`
- `payload.bin` when the ROM is an A/B OTA package
- kernel command line, boot header version and ramdisk compression
- embedded DTB and DTBO identifiers
- `vendor/lib/modules`, `vendor_dlkm/lib/modules` and `system_dlkm/lib/modules`
- module dependency, alias and load-order files
- `fstab*`, first-stage init files and AVB flags
- `ro.product.*`, `ro.boot.*`, VNDK and first API-level properties
- SELinux policy files and init services that load kernel modules

The ROM cannot supply missing Qualcomm kernel source by itself, but it can identify the exact ABI, module-loading, partition and device-tree expectations that the hybrid kernel must satisfy.
"""
    (args.out / "CUSTOM-ROM-INPUT.md").write_text(rom_checklist)


if __name__ == "__main__":
    main()
