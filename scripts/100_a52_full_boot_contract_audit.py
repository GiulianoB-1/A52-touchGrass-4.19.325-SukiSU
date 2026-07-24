#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import struct
import subprocess
from collections import defaultdict
from pathlib import Path

CRITICAL_GROUPS = {
    "storage": ("ufs", "ice", "scsi"),
    "display": ("sde", "mdss", "dsi", "panel", "kgsl", "gpu"),
    "secure": ("qsee", "keymaster", "scm", "secure"),
    "ipc_remoteproc": ("glink", "qrtr", "smp2p", "ipcc", "remoteproc", "pil-", "subsys"),
    "power_clock": ("rpmh", "gdsc", "gcc", "dispcc", "gpucc", "regulator", "interconnect"),
    "iommu": ("smmu", "iommu"),
}

CONFIG_KEYS = [
    "CONFIG_BLK_DEV_DM", "CONFIG_DM_INIT", "CONFIG_DM_VERITY", "CONFIG_DM_VERITY_FEC",
    "CONFIG_DM_DEFAULT_KEY", "CONFIG_DM_CRYPT", "CONFIG_DM_UEVENT",
    "CONFIG_CGROUPS", "CONFIG_BLK_CGROUP", "CONFIG_BFQ_GROUP_IOSCHED",
    "CONFIG_CGROUP_DEVICE", "CONFIG_CGROUP_PIDS", "CONFIG_CGROUP_PERF",
    "CONFIG_CPUSETS", "CONFIG_MEMCG", "CONFIG_CGROUP_BPF",
    "CONFIG_DRM", "CONFIG_DRM_MSM", "CONFIG_DRM_FBDEV_EMULATION", "CONFIG_FB",
    "CONFIG_QCOM_KGSL", "CONFIG_QCOM_KGSL_IOMMU",
    "CONFIG_ION", "CONFIG_ION_SYSTEM_HEAP", "CONFIG_DMABUF_HEAPS",
    "CONFIG_QCOM_SCM", "CONFIG_TEE", "CONFIG_QCOM_QSEECOM",
    "CONFIG_QRTR", "CONFIG_QRTR_SMD", "CONFIG_QRTR_TUN", "CONFIG_QCOM_IPCC",
    "CONFIG_QCOM_SMP2P", "CONFIG_RPMSG", "CONFIG_RPMSG_CHAR",
    "CONFIG_RPMSG_QCOM_GLINK_RPM", "CONFIG_RPMSG_QCOM_GLINK_SMEM",
    "CONFIG_REMOTEPROC", "CONFIG_QCOM_Q6V5_COMMON", "CONFIG_QCOM_RMTFS_MEM",
    "CONFIG_QCOM_RPMHPD", "CONFIG_QCOM_PDC", "CONFIG_QCOM_SOCINFO", "CONFIG_QCOM_QFPROM",
    "CONFIG_ARM_SMMU", "CONFIG_INTERCONNECT", "CONFIG_INTERCONNECT_QCOM",
    "CONFIG_REGULATOR_QCOM_RPMH", "CONFIG_QCOM_RPMH", "CONFIG_QCOM_COMMAND_DB",
    "CONFIG_SCSI_UFS_QCOM", "CONFIG_PHY_QCOM_UFS",
]

SOURCE_SUFFIXES = {".c", ".h", ".dts", ".dtsi", ".S", ".Kconfig"}


def align(value: int, page: int) -> int:
    return (value + page - 1) // page * page


