#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path


def parse_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            result[key[7:]] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            result[line[9:-11]] = "n"
    return result


def enabled(value: str | None) -> bool:
    return value not in (None, "n")


def git_value(tree: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(tree), *args], text=True).strip()


def kernel_version(tree: Path) -> str:
    values: dict[str, str] = {}
    for line in (tree / "Makefile").read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in {"VERSION", "PATCHLEVEL", "SUBLEVEL", "EXTRAVERSION"}:
            values[parts[0]] = parts[2]
    return f"{values.get('VERSION')}.{values.get('PATCHLEVEL')}.{values.get('SUBLEVEL')}{values.get('EXTRAVERSION', '')}"


BOOT_SYMBOLS: list[tuple[str, str, str]] = [
    ("core", "ARM64", "64-bit ARM architecture"),
    ("core", "SMP", "multicore boot"),
    ("core", "PREEMPT", "Android vendor scheduling model"),
    ("core", "MODULES", "future vendor modules"),
    ("core", "BLK_DEV_INITRD", "boot ramdisk"),
    ("core", "DEVTMPFS", "early device nodes"),
    ("core", "DEVTMPFS_MOUNT", "automatic devtmpfs mount"),
    ("android", "ANDROID_BINDER_IPC", "Android Binder"),
    ("android", "ANDROID_BINDERFS", "Binder filesystem support"),
    ("android", "CGROUPS", "Android process groups"),
    ("android", "CGROUP_FREEZER", "UN1CA /dev/freezer contract"),
    ("android", "CPUSETS", "UN1CA /dev/cpuset contract"),
    ("android", "BLK_CGROUP", "UN1CA /dev/blkio contract"),
    ("android", "BLK_DEV_THROTTLING", "legacy blkio throttling"),
    ("android", "CFS_BANDWIDTH", "Android task profiles"),
    ("android", "SCHED_TUNE", "UN1CA /dev/stune contract"),
    ("android", "PSI", "Android memory pressure reporting"),
    ("device-tree", "OF", "flattened device tree"),
    ("device-tree", "OF_EARLY_FLATTREE", "early DT parsing"),
    ("device-tree", "OF_RESERVED_MEM", "reserved firmware and ramoops memory"),
    ("interrupts", "ARM_GIC", "interrupt controller"),
    ("interrupts", "ARM_GIC_V3", "GICv3 support"),
    ("interrupts", "ARM_ARCH_TIMER", "architected timer"),
    ("qcom-core", "ARCH_QCOM", "Qualcomm platform support"),
    ("qcom-core", "QCOM_SCM", "secure monitor calls"),
    ("qcom-core", "QCOM_SMEM", "shared memory"),
    ("qcom-core", "QCOM_RPMH", "RPMh power management"),
    ("qcom-core", "QCOM_COMMAND_DB", "RPMh command database"),
    ("qcom-core", "QCOM_AOSS_QMP", "AOSS messaging"),
    ("serial", "QCOM_GENI_SE", "GENI serial engine"),
    ("serial", "SERIAL_QCOM_GENI", "Qualcomm GENI UART"),
    ("serial", "SERIAL_QCOM_GENI_CONSOLE", "early serial console"),
    ("clock", "COMMON_CLK_QCOM", "Qualcomm clocks"),
    ("clock", "QCOM_CLK_RPMH", "RPMh clocks"),
    ("pinctrl", "PINCTRL_QCOM", "Qualcomm pin control"),
    ("regulator", "REGULATOR", "power rails"),
    ("regulator", "REGULATOR_QCOM_RPMH", "Qualcomm RPMh regulators"),
    ("interconnect", "INTERCONNECT", "interconnect framework"),
    ("interconnect", "INTERCONNECT_QCOM", "Qualcomm interconnect providers"),
    ("iommu", "IOMMU_SUPPORT", "DMA isolation"),
    ("iommu", "ARM_SMMU", "Qualcomm system MMU"),
    ("storage", "SCSI", "UFS transport dependency"),
    ("storage", "SCSI_UFSHCD", "UFS host core"),
    ("storage", "SCSI_UFS_QCOM", "Qualcomm UFS host"),
    ("storage", "SCSI_UFS_CRYPTO", "UFS inline encryption"),
    ("storage", "BLK_INLINE_ENCRYPTION", "Android inline block encryption"),
    ("storage", "BLK_INLINE_ENCRYPTION_FALLBACK", "inline encryption fallback"),
    ("storage", "DM_CRYPT", "metadata encryption"),
    ("storage", "DM_VERITY", "verified dynamic partitions"),
    ("storage", "DM_VERITY_FEC", "AVB error correction"),
    ("filesystem", "EXT4_FS", "metadata partition"),
    ("filesystem", "EROFS_FS", "UN1CA system/vendor partitions"),
    ("filesystem", "F2FS_FS", "userdata"),
    ("filesystem", "FS_ENCRYPTION", "file-based encryption"),
    ("filesystem", "FS_ENCRYPTION_INLINE_CRYPT", "F2FS inlinecrypt"),
    ("crypto", "CRYPTO_AES", "Android storage encryption"),
    ("crypto", "CRYPTO_XTS", "AES-XTS file encryption"),
    ("crypto", "CRYPTO_CTS", "AES-CTS filename encryption"),
    ("pstore", "PSTORE", "persistent diagnostics"),
    ("pstore", "PSTORE_RAM", "ramoops backend"),
    ("pstore", "PSTORE_CONSOLE", "persistent console"),
    ("pstore", "PSTORE_PMSG", "Android pmsg"),
    ("usb", "USB", "USB core"),
    ("usb", "USB_GADGET", "ADB gadget mode"),
    ("usb", "USB_CONFIGFS", "Android USB composition"),
    ("usb", "USB_DWC3", "Qualcomm USB controller core"),
    ("usb", "USB_DWC3_QCOM", "Qualcomm DWC3 glue"),
    ("security", "SECURITY_SELINUX", "Android SELinux"),
    ("security", "SECCOMP", "Android sandboxing"),
    ("security", "SECCOMP_FILTER", "userspace syscall filters"),
]


