#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import struct
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable

CONFIG_EXACT = {
    "PINCTRL", "PINCTRL_MSM", "PINCTRL_LAGOON", "QCOM_PDC",
    "IRQ_DOMAIN_HIERARCHY", "GENERIC_IRQ_CHIP", "COMMON_CLK_QCOM",
    "DISP_CC_LAGOON", "SDM_DISPCC_LAGOON", "DRM", "DRM_MSM",
    "DRM_MIPI_DSI", "DRM_PANEL", "BACKLIGHT_CLASS_DEVICE",
    "BACKLIGHT_QCOM_SPMI_WLED", "PANEL_S6E3FC3_AMS646YD01_FHD",
    "REGULATOR", "REGULATOR_QCOM_RPMH", "QCOM_RPMH",
    "QCOM_COMMAND_DB", "INTERCONNECT", "INTERCONNECT_QCOM",
    "ARM_SMMU", "QCOM_SCM", "SPMI", "MFD_SPMI_PMIC",
    "PM_GENERIC_DOMAINS", "QCOM_RPMH_POWER_DOMAIN",
}
CONFIG_PREFIXES = (
    "DRM", "FB", "MSM_DRM", "SDE", "DSI", "MDSS", "PANEL", "BACKLIGHT",
    "PINCTRL", "QCOM_PDC", "DISP_CC", "SDM_DISPCC", "COMMON_CLK_QCOM",
    "REGULATOR", "QCOM_RPMH", "QCOM_COMMAND_DB", "INTERCONNECT",
    "ARM_SMMU", "IOMMU", "PHY_QCOM", "QCOM_SCM", "SPMI",
)

EXACT_FILES = [
    "drivers/pinctrl/qcom/pinctrl-lagoon.c",
    "drivers/pinctrl/qcom/pinctrl-msm.c",
    "drivers/pinctrl/qcom/pinctrl-msm.h",
    "drivers/irqchip/qcom-pdc.c",
    "drivers/clk/qcom/dispcc-lagoon.c",
    "drivers/clk/qcom/gcc-lagoon.c",
]

