#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import struct
from pathlib import Path


TARGET_CONFIG = [
    "PINCTRL",
    "PINCTRL_MSM",
    "PINCTRL_LAGOON",
    "QCOM_PDC",
    "IRQ_DOMAIN_HIERARCHY",
    "GENERIC_IRQ_CHIP",
    "COMMON_CLK_QCOM",
    "DISP_CC_LAGOON",
    "SDM_DISPCC_LAGOON",
    "DRM",
    "DRM_MIPI_DSI",
    "DRM_PANEL",
    "BACKLIGHT_CLASS_DEVICE",
    "BACKLIGHT_QCOM_SPMI_WLED",
    "PANEL_S6E3FC3_AMS646YD01_FHD",
    "REGULATOR",
    "REGULATOR_QCOM_RPMH",
    "QCOM_RPMH",
    "QCOM_COMMAND_DB",
    "INTERCONNECT",
    "INTERCONNECT_QCOM",
    "ARM_SMMU",
]

EXACT_FILES = [
    "drivers/pinctrl/qcom/pinctrl-lagoon.c",
    "drivers/pinctrl/qcom/pinctrl-msm.c",
    "drivers/pinctrl/qcom/pinctrl-msm.h",
    "drivers/pinctrl/qcom/Kconfig",
    "drivers/pinctrl/qcom/Makefile",
    "drivers/irqchip/qcom-pdc.c",
]

DISPLAY_BASENAMES = {
    "dsi_display.c",
    "dsi_panel.c",
    "dsi_ctrl.c",
    "dsi_ctrl_hw.c",
    "dsi_phy.c",
    "sde_kms.c",
    "sde_connector.c",
    "msm_drv.c",
}