SOURCE_PROBES: list[tuple[str, str, list[str]]] = [
    ("device-tree", "Lagoon/A52 device tree", ["arch/arm64/boot/dts/**/*lagoon*", "arch/arm64/boot/dts/**/*a52xq*", "arch/arm64/boot/dts/**/*sm7225*"]),
    ("clock", "Lagoon-family GCC clocks", ["drivers/clk/qcom/*lagoon*", "drivers/clk/qcom/*sm7150*", "drivers/clk/qcom/*sm7225*"]),
    ("pinctrl", "Lagoon-family pinctrl", ["drivers/pinctrl/qcom/*lagoon*", "drivers/pinctrl/qcom/*sm7150*", "drivers/pinctrl/qcom/*sm7225*"]),
    ("interconnect", "Lagoon-family interconnect", ["drivers/interconnect/qcom/*lagoon*", "drivers/interconnect/qcom/*sm7150*", "drivers/interconnect/qcom/*sm7225*"]),
    ("rpmh", "RPMh and command-db", ["drivers/soc/qcom/*rpmh*", "drivers/soc/qcom/*cmd-db*", "drivers/clk/qcom/*rpmh*", "drivers/regulator/*rpmh*"]),
    ("scm-smem", "SCM and SMEM", ["drivers/firmware/qcom_scm*", "drivers/soc/qcom/*smem*", "drivers/soc/qcom/*socinfo*"]),
    ("geni", "GENI serial engine", ["drivers/soc/qcom/*geni*", "drivers/tty/serial/*geni*", "drivers/i2c/busses/*geni*", "drivers/spi/*geni*"]),
    ("iommu", "ARM SMMU", ["drivers/iommu/arm-smmu*", "drivers/iommu/arm/*smmu*", "drivers/iommu/*qcom*"]),
    ("ufs", "Qualcomm UFS host", ["drivers/scsi/ufs/*qcom*", "drivers/ufs/host/*qcom*"]),
    ("inline-crypto", "Qualcomm ICE and inline crypto", ["drivers/**/*ice*", "block/*crypto*", "fs/crypto/*inline*"]),
    ("pstore", "ramoops/pstore", ["fs/pstore/*", "drivers/platform/chrome/*pstore*"]),
    ("usb", "Qualcomm DWC3", ["drivers/usb/dwc3/*qcom*", "drivers/usb/phy/*qcom*", "drivers/usb/typec/**/*qcom*"]),
]