DISPLAY_PATH_TOKENS = (
    "techpack/display", "drivers/gpu/drm/msm", "drivers/video/fbdev/msm",
    "drivers/video/backlight", "drivers/clk/qcom/dispcc", "drivers/irqchip/qcom-pdc",
    "drivers/pinctrl/qcom/pinctrl-lagoon",
)
DISPLAY_NAME_TOKENS = ("dsi", "sde", "mdss", "panel", "dispcc", "wled", "backlight")
CRITICAL_NODE_TOKENS = (
    "display", "dsi", "mdss", "panel", "dispcc", "pinctrl", "pdc",
    "interrupt-controller@b220000", "f100000", "af00000", "phy", "wled",
    "backlight", "smmu", "regulator", "rpmh",
)
FUNCTION_TARGETS = (
    "dsi_display_probe", "dsi_ctrl_probe", "dsi_phy_probe", "dsi_panel_get",
    "dsi_panel_drv_init", "dsi_display_dev_init", "dsi_display_bind",
    "sde_kms_hw_init", "sde_kms_init", "msm_pinctrl_probe",
    "lagoon_pinctrl_probe", "qcom_pdc_init", "qcom_pdc_probe",
    "disp_cc_lagoon_probe", "disp_cc_lagoon_init",
)
INIT_PATTERNS = (
    "early_initcall", "core_initcall", "postcore_initcall", "arch_initcall",
    "subsys_initcall", "fs_initcall", "device_initcall", "late_initcall",
    "module_init", "module_platform_driver", "builtin_platform_driver",
    "IRQCHIP_DECLARE", "CLK_OF_DECLARE",
)
SELECTED_DT_PROPERTIES = {
    "status", "compatible", "interrupt-parent", "interrupts", "interrupts-extended",
    "pinctrl-names", "pinctrl-0", "pinctrl-1", "clocks", "clock-names",
    "power-domains", "power-domain-names", "interconnects", "interconnect-names",
    "phys", "phy-names", "resets", "reset-names", "qcom,platform-supply-entries",
    "qcom,dsi-display-active", "qcom,dsi-display-primary", "qcom,dsi-ctrl",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def relevant_symbol(symbol: str) -> bool:
    return symbol in CONFIG_EXACT or symbol.startswith(CONFIG_PREFIXES)


def enabled(value: str | None) -> bool:
    return value not in (None, "n", "<absent>")


def iter_text_files(root: Path, suffixes: set[str]) -> Iterable[Path]:
    skip = {".git", "out", "workspace", "artifacts", "prebuilts", "toolchain"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in skip for part in rel_parts):
            continue
        yield path


def source_diff(left: Path, right: Path, left_name: str, right_name: str) -> str:
    left_lines = left.read_text(errors="replace").splitlines(keepends=True) if left.is_file() else []
    right_lines = right.read_text(errors="replace").splitlines(keepends=True) if right.is_file() else []
    return "".join(difflib.unified_diff(left_lines, right_lines, fromfile=left_name, tofile=right_name, n=5))


def collect_compatible_providers(root: Path) -> dict[str, list[str]]:
    providers: dict[str, set[str]] = defaultdict(set)
    regexes = [
        re.compile(r"\.compatible\s*=\s*\"([^\"]+)\""),
        re.compile(r"IRQCHIP_DECLARE\s*\([^,]+,\s*\"([^\"]+)\""),
        re.compile(r"(?:CLK_OF_DECLARE|TIMER_OF_DECLARE)\s*\([^,]+,\s*\"([^\"]+)\""),
    ]
    for path in iter_text_files(root, {".c", ".h"}):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for regex in regexes:
            for compat in regex.findall(text):
                providers[compat].add(rel)
    return {key: sorted(value) for key, value in sorted(providers.items())}


def extract_boot_dtb(boot_path: Path, dtb_path: Path) -> dict[str, object]:
    data = boot_path.read_bytes()
    if data[:8] != b"ANDROID!":
        raise SystemExit("invalid Android boot image")
    kernel_size, _, ramdisk_size, _, second_size, _, _, page_size, header_version, _ = struct.unpack_from("<10I", data, 8)
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
    dtb = data[dtb_offset:dtb_offset + dtb_size]
    if len(dtb) != dtb_size or dtb[:4] != b"\xd0\r\xfe\xed":
        raise SystemExit("failed to extract valid DTB")
    dtb_path.write_bytes(dtb)
    return {
        "boot_sha256": sha256(boot_path),
        "dtb_sha256": hashlib.sha256(dtb).hexdigest(),
        "dtb_bytes": len(dtb),
    }


def decompile_dtb(dtb_path: Path, dts_path: Path) -> None:
    dtc = shutil.which("dtc")
    if not dtc:
        raise SystemExit("dtc is required for display dependency analysis")
    proc = subprocess.run(
        [dtc, "-q", "-I", "dtb", "-O", "dts", "-o", str(dts_path), str(dtb_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode:
        raise SystemExit(f"dtc failed: {proc.stderr.strip()}")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def parse_dts_nodes(dts_path: Path) -> list[dict[str, object]]:
    text = strip_comments(dts_path.read_text(errors="replace"))
    nodes: list[dict[str, object]] = []
    stack: list[dict[str, object]] = []
    pending = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("/dts-v1/") or line.startswith("/memreserve/"):
            continue

        if pending:
            pending += " " + line
            if ";" not in line:
                continue
            line = pending
            pending = ""

        if "=" in line and ";" not in line:
            pending = line
            continue

        if line.endswith("{"):
            head = line[:-1].strip()
            if ":" in head:
                _label, head = head.split(":", 1)
                head = head.strip()
            name = head or "/"
            parent = str(stack[-1]["path"]) if stack else ""
            if name == "/":
                path = "/"
            elif parent in ("", "/"):
                path = "/" + name
            else:
                path = parent + "/" + name
            node = {"path": path, "name": name, "properties": {}}
            nodes.append(node)
            stack.append(node)
            continue

        if line.startswith("}"):
            if stack:
                stack.pop()
            continue

        if not stack or not line.endswith(";"):
            continue
        statement = line[:-1].strip()
        props = stack[-1]["properties"]
        assert isinstance(props, dict)
        if "=" in statement:
            key, value = statement.split("=", 1)
            props[key.strip()] = value.strip()
        else:
            props[statement] = True
    return nodes


def quoted_strings(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return re.findall(r'"([^\"]+)"', value)


def compact_value(value: object, limit: int = 360) -> object:
    if value is True:
        return True
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def critical_node(node: dict[str, object]) -> bool:
    path = str(node["path"]).lower()
    props = node["properties"]
    assert isinstance(props, dict)
    compat = " ".join(quoted_strings(props.get("compatible"))).lower()
    combined = path + " " + compat
    return any(token in combined for token in CRITICAL_NODE_TOKENS)


def node_driver_matrix(
    nodes: list[dict[str, object]],
    tg_providers: dict[str, list[str]],
    gki_providers: dict[str, list[str]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for node in nodes:
        if not critical_node(node):
            continue
        props = node["properties"]
        assert isinstance(props, dict)
        compats = quoted_strings(props.get("compatible"))
        tg_matches = {c: tg_providers.get(c, []) for c in compats if tg_providers.get(c)}
        gki_matches = {c: gki_providers.get(c, []) for c in compats if gki_providers.get(c)}
        if compats and not gki_matches and tg_matches:
            risk = "high-touchgrass-only-compatible"
        elif compats and not gki_matches:
            risk = "inspect-no-source-compatible"
        elif gki_matches:
            risk = "matched-in-phase183"
        else:
            risk = "no-compatible-property"
        selected: dict[str, object] = {}
        for key, value in props.items():
            if key in SELECTED_DT_PROPERTIES or key.endswith("-supply"):
                selected[key] = compact_value(value)
        rows.append({
            "path": node["path"],
            "compatibles": compats,
            "touchgrass_matches": tg_matches,
            "phase183_matches": gki_matches,
            "risk": risk,
            "properties": selected,
        })
    return sorted(rows, key=lambda row: str(row["path"]))


def source_inventory(root: Path) -> list[str]:
    rows: list[str] = []
    for path in iter_text_files(root, {".c", ".h", ".dts", ".dtsi"}):
        rel = path.relative_to(root).as_posix()
        low = rel.lower()
        if any(token in low for token in DISPLAY_PATH_TOKENS) or any(token in path.name.lower() for token in DISPLAY_NAME_TOKENS):
            rows.append(rel)
    return sorted(set(rows))


def function_locations(root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {name: [] for name in FUNCTION_TARGETS}
    patterns = {name: re.compile(rf"\b{re.escape(name)}\s*\(") for name in FUNCTION_TARGETS}
    for path in iter_text_files(root, {".c", ".h"}):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        for name, regex in patterns.items():
            if regex.search(text):
                found[name].append(rel)
    return {key: sorted(value) for key, value in found.items()}


def init_registrations(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in iter_text_files(root, {".c"}):
        rel = path.relative_to(root).as_posix()
        low = rel.lower()
        if not (any(token in low for token in DISPLAY_PATH_TOKENS) or any(token in path.name.lower() for token in DISPLAY_NAME_TOKENS)):
            continue
        lines = path.read_text(errors="replace").splitlines()
        for number, line in enumerate(lines, 1):
            if any(pattern in line for pattern in INIT_PATTERNS):
                rows.append({"path": rel, "line": number, "statement": line.strip()})
    return rows


def pinctrl_soc_fields(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(errors="replace")
    match = re.search(
        r"static\s+const\s+struct\s+msm_pinctrl_soc_data\s+lagoon_pinctrl\s*=\s*\{(.*?)\n\};",
        text, re.DOTALL,
    )
    if not match:
        return []
    return sorted(set(re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*=", match.group(1))))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--touchgrass-config", type=Path, required=True)
    parser.add_argument("--gki-config", type=Path, required=True)
    parser.add_argument("--phase183-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.touchgrass, args.gki, args.touchgrass_config, args.gki_config, args.phase183_artifact):
        if not path.exists():
            raise SystemExit(f"missing input: {path}")

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    diff_dir = out / "diffs"
    diff_dir.mkdir(exist_ok=True)

    tg_config = parse_config(args.touchgrass_config)
    gki_config = parse_config(args.gki_config)
    symbols = sorted(symbol for symbol in set(tg_config) | set(gki_config) if relevant_symbol(symbol))
    config_rows: list[dict[str, str]] = []
    for symbol in symbols:
        tv = tg_config.get(symbol, "<absent>")
        gv = gki_config.get(symbol, "<absent>")
        if tv == gv:
            status = "same"
        elif enabled(tv) and not enabled(gv):
            status = "touchgrass-enabled-phase183-disabled"
        elif not enabled(tv) and enabled(gv):
            status = "phase183-enabled-touchgrass-disabled"
        else:
            status = "different"
        config_rows.append({"symbol": f"CONFIG_{symbol}", "touchgrass": tv, "phase183": gv, "status": status})

    exact_rows: list[dict[str, object]] = []
    for rel in EXACT_FILES:
        left, right = args.touchgrass / rel, args.gki / rel
        diff = source_diff(left, right, f"touchgrass/{rel}", f"phase183/{rel}")
        diff_path = diff_dir / (rel.replace("/", "__") + ".diff")
        diff_path.write_text(diff, encoding="utf-8")
        exact_rows.append({
            "path": rel,
            "touchgrass_exists": left.is_file(),
            "phase183_exists": right.is_file(),
            "touchgrass_sha256": sha256(left) if left.is_file() else None,
            "phase183_sha256": sha256(right) if right.is_file() else None,
            "diff_file": diff_path.relative_to(out).as_posix(),
            "diff_lines": len(diff.splitlines()),
        })

    boot_path = args.phase183_artifact / "package/boot.img"
    dtb_path = out / "phase183-preserved-base.dtb"
    dts_path = out / "phase183-preserved-base.dts"
    dtb_info = extract_boot_dtb(boot_path, dtb_path)
    decompile_dtb(dtb_path, dts_path)
    nodes = parse_dts_nodes(dts_path)

    tg_providers = collect_compatible_providers(args.touchgrass)
    gki_providers = collect_compatible_providers(args.gki)
    matrix = node_driver_matrix(nodes, tg_providers, gki_providers)
    high_risk = [row for row in matrix if row["risk"] == "high-touchgrass-only-compatible"]
    no_match = [row for row in matrix if row["risk"] == "inspect-no-source-compatible"]

    tg_pinctrl_fields = pinctrl_soc_fields(args.touchgrass / "drivers/pinctrl/qcom/pinctrl-lagoon.c")
    gki_pinctrl_fields = pinctrl_soc_fields(args.gki / "drivers/pinctrl/qcom/pinctrl-lagoon.c")
    missing_pinctrl_fields = sorted(set(tg_pinctrl_fields) - set(gki_pinctrl_fields))

    tg_functions = function_locations(args.touchgrass)
    gki_functions = function_locations(args.gki)
    tg_init = init_registrations(args.touchgrass)
    gki_init = init_registrations(args.gki)
    tg_inventory = source_inventory(args.touchgrass)
    gki_inventory = source_inventory(args.gki)

    findings: list[str] = []
    if high_risk:
        findings.append(f"{len(high_risk)} display-critical DT nodes have compatibles provided by TouchGrass but not by phase 183.")
        for row in high_risk[:20]:
            findings.append(f"Unmatched runtime node {row['path']}: {', '.join(row['compatibles'])}.")
    if no_match:
        findings.append(f"{len(no_match)} additional display-critical DT nodes have no directly indexed compatible provider in either compared source tree and require manual binding review.")
    config_gaps = [row for row in config_rows if row["status"] == "touchgrass-enabled-phase183-disabled"]
    if config_gaps:
        findings.append(f"{len(config_gaps)} relevant configuration symbols are enabled in TouchGrass but disabled or absent in phase 183.")
    if missing_pinctrl_fields:
        findings.append("Phase 183 Lagoon pinctrl data omits TouchGrass fields: " + ", ".join(missing_pinctrl_fields) + ".")
    if not high_risk:
        findings.append("No TouchGrass-only compatible mismatch remains among the parsed display-critical DT nodes.")
    findings.append("Panel commands, timings and voltages must remain unchanged until compatible, supplier and initialization-order parity checks are clean.")

    result = {
        "artifact_type": "a52-touchgrass-display-parity-v2-not-flashable",
        "touchgrass_commit": subprocess.check_output(["git", "-C", str(args.touchgrass), "rev-parse", "HEAD"], text=True).strip(),
        "gki_commit": subprocess.check_output(["git", "-C", str(args.gki), "rev-parse", "HEAD"], text=True).strip(),
        "phase": 183,
        "dtb": dtb_info,
        "findings": findings,
        "high_risk_compatible_mismatches": high_risk,
        "unindexed_critical_nodes": no_match,
        "missing_lagoon_pinctrl_fields": missing_pinctrl_fields,
        "config_gap_count": len(config_gaps),
    }

    write_json(out / "comparison.json", result)
    write_json(out / "config-comparison.json", config_rows)
    write_json(out / "critical-node-driver-matrix.json", matrix)
    write_json(out / "compatible-providers-touchgrass.json", tg_providers)
    write_json(out / "compatible-providers-phase183.json", gki_providers)
    write_json(out / "function-locations-touchgrass.json", tg_functions)
    write_json(out / "function-locations-phase183.json", gki_functions)
    write_json(out / "init-registration-touchgrass.json", tg_init)
    write_json(out / "init-registration-phase183.json", gki_init)
    write_json(out / "source-inventory-touchgrass.json", tg_inventory)
    write_json(out / "source-inventory-phase183.json", gki_inventory)
    write_json(out / "exact-source-comparison.json", exact_rows)

    report = [
        "# A52 TouchGrass display initialization parity",
        "",
        "This is a non-flashable comparison of the exact working TouchGrass source, the exact phase 183 runtime source, and the Samsung DTB preserved in the tested boot image.",
        "",
        "## Immediate findings",
        "",
    ]
    report.extend(f"- {item}" for item in findings)
    report.extend(["", "## Runtime compatible audit", "", "| Node | Compatible | Phase 183 | TouchGrass | Risk |", "|---|---|---:|---:|---|"])
    for row in matrix:
        report.append(
            f"| `{row['path']}` | `{', '.join(row['compatibles']) or '<none>'}` | "
            f"`{bool(row['phase183_matches'])}` | `{bool(row['touchgrass_matches'])}` | `{row['risk']}` |"
        )
    report.extend(["", "## Relevant configuration differences", "", "| Symbol | TouchGrass | Phase 183 | Status |", "|---|---:|---:|---|"])
    for row in config_rows:
        if row["status"] != "same":
            report.append(f"| `{row['symbol']}` | `{row['touchgrass']}` | `{row['phase183']}` | `{row['status']}` |")
    report.extend(["", "## Lagoon pinctrl parity", ""])
    report.append("TouchGrass fields: `" + "`, `".join(tg_pinctrl_fields) + "`")
    report.append("")
    report.append("Phase 183 fields: `" + "`, `".join(gki_pinctrl_fields) + "`")
    report.append("")
    report.append("Missing from phase 183: `" + "`, `".join(missing_pinctrl_fields) + "`")
    report.extend(["", "## Exact source comparisons", "", "| Path | TouchGrass | Phase 183 | Diff lines |", "|---|---:|---:|---:|"])
    for row in exact_rows:
        report.append(f"| `{row['path']}` | `{row['touchgrass_exists']}` | `{row['phase183_exists']}` | `{row['diff_lines']}` |")
    report.extend(["", "## Generated evidence", ""])
    report.extend([
        "- `critical-node-driver-matrix.json`: DT node to compatible provider mapping",
        "- `phase183-preserved-base.dts`: decompiled tested Samsung DTB",
        "- `config-comparison.json`: display-related Kconfig parity",
        "- `function-locations-*.json`: display probe function source mapping",
        "- `init-registration-*.json`: initcall and driver registration ordering evidence",
        "- `source-inventory-*.json`: display source inventory",
        "- `diffs/`: exact source diffs for PDC, pinctrl and clock controllers",
    ])
    (out / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    with (out / "SHA256SUMS").open("w", encoding="utf-8") as stream:
        for path in sorted(out.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                stream.write(f"{sha256(path)}  {path.relative_to(out).as_posix()}\n")

    print(json.dumps({"status": "ok", "high_risk": len(high_risk), "config_gaps": len(config_gaps)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
