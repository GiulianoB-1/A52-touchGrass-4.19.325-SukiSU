#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import shutil
from pathlib import Path

REQUIRED = {
    "lagoon-clocks-pinctrl": (
        "CONFIG_SDM_GCC_LAGOON", "CONFIG_PINCTRL_LAGOON", "CONFIG_QCOM_LLCC",
        "CONFIG_CAM_CC_LAGOON", "CONFIG_DISP_CC_LAGOON",
        "CONFIG_GPU_CC_LAGOON", "CONFIG_VIDEO_CC_LAGOON",
        "CONFIG_NPU_CC_LAGOON", "CONFIG_QCOM_CLK_DEBUG",
        "CONFIG_DEBUG_CC_LAGOON",
    ),
    "qcom-boot-core": (
        "CONFIG_QCOM_SCM", "CONFIG_QCOM_SMEM", "CONFIG_QCOM_COMMAND_DB",
        "CONFIG_QCOM_RPMH", "CONFIG_QCOM_AOSS_QMP", "CONFIG_QCOM_GENI_SE",
        "CONFIG_INTERCONNECT_QCOM_SM6350",
    ),
    "memory-storage": (
        "CONFIG_ARM_SMMU", "CONFIG_REGULATOR_QCOM_RPMH",
        "CONFIG_PHY_QCOM_QMP", "CONFIG_SCSI_UFSHCD",
        "CONFIG_SCSI_UFSHCD_PLATFORM", "CONFIG_SCSI_UFS_QCOM",
        "CONFIG_SCSI_UFS_CRYPTO", "CONFIG_BLK_INLINE_ENCRYPTION",
    ),
    "android-filesystems-dm": (
        "CONFIG_EROFS_FS", "CONFIG_EROFS_FS_XATTR", "CONFIG_EROFS_FS_ZIP",
        "CONFIG_F2FS_FS", "CONFIG_F2FS_FS_XATTR",
        "CONFIG_F2FS_FS_POSIX_ACL", "CONFIG_F2FS_FS_COMPRESSION",
        "CONFIG_FS_ENCRYPTION", "CONFIG_FS_ENCRYPTION_INLINE_CRYPT",
        "CONFIG_FS_VERITY", "CONFIG_BLK_DEV_DM", "CONFIG_DM_INIT",
        "CONFIG_DM_UEVENT", "CONFIG_DM_VERITY", "CONFIG_DM_VERITY_FEC",
        "CONFIG_DM_CRYPT", "CONFIG_DM_DEFAULT_KEY",
    ),
    "runtime-contract": (
        "CONFIG_CGROUPS", "CONFIG_CGROUP_FREEZER", "CONFIG_BLK_CGROUP",
        "CONFIG_BLK_CGROUP_RWSTAT", "CONFIG_SERIAL_QCOM_GENI",
        "CONFIG_SERIAL_QCOM_GENI_CONSOLE", "CONFIG_PSTORE",
        "CONFIG_PSTORE_CONSOLE", "CONFIG_PSTORE_PMSG", "CONFIG_PSTORE_RAM",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            symbol, value = line.split("=", 1)
            values[symbol] = value
        else:
            match = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
            if match:
                values[match.group(1)] = "n"
    return values


def audit(args: argparse.Namespace) -> None:
    config = args.config.resolve()
    output = args.output.resolve()
    if not config.is_file():
        raise SystemExit(f"missing integrated config: {config}")
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, output / "integrated.config")

    values = config_values(config)
    rows: list[dict[str, str]] = []
    missing = 0
    for category, symbols in REQUIRED.items():
        for symbol in symbols:
            value = values.get(symbol, "n")
            passed = value == "y"
            missing += int(not passed)
            rows.append({
                "category": category,
                "symbol": symbol,
                "resolved_value": value,
                "required_value": "y",
                "result": "pass" if passed else "fail",
            })

    with (output / "integrated-config-status.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["category", "symbol", "resolved_value", "required_value", "result"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)

    metadata = [
        "artifact_type=a52xq-gki-5.10-integrated-image-dtb-probe-not-flashable",
        f"required_symbol_count={len(rows)}",
        f"required_symbol_failures={missing}",
        "output_scope=integrated-Image-and-Lagoon-DTB-no-boot-packaging",
        "flashable=no",
    ]
    (output / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")
    if missing:
        raise SystemExit(f"{missing} required integrated config symbols did not resolve to y")


def diagnostics(path: Path, limit: int = 40) -> list[str]:
    if not path.is_file():
        return ["log missing"]
    patterns = (
        "error:", "fatal error:", "undefined reference", "No rule to make target",
        "No such file or directory", "implicit declaration", "warning:",
        "FAILED:", "ld.lld:",
    )
    selected: list[str] = []
    lines = path.read_text(errors="replace").splitlines()
    for line in lines:
        if any(pattern.lower() in line.lower() for pattern in patterns):
            cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if cleaned and cleaned not in selected:
                selected.append(cleaned)
        if len(selected) >= limit:
            break
    return selected or [line.strip() for line in lines[-20:] if line.strip()] or ["no diagnostic text found"]


def finalize(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    status = args.status_file.resolve()
    with status.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if {row.get("target") for row in rows} != {"Image", "Lagoon-DTB"}:
        raise SystemExit("integrated build status target set mismatch")
    shutil.copy2(status, output / "build-status.tsv")

    image_row = next(row for row in rows if row["target"] == "Image")
    dtb_row = next(row for row in rows if row["target"] == "Lagoon-DTB")
    report = [
        "# A52xq GKI 5.10 integrated build probe", "",
        "## Safety", "",
        "- This artifact is not flashable.",
        "- It contains no boot image, vendor_boot image, recovery image, or installer.",
        "- Hardware boot validation has not been performed.", "",
        "## Build result", "",
        f"- ARM64 Image: **{image_row['result']}**",
        f"- Image exit code: `{image_row['exit_code']}`",
        f"- Image bytes: `{image_row['bytes']}`",
        f"- Lagoon DTB: **{dtb_row['result']}**",
        f"- DTB exit code: `{dtb_row['exit_code']}`",
        f"- DTB bytes: `{dtb_row['bytes']}`", "",
        "## Image diagnostics", "",
    ]
    report.extend(f"- `{line.replace('`', chr(39))}`" for line in diagnostics(output / "logs" / "Image.log"))
    report.extend(["", "## DTB diagnostics", ""])
    report.extend(f"- `{line.replace('`', chr(39))}`" for line in diagnostics(output / "logs" / "Lagoon-DTB.log"))
    (output / "INTEGRATED-BUILD-REPORT.md").write_text("\n".join(report) + "\n")

    metadata = (output / "analysis-metadata.txt").read_text().rstrip().splitlines()
    metadata.extend([
        f"image_result={image_row['result']}",
        f"image_bytes={image_row['bytes']}",
        f"dtb_result={dtb_row['result']}",
        f"dtb_bytes={dtb_row['bytes']}",
    ])
    (output / "analysis-metadata.txt").write_text("\n".join(metadata) + "\n")

    files = sorted(path for path in output.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    with (output / "SHA256SUMS").open("w") as stream:
        for path in files:
            stream.write(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--config", type=Path, required=True)
    audit_parser.add_argument("--output", type=Path, required=True)
    audit_parser.set_defaults(func=audit)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.add_argument("--status-file", type=Path, required=True)
    finalize_parser.set_defaults(func=finalize)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