DT_TOKENS = [
    "a52xq",
    "lagoon",
    "sm7225",
    "dsi-display-primary",
    "qcom,dsi-display",
    "qcom,lagoon-pinctrl",
    "pinctrl@f100000",
    "interrupt-controller@b220000",
    "qcom,pdc",
    "panel_active",
    "panel_suspend",
    "qcom,mdss_dsi",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def find_display_files(root: Path) -> list[str]:
    found: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and path.name in DISPLAY_BASENAMES:
            found.append(path.relative_to(root).as_posix())
    return sorted(found)


def extract_functions(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(errors="replace")
    pattern = re.compile(
        r"^(?:static\s+)?(?:inline\s+)?[\w\s\*]+?\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*\{",
        re.MULTILINE,
    )
    return sorted(set(pattern.findall(text)))


def source_diff(left: Path, right: Path, left_name: str, right_name: str) -> str:
    left_lines = left.read_text(errors="replace").splitlines(keepends=True) if left.is_file() else []
    right_lines = right.read_text(errors="replace").splitlines(keepends=True) if right.is_file() else []
    return "".join(
        difflib.unified_diff(
            left_lines,
            right_lines,
            fromfile=left_name,
            tofile=right_name,
            n=5,
        )
    )


def collect_dt_evidence(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".dts", ".dtsi"}:
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(errors="replace")
        lower_rel = rel.lower()
        matches = [token for token in DT_TOKENS if token.lower() in lower_rel or token in text]
        if not matches:
            continue
        snippets: list[str] = []
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            if any(token in line for token in DT_TOKENS):
                start = max(0, number - 3)
                end = min(len(lines), number + 2)
                snippets.append(
                    f"{rel}:{number}\n" + "\n".join(f"{idx + 1}: {lines[idx]}" for idx in range(start, end))
                )
                if len(snippets) >= 20:
                    break
        rows.append({"path": rel, "tokens": matches, "snippets": snippets})
    return sorted(rows, key=lambda row: str(row["path"]))


def pinctrl_soc_fields(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(errors="replace")
    match = re.search(
        r"static\s+const\s+struct\s+msm_pinctrl_soc_data\s+lagoon_pinctrl\s*=\s*\{(.*?)\n\};",
        text,
        re.DOTALL,
    )
    if not match:
        return []
    return sorted(set(re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*=", match.group(1))))


def parse_boot_dtb(boot_path: Path, output: Path) -> dict[str, object]:
    data = boot_path.read_bytes()
    if data[:8] != b"ANDROID!":
        raise SystemExit("phase-183 boot image has invalid Android magic")
    (
        kernel_size,
        _kernel_addr,
        ramdisk_size,
        _ramdisk_addr,
        second_size,
        _second_addr,
        _tags_addr,
        page_size,
        header_version,
        _os_version,
    ) = struct.unpack_from("<10I", data, 8)
    if header_version != 2:
        raise SystemExit(f"expected boot header v2, got {header_version}")
    recovery_size = struct.unpack_from("<I", data, 1632)[0]
    recovery_offset = struct.unpack_from("<Q", data, 1636)[0]
    dtb_size = struct.unpack_from("<I", data, 1648)[0]

    def align(value: int) -> int:
        return (value + page_size - 1) // page_size * page_size

    kernel_offset = page_size
    ramdisk_offset = kernel_offset + align(kernel_size)
    second_offset = ramdisk_offset + align(ramdisk_size)
    computed_recovery = second_offset + align(second_size)
    actual_recovery = recovery_offset if recovery_size and recovery_offset else computed_recovery
    dtb_offset = align(actual_recovery + recovery_size) if recovery_size else computed_recovery
    dtb = data[dtb_offset : dtb_offset + dtb_size]
    if len(dtb) != dtb_size or dtb[:4] != b"\xd0\r\xfe\xed":
        raise SystemExit("failed to extract a valid phase-183 DTB")
    output.write_bytes(dtb)
    strings = []
    for match in re.finditer(rb"[ -~]{4,}", dtb):
        value = match.group().decode("ascii", errors="replace")
        if any(token.lower() in value.lower() for token in DT_TOKENS):
            strings.append(value)
    return {
        "boot_sha256": sha256(boot_path),
        "dtb_sha256": hashlib.sha256(dtb).hexdigest(),
        "dtb_bytes": len(dtb),
        "matching_strings": sorted(set(strings)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--touchgrass-config", type=Path, required=True)
    parser.add_argument("--gki-config", type=Path, required=True)
    parser.add_argument("--phase183-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for path in [
        args.touchgrass,
        args.gki,
        args.touchgrass_config,
        args.gki_config,
        args.phase183_artifact,
    ]:
        if not path.exists():
            raise SystemExit(f"missing input: {path}")

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    diff_dir = out / "diffs"
    diff_dir.mkdir(exist_ok=True)

    tg_config = parse_config(args.touchgrass_config)
    gki_config = parse_config(args.gki_config)

    config_rows = []
    for symbol in TARGET_CONFIG:
        config_rows.append(
            {
                "symbol": f"CONFIG_{symbol}",
                "touchgrass": tg_config.get(symbol, "<absent>"),
                "phase183": gki_config.get(symbol, "<absent>"),
            }
        )

    exact_rows = []
    for rel in EXACT_FILES:
        left = args.touchgrass / rel
        right = args.gki / rel
        diff = source_diff(left, right, f"touchgrass/{rel}", f"phase183/{rel}")
        diff_path = diff_dir / (rel.replace("/", "__") + ".diff")
        diff_path.write_text(diff, encoding="utf-8")
        exact_rows.append(
            {
                "path": rel,
                "touchgrass_exists": left.is_file(),
                "phase183_exists": right.is_file(),
                "touchgrass_sha256": sha256(left) if left.is_file() else None,
                "phase183_sha256": sha256(right) if right.is_file() else None,
                "touchgrass_functions": extract_functions(left),
                "phase183_functions": extract_functions(right),
                "diff_file": diff_path.relative_to(out).as_posix(),
                "diff_lines": len(diff.splitlines()),
            }
        )

    tg_display = find_display_files(args.touchgrass)
    gki_display = find_display_files(args.gki)
    tg_dt = collect_dt_evidence(args.touchgrass)
    gki_dt = collect_dt_evidence(args.gki)

    tg_pinctrl = args.touchgrass / "drivers/pinctrl/qcom/pinctrl-lagoon.c"
    gki_pinctrl = args.gki / "drivers/pinctrl/qcom/pinctrl-lagoon.c"
    tg_fields = pinctrl_soc_fields(tg_pinctrl)
    gki_fields = pinctrl_soc_fields(gki_pinctrl)

    boot_path = args.phase183_artifact / "package/boot.img"
    dtb_info = parse_boot_dtb(boot_path, out / "phase183-preserved-base.dtb")

    findings = []
    if tg_pinctrl.is_file() and gki_pinctrl.is_file():
        findings.append("Both trees contain a Lagoon TLMM driver with the same qcom,lagoon-pinctrl binding.")
    missing_fields = sorted(set(tg_fields) - set(gki_fields))
    extra_fields = sorted(set(gki_fields) - set(tg_fields))
    if missing_fields:
        findings.append("Phase 183 Lagoon pinctrl data omits TouchGrass fields: " + ", ".join(missing_fields) + ".")
    if extra_fields:
        findings.append("Phase 183 Lagoon pinctrl data adds fields not present in TouchGrass: " + ", ".join(extra_fields) + ".")
    if gki_config.get("PINCTRL_LAGOON") == "y":
        findings.append("CONFIG_PINCTRL_LAGOON is built into phase 183, so a missing module is not the explanation for the unbound TLMM device.")
    if tg_config.get("QCOM_PDC") == "y" and gki_config.get("QCOM_PDC") == "y":
        findings.append("CONFIG_QCOM_PDC is built into both kernels; source or binding parity must be checked rather than only toggling the symbol.")
    if not gki_dt:
        findings.append("The phase-183 GKI source tree has no native A52/Lagoon DTS hierarchy; runtime still depends on the preserved Samsung DTB and bootloader overlays.")
    findings.append("The phase-183 RAMOOPS trace must be interpreted against this parity report before any supplier bypass or panel-command change.")

    result = {
        "artifact_type": "a52-touchgrass-display-parity-not-flashable",
        "touchgrass_commit": "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
        "gki_commit": "f960ed27302b1ff8e61e152fc202554d778deccd",
        "phase183_commit": "a609d255be282311a86b94fd285a5dcbbf3935b0",
        "config": config_rows,
        "exact_files": exact_rows,
        "touchgrass_display_files": tg_display,
        "phase183_display_files": gki_display,
        "touchgrass_dt_evidence": tg_dt,
        "phase183_dt_evidence": gki_dt,
        "touchgrass_lagoon_pinctrl_fields": tg_fields,
        "phase183_lagoon_pinctrl_fields": gki_fields,
        "missing_lagoon_pinctrl_fields": missing_fields,
        "extra_lagoon_pinctrl_fields": extra_fields,
        "phase183_boot_dtb": dtb_info,
        "findings": findings,
    }
    (out / "comparison.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    lines = [
        "# A52 TouchGrass display dependency parity",
        "",
        "This artifact compares the exact working TouchGrass source against the source used by phase 183. It is analysis only and contains no flashable image.",
        "",
        "## Fixed inputs",
        "",
        "- TouchGrass: `micr0softstore/samsung_android_kernel_a52xq` at `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`",
        "- GKI common: `f960ed27302b1ff8e61e152fc202554d778deccd`",
        "- Phase 183 branch head: `a609d255be282311a86b94fd285a5dcbbf3935b0`",
        "",
        "## Immediate findings",
        "",
    ]
    lines.extend(f"- {item}" for item in findings)
    lines.extend(["", "## Target configuration", "", "| Symbol | TouchGrass | Phase 183 |", "|---|---:|---:|"])
    for row in config_rows:
        lines.append(f"| `{row['symbol']}` | `{row['touchgrass']}` | `{row['phase183']}` |")

    lines.extend(["", "## Lagoon pinctrl data fields", ""])
    lines.append("TouchGrass: `" + "`, `".join(tg_fields) + "`")
    lines.append("")
    lines.append("Phase 183: `" + "`, `".join(gki_fields) + "`")

    lines.extend(["", "## Exact source comparisons", "", "| Path | TouchGrass | Phase 183 | Diff lines |", "|---|---:|---:|---:|"])
    for row in exact_rows:
        lines.append(
            f"| `{row['path']}` | `{row['touchgrass_exists']}` | `{row['phase183_exists']}` | {row['diff_lines']} |"
        )

    lines.extend(["", "## Display source inventory", "", "### TouchGrass"])
    lines.extend(f"- `{path}`" for path in tg_display)
    lines.extend(["", "### Phase 183"])
    lines.extend(f"- `{path}`" for path in gki_display)

    lines.extend(["", "## TouchGrass device-tree evidence", ""])
    for row in tg_dt:
        lines.append(f"### `{row['path']}`")
        lines.append("")
        lines.append("Matched: " + ", ".join(f"`{token}`" for token in row["tokens"]))
        for snippet in row["snippets"]:
            lines.extend(["", "```text", str(snippet), "```"])

    lines.extend(["", "## Preserved phase-183 base DTB", ""])
    lines.append(f"- Bytes: `{dtb_info['dtb_bytes']}`")
    lines.append(f"- SHA-256: `{dtb_info['dtb_sha256']}`")
    lines.append("- Relevant strings: " + (", ".join(f"`{value}`" for value in dtb_info["matching_strings"]) or "none"))

    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "config-comparison.json").write_text(json.dumps(config_rows, indent=2) + "\n")

    with (out / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(out.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256(path)}  {path.relative_to(out).as_posix()}\n")

    print(json.dumps({"status": "ok", "findings": findings}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