def parse_boot(path: Path) -> dict:
    data = path.read_bytes()
    if data[:8] != b"ANDROID!":
        raise SystemExit(f"not an Android boot image: {path}")
    kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr, tags_addr, page_size, header_version, os_version = struct.unpack_from("<10I", data, 8)
    if header_version < 2:
        raise SystemExit(f"expected boot header v2, got {header_version}")
    recovery_dtbo_size = struct.unpack_from("<I", data, 1632)[0]
    recovery_dtbo_offset = struct.unpack_from("<Q", data, 1636)[0]
    header_size = struct.unpack_from("<I", data, 1644)[0]
    dtb_size = struct.unpack_from("<I", data, 1648)[0]
    dtb_addr = struct.unpack_from("<Q", data, 1652)[0]
    kernel_off = page_size
    ramdisk_off = kernel_off + align(kernel_size, page_size)
    second_off = ramdisk_off + align(ramdisk_size, page_size)
    recovery_dtbo_off = second_off + align(second_size, page_size)
    dtb_off = recovery_dtbo_off + align(recovery_dtbo_size, page_size)
    cmdline = data[64:576].split(b"\0", 1)[0] + data[608:1632].split(b"\0", 1)[0]
    return {
        "raw": data,
        "kernel": data[kernel_off:kernel_off + kernel_size],
        "ramdisk": data[ramdisk_off:ramdisk_off + ramdisk_size],
        "dtb": data[dtb_off:dtb_off + dtb_size],
        "page_size": page_size,
        "header_version": header_version,
        "header_size": header_size,
        "dtb_addr": dtb_addr,
        "cmdline": cmdline.decode(errors="replace"),
        "recovery_dtbo_size": recovery_dtbo_size,
        "recovery_dtbo_offset": recovery_dtbo_offset,
    }


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, text=text, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def extract_ramdisk(blob: bytes, out: Path) -> tuple[str, list[str]]:
    out.mkdir(parents=True, exist_ok=True)
    raw = out.parent / "ramdisk.bin"
    raw.write_bytes(blob)
    fmt = "unknown"
    payload = raw
    if blob[:2] == b"\x1f\x8b":
        fmt = "gzip-cpio"
        decompressed = out.parent / "ramdisk.cpio"
        with gzip.open(raw, "rb") as src, decompressed.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        payload = decompressed
    elif blob[:4] == b"\x04\x22\x4d\x18":
        fmt = "lz4-cpio"
        decompressed = out.parent / "ramdisk.cpio"
        result = subprocess.run(["lz4", "-d", "-c", str(raw)], stdout=decompressed.open("wb"), stderr=subprocess.PIPE)
        if result.returncode != 0:
            return fmt + "-extract-failed", []
        payload = decompressed
    elif blob[:6] in (b"070701", b"070702"):
        fmt = "cpio"
    else:
        return fmt, []
    proc = subprocess.run(["cpio", "-idmu", "--quiet"], cwd=out, stdin=payload.open("rb"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        return fmt + "-extract-failed", []
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    return fmt, files


def parse_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            key = line[len("# "):].split(" ", 1)[0]
            result[key] = "n"
    return result


def load_touchgrass_config(root: Path) -> tuple[Path | None, dict[str, str]]:
    candidates = sorted(root.glob("arch/arm64/configs/*a52*defconfig"))
    if not candidates:
        candidates = sorted(root.glob("arch/arm64/configs/*lagoon*defconfig"))
    if not candidates:
        return None, {}
    preferred = next((p for p in candidates if p.name == "a52xq_defconfig"), candidates[0])
    return preferred, parse_config(preferred)


def scan_compatibles(root: Path, compatibles: list[str]) -> dict[str, list[str]]:
    wanted = set(compatibles)
    hits: dict[str, list[str]] = defaultdict(list)
    for base in (root / "drivers", root / "arch/arm64/boot/dts", root / "include"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES and path.name not in {"Kconfig", "Makefile"}:
                continue
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            for comp in wanted:
                if comp in text:
                    rel = str(path.relative_to(root))
                    if rel not in hits[comp]:
                        hits[comp].append(rel)
    for values in hits.values():
        values.sort()
    return dict(hits)


def classify(comp: str) -> list[str]:
    lower = comp.lower()
    groups = [name for name, tokens in CRITICAL_GROUPS.items() if any(token in lower for token in tokens)]
    return groups or ["other"]


def source_inventory(root: Path, patterns: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for base in (root / "drivers", root / "include"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).lower()
            if any(p in rel for p in patterns):
                out.append(str(path.relative_to(root)))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--touchgrass", type=Path, required=True)
    ap.add_argument("--gki", type=Path, required=True)
    ap.add_argument("--boot", type=Path, required=True)
    ap.add_argument("--gki-config", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()

    tg = args.touchgrass.resolve()
    gki = args.gki.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    boot = parse_boot(args.boot)
    (out / "preserved.dtb").write_bytes(boot["dtb"])
    (out / "boot-cmdline.txt").write_text(boot["cmdline"] + "\n")
    dts = run(["dtc", "-I", "dtb", "-O", "dts", str(out / "preserved.dtb")], check=False).stdout
    (out / "preserved.dts").write_text(dts)
    compatibles = sorted(set(re.findall(r'compatible\s*=\s*(?:"[^"]+"\s*,\s*)*"([^"]+)"', dts)))
    for prop in re.findall(r"compatible\s*=\s*([^;]+);", dts, flags=re.S):
        compatibles.extend(re.findall(r'"([^"]+)"', prop))
    compatibles = sorted(set(compatibles))
    critical = [c for c in compatibles if c.startswith(("qcom,", "samsung,", "stm,")) and classify(c) != ["other"]]

    tg_hits = scan_compatibles(tg, critical)
    gki_hits = scan_compatibles(gki, critical)

    tg_cfg_path, tg_cfg = load_touchgrass_config(tg)
    gki_cfg = parse_config(args.gki_config)
    config_rows = []
    for key in CONFIG_KEYS:
        config_rows.append({"symbol": key, "touchgrass": tg_cfg.get(key, "absent"), "gki": gki_cfg.get(key, "absent")})

    ramdisk_dir = out / "ramdisk-root"
    ramdisk_format, ramdisk_files = extract_ramdisk(boot["ramdisk"], ramdisk_dir)
    init_files = [p for p in ramdisk_files if p == "init" or p.endswith(".rc") or "fstab" in Path(p).name or "ueventd" in Path(p).name]
    contract_lines: list[str] = []
    for rel in init_files:
        p = ramdisk_dir / rel
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if re.search(r"(/dev/|/sys/|/proc/|/vendor/firmware|keymaster|keystore|surfaceflinger|zygote|mount_all|first_stage|logical|super|cgroup|blkio)", line, re.I):
                contract_lines.append(f"{rel}: {line.strip()}")
    (out / "ramdisk-init-contract.txt").write_text("\n".join(contract_lines) + ("\n" if contract_lines else ""))
    (out / "ramdisk-files.txt").write_text("\n".join(ramdisk_files) + ("\n" if ramdisk_files else ""))

    rows = []
    blockers = []
    for comp in critical:
        row = {
            "compatible": comp,
            "groups": classify(comp),
            "touchgrass_files": tg_hits.get(comp, []),
            "gki_files": gki_hits.get(comp, []),
        }
        row["status"] = "exact-gki-match" if row["gki_files"] else ("downstream-only" if row["touchgrass_files"] else "unresolved")
        if row["status"] != "exact-gki-match":
            blockers.append(row)
        rows.append(row)

    inventories = {
        "touchgrass_display": source_inventory(tg, ("/drm/msm", "/mdss", "/gpu/msm", "sde", "dsi")),
        "gki_display": source_inventory(gki, ("/drm/msm", "/mdss", "/gpu/msm", "sde", "dsi")),
        "touchgrass_secure": source_inventory(tg, ("qsee", "smcinvoke", "qtee", "keymaster", "/ion")),
        "gki_secure": source_inventory(gki, ("qsee", "smcinvoke", "qtee", "keymaster", "/ion")),
        "touchgrass_ipc": source_inventory(tg, ("glink", "qrtr", "smp2p", "ipcc", "remoteproc")),
        "gki_ipc": source_inventory(gki, ("glink", "qrtr", "smp2p", "ipcc", "remoteproc")),
    }

    report = {
        "status": "offline-boot-contract-audit-complete",
        "hardware_validated": False,
        "touchgrass_commit": run(["git", "rev-parse", "HEAD"], cwd=tg).stdout.strip(),
        "gki_commit": run(["git", "rev-parse", "HEAD"], cwd=gki).stdout.strip(),
        "boot": {
            "sha256": hashlib.sha256(args.boot.read_bytes()).hexdigest(),
            "header_version": boot["header_version"],
            "page_size": boot["page_size"],
            "dtb_sha256": hashlib.sha256(boot["dtb"]).hexdigest(),
            "ramdisk_sha256": hashlib.sha256(boot["ramdisk"]).hexdigest(),
            "cmdline": boot["cmdline"],
            "ramdisk_format": ramdisk_format,
        },
        "touchgrass_config": str(tg_cfg_path.relative_to(tg)) if tg_cfg_path else None,
        "critical_compatible_count": len(critical),
        "blocker_count": len(blockers),
        "compatibles": rows,
        "config_parity": config_rows,
        "inventories": inventories,
        "ramdisk_contract_line_count": len(contract_lines),
    }
    (out / "boot-contract-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    with (out / "critical-compatible-matrix.tsv").open("w") as f:
        f.write("compatible\tgroups\tstatus\ttouchgrass_files\tgki_files\n")
        for row in rows:
            f.write("\t".join([
                row["compatible"], ",".join(row["groups"]), row["status"],
                ";".join(row["touchgrass_files"]), ";".join(row["gki_files"]),
            ]) + "\n")
    with (out / "config-parity.tsv").open("w") as f:
        f.write("symbol\ttouchgrass\tgki\n")
        for row in config_rows:
            f.write(f"{row['symbol']}\t{row['touchgrass']}\t{row['gki']}\n")

    group_counts = defaultdict(lambda: defaultdict(int))
    for row in rows:
        for group in row["groups"]:
            group_counts[group][row["status"]] += 1
    md = [
        "# A52 4.19 to 5.10 full boot-contract audit",
        "",
        "This report compares the preserved Workflow 99 boot image, the exact working TouchGrass source commit, and the staged Android common 5.10 source.",
        "",
        f"- Critical DT compatibles: **{len(critical)}**",
        f"- Missing or unresolved exact 5.10 bindings: **{len(blockers)}**",
        f"- Ramdisk format: `{ramdisk_format}`",
        f"- Ramdisk contract lines extracted: **{len(contract_lines)}**",
        "",
        "## Subsystem binding summary",
        "",
        "| Subsystem | Exact GKI matches | Downstream-only | Unresolved |",
        "|---|---:|---:|---:|",
    ]
    for group in sorted(group_counts):
        counts = group_counts[group]
        md.append(f"| {group} | {counts['exact-gki-match']} | {counts['downstream-only']} | {counts['unresolved']} |")
    md += ["", "## Highest-priority blockers", ""]
    for row in blockers[:80]:
        tg_text = ", ".join(row["touchgrass_files"][:3]) or "no exact source match"
        md.append(f"- `{row['compatible']}`: **{row['status']}**. TouchGrass: {tg_text}")
    md += [
        "",
        "## Safety conclusion",
        "",
        "The preserved DTB uses downstream SDE/MDSS, KGSL, QSEECom and Qualcomm IPC bindings. A config-only switch to upstream DRM MSM or generic TEE is not a valid compatibility fix. These downstream driver families must be ported with their dependent headers and kernel API adapters before a new flashable image is produced.",
    ]
    (out / "BOOT-CONTRACT-AUDIT.md").write_text("\n".join(md) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