def matches(root: Path, patterns: list[str]) -> list[str]:
    found: set[str] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                found.add(path.relative_to(root).as_posix())
    return sorted(found)


def device_specific_files(root: Path) -> list[str]:
    result: list[str] = []
    skip = {".git", "out", "toolchain", "prebuilts"}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in skip for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        lower = rel.lower()
        if any(token in lower for token in ("a52xq", "lagoon", "sm7225")):
            result.append(rel)
    return sorted(result)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--touchgrass-config", type=Path, required=True)
    parser.add_argument("--gki-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.touchgrass, args.gki, args.touchgrass_config, args.gki_config):
        if not path.exists():
            raise SystemExit(f"Missing required input: {path}")

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    tg = parse_config(args.touchgrass_config)
    gki = parse_config(args.gki_config)

    (out / "touchgrass-4.19.200.config").write_bytes(args.touchgrass_config.read_bytes())
    (out / "gki-5.10.config").write_bytes(args.gki_config.read_bytes())

    all_symbols = sorted(set(tg) | set(gki))
    with (out / "config-comparison.tsv").open("w") as f:
        f.write("symbol\ttouchgrass_4.19.200\tgki_5.10\tclassification\n")
        for symbol in all_symbols:
            tv, gv = tg.get(symbol, "<absent>"), gki.get(symbol, "<absent>")
            if tv == gv:
                cls = "same"
            elif enabled(tv) and not enabled(gv):
                cls = "touchgrass-enabled-gki-disabled"
            elif not enabled(tv) and enabled(gv):
                cls = "gki-enabled-touchgrass-disabled"
            else:
                cls = "different"
            f.write(f"CONFIG_{symbol}\t{tv}\t{gv}\t{cls}\n")

    missing_boot: list[tuple[str, str, str, str, str]] = []
    with (out / "boot-critical-config.tsv").open("w") as f:
        f.write("group\tsymbol\ttouchgrass_4.19.200\tgki_5.10\tstatus\treason\n")
        for group, symbol, reason in BOOT_SYMBOLS:
            tv, gv = tg.get(symbol, "<absent>"), gki.get(symbol, "<absent>")
            if enabled(tv) and not enabled(gv):
                status = "required-gap"
                missing_boot.append((group, symbol, tv, gv, reason))
            elif enabled(gv):
                status = "present-in-gki"
            else:
                status = "not-enabled-in-working-config"
            f.write(f"{group}\tCONFIG_{symbol}\t{tv}\t{gv}\t{status}\t{reason}\n")

    tg_device = device_specific_files(args.touchgrass)
    with (out / "touchgrass-device-file-map.tsv").open("w") as f:
        f.write("touchgrass_relative_path\texact_path_in_gki\n")
        for rel in tg_device:
            f.write(f"{rel}\t{'yes' if (args.gki / rel).is_file() else 'no'}\n")

    probe_rows: list[tuple[str, str, list[str], list[str]]] = []
    with (out / "source-capability-matrix.tsv").open("w") as f:
        f.write("group\tcapability\ttouchgrass_matches\tgki_matches\n")
        for group, label, patterns in SOURCE_PROBES:
            tgm = matches(args.touchgrass, patterns)
            gkm = matches(args.gki, patterns)
            probe_rows.append((group, label, tgm, gkm))
            f.write(f"{group}\t{label}\t{' | '.join(tgm) or '<none>'}\t{' | '.join(gkm) or '<none>'}\n")

    tg_commit = git_value(args.touchgrass, "rev-parse", "HEAD")
    gki_commit = git_value(args.gki, "rev-parse", "HEAD")
    metadata = (
        "artifact_type=a52xq-gki-5.10-source-gap-analysis-not-flashable\n"
        f"touchgrass_commit={tg_commit}\n"
        f"touchgrass_kernel_version={kernel_version(args.touchgrass)}\n"
        f"gki_commit={gki_commit}\n"
        f"gki_kernel_version={kernel_version(args.gki)}\n"
        f"touchgrass_config_symbols={len(tg)}\n"
        f"gki_config_symbols={len(gki)}\n"
        f"boot_critical_required_gaps={len(missing_boot)}\n"
        f"touchgrass_device_specific_files={len(tg_device)}\n"
    )
    (out / "analysis-metadata.txt").write_text(metadata)

    exact_paths = sum(1 for rel in tg_device if (args.gki / rel).is_file())
    groups_with_no_gki_match = sorted({group for group, _, _, gkm in probe_rows if not gkm})
    tg_only_enabled = sum(1 for s in all_symbols if enabled(tg.get(s)) and not enabled(gki.get(s)))
    gki_only_enabled = sum(1 for s in all_symbols if enabled(gki.get(s)) and not enabled(tg.get(s)))

    report = [
        "# A52xq touchGrass 4.19.200 to Android 12 GKI 5.10 gap analysis",
        "",
        "This is a source and configuration analysis artifact. Nothing here is flashable.",
        "",
        "## Reproducible inputs",
        "",
        f"- touchGrass commit: `{tg_commit}`",
        f"- touchGrass version: `{kernel_version(args.touchgrass)}`",
        f"- GKI commit: `{gki_commit}`",
        f"- GKI version: `{kernel_version(args.gki)}`",
        "",
        "## Configuration summary",
        "",
        f"- Symbols enabled in touchGrass but disabled/absent in GKI: **{tg_only_enabled}**",
        f"- Symbols enabled in GKI but disabled/absent in touchGrass: **{gki_only_enabled}**",
        f"- Boot-critical required gaps from the controlled list: **{len(missing_boot)}**",
        "",
        "## Device-source summary",
        "",
        f"- touchGrass paths containing `a52xq`, `lagoon`, or `sm7225`: **{len(tg_device)}**",
        f"- Those paths already existing at the same location in GKI: **{exact_paths}**",
        f"- Probe groups with no matching GKI source: **{', '.join(groups_with_no_gki_match) or 'none'}**",
        "",
        "## First porting gate",
        "",
        "The next patch set must focus only on early boot and persistent diagnostics:",
        "",
        "1. Lagoon/A52 DT hierarchy and reserved-memory layout.",
        "2. clocks, pinctrl, RPMh regulators, command-db and interconnect.",
        "3. SCM, SMEM, SMMU/IOMMU and GENI dependencies.",
        "4. Qualcomm UFS plus Android inline/metadata encryption prerequisites.",
        "5. EROFS, F2FS, device mapper, legacy UN1CA cgroups, console and ramoops.",
        "",
        "Display, touch, camera, audio, Wi-Fi, modem integration and root modifications are deliberately excluded from the first porting gate.",
        "",
        "## Required-gap symbols",
        "",
    ]
    if missing_boot:
        report.extend(["| Group | Symbol | touchGrass | GKI | Reason |", "|---|---|---:|---:|---|"])
        for group, symbol, tv, gv, reason in missing_boot:
            report.append(f"| {group} | `CONFIG_{symbol}` | `{tv}` | `{gv}` | {reason} |")
    else:
        report.append("No controlled boot-critical configuration gaps were detected.")
    report.append("")
    (out / "PORTING-REPORT.md").write_text("\n".join(report))

    files = sorted(p for p in out.iterdir() if p.is_file() and p.name != "SHA256SUMS")
    with (out / "SHA256SUMS").open("w") as f:
        for path in files:
            f.write(f"{sha256(path)}  {path.name}\n")


if __name__ == "__main__":
    main()
