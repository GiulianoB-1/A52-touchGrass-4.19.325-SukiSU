#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

TOUCHGRASS_DT_SOURCES = [
    "arch/arm64/boot/dts/vendor/qcom/lagoon.dtsi",
    "arch/arm64/boot/dts/vendor/qcom/lagoon-sde.dtsi",
    "arch/arm64/boot/dts/vendor/qcom/lagoon-sde-display.dtsi",
    "arch/arm64/boot/dts/vendor/qcom/lagoon-sde-pll.dtsi",
    "arch/arm64/boot/dts/vendor/qcom/lagoon-pinctrl.dtsi",
]

A52_OVERLAYS = [
    "arch/arm64/boot/dts/samsung/a52/a52xq/a52xq_eur_open_w00_r00.dts",
    "arch/arm64/boot/dts/samsung/a52/a52xq/a52xq_eur_open_w00_r01.dts",
    "arch/arm64/boot/dts/samsung/a52/a52xq/a52xq_eur_open_w00_r02.dts",
]

DISPLAY_CHAIN_COMPATIBLES = [
    "qcom,lagoon-pdc",
    "qcom,lagoon-pinctrl",
    "qcom,lagoon-dispcc",
    "qcom,lagoon-gcc",
    "qcom,sde-kms",
    "qcom,smmu_sde_unsec",
    "qcom,smmu_sde_sec",
    "qcom,sde-rsc",
    "qcom,dsi-display",
    "qcom,dsi-ctrl-hw-v2.4",
    "qcom,dsi-phy-v3.0",
    "qcom,mdss_dsi_pll_10nm",
]

PANEL_ID_KEYS = {
    "qcom,mdss-dsi-panel-name",
    "qcom,mdss-dsi-panel-type",
    "qcom,dsi-ctrl-num",
    "qcom,dsi-phy-num",
}


def load_helpers(path: Path):
    spec = importlib.util.spec_from_file_location("display_parity_helpers", path)
    if not spec or not spec.loader:
        raise SystemExit(f"cannot load helper module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def panel_property_group(key: str) -> str | None:
    low = key.lower()
    if key in PANEL_ID_KEYS:
        return "identity"
    if "command" in low or "cmd" in low and "mode" not in low:
        return "commands"
    if any(token in low for token in (
        "timing", "framerate", "front-porch", "back-porch", "pulse-width",
        "panel-width", "panel-height", "jitter", "dsc", "topology",
        "lane", "traffic-mode", "bpp", "color-order", "t-clk",
    )):
        return "timing"
    if key.endswith("-supply") or any(token in low for token in (
        "supply", "gpio", "pinctrl", "reset-sequence", "te-pin", "te-source",
    )):
        return "hardware"
    if any(token in low for token in ("backlight", "bl-", "brightness", "wled")):
        return "backlight"
    if any(token in low for token in ("esd", "status-check", "status-value")):
        return "health"
    return None


def panel_records(nodes: list[dict[str, object]], source: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for node in nodes:
        props = node["properties"]
        assert isinstance(props, dict)
        if "qcom,mdss-dsi-panel-name" not in props:
            continue
        grouped: dict[str, dict[str, object]] = {
            "identity": {}, "commands": {}, "timing": {}, "hardware": {},
            "backlight": {}, "health": {},
        }
        for key, value in props.items():
            group = panel_property_group(key)
            if group:
                text = value if value is True else str(value)
                grouped[group][key] = text
        identity_strings = re.findall(r'"([^\"]+)"', str(props["qcom,mdss-dsi-panel-name"]))
        name = identity_strings[0] if identity_strings else str(props["qcom,mdss-dsi-panel-name"])
        records.append({
            "source": source,
            "path": node["path"],
            "panel_name": name,
            "group_hashes": {group: stable_hash(values) for group, values in grouped.items()},
            "property_counts": {group: len(values) for group, values in grouped.items()},
            "groups": grouped,
        })
    return records


def source_dependency_summary(nodes: list[dict[str, object]], source: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for node in nodes:
        props = node["properties"]
        assert isinstance(props, dict)
        compats = []
        if "compatible" in props:
            compats = re.findall(r'"([^\"]+)"', str(props["compatible"]))
        combined = (str(node["path"]) + " " + " ".join(compats)).lower()
        if not any(token in combined for token in (
            "display", "dsi", "mdss", "sde", "dispcc", "pinctrl", "pdc", "panel",
        )):
            continue
        selected: dict[str, object] = {}
        for key, value in props.items():
            if key in {
                "compatible", "status", "clocks", "clock-names", "interrupt-parent",
                "interrupts", "wakeup-parent", "irqdomain-map", "pinctrl-names",
                "pinctrl-0", "pinctrl-1", "qcom,dsi-ctrl", "qcom,dsi-phy",
                "qcom,dsi-default-panel", "qcom,mdp", "phys", "phy-names",
                "power-domains", "interconnects", "interconnect-names",
            } or key.endswith("-supply"):
                text = value if value is True else str(value)
                selected[key] = text if len(text) <= 1200 else text[:1200] + "..."
        rows.append({
            "source": source,
            "path": node["path"],
            "compatibles": compats,
            "properties": selected,
        })
    return rows


def try_compile_and_apply_overlays(
    touchgrass: Path,
    phase183_dtb: Path,
    output: Path,
) -> list[dict[str, object]]:
    dtc = shutil.which("dtc")
    fdtoverlay = shutil.which("fdtoverlay")
    results: list[dict[str, object]] = []
    compiled = output / "compiled-overlays"
    effective = output / "effective-dtbs"
    compiled.mkdir(exist_ok=True)
    effective.mkdir(exist_ok=True)
    if not dtc or not fdtoverlay:
        return [{"status": "tools-unavailable", "dtc": dtc, "fdtoverlay": fdtoverlay}]

    for rel in A52_OVERLAYS:
        source = touchgrass / rel
        revision = source.stem.rsplit("_", 1)[-1]
        dtbo = compiled / f"{revision}.dtbo"
        eff = effective / f"{revision}.dtb"
        compile_proc = subprocess.run(
            [dtc, "-q", "-@", "-I", "dts", "-O", "dtb", "-o", str(dtbo), str(source)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        row: dict[str, object] = {
            "revision": revision,
            "source": rel,
            "compile_return_code": compile_proc.returncode,
            "compile_stderr": compile_proc.stderr[-4000:],
        }
        if compile_proc.returncode == 0:
            apply_proc = subprocess.run(
                [fdtoverlay, "-i", str(phase183_dtb), "-o", str(eff), str(dtbo)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            row.update({
                "apply_return_code": apply_proc.returncode,
                "apply_stderr": apply_proc.stderr[-4000:],
                "effective_dtb": str(eff.relative_to(output)) if apply_proc.returncode == 0 else None,
            })
        results.append(row)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helpers", type=Path, required=True)
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--phase183-dtb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    helpers = load_helpers(args.helpers)
    out = args.output
    out.mkdir(parents=True, exist_ok=True)

    providers = helpers.collect_compatible_providers(args.gki)
    source_rows: list[dict[str, object]] = []
    panels: list[dict[str, object]] = []
    missing_sources: list[str] = []

    for rel in TOUCHGRASS_DT_SOURCES + A52_OVERLAYS:
        path = args.touchgrass / rel
        if not path.is_file():
            missing_sources.append(rel)
            continue
        nodes = helpers.parse_dts_nodes(path)
        source_rows.extend(source_dependency_summary(nodes, rel))
        panels.extend(panel_records(nodes, rel))

    source_compats = sorted({compat for row in source_rows for compat in row["compatibles"]})
    chain_coverage = []
    for compat in DISPLAY_CHAIN_COMPATIBLES:
        chain_coverage.append({
            "compatible": compat,
            "present_in_touchgrass_dt_sources": compat in source_compats,
            "phase183_provider_files": providers.get(compat, []),
            "phase183_provider_present": bool(providers.get(compat)),
        })

    revision_map: dict[str, list[dict[str, object]]] = {}
    for record in panels:
        source = str(record["source"])
        revision = "base"
        match = re.search(r"_(r\d\d)\.dts$", source)
        if match:
            revision = match.group(1)
        revision_map.setdefault(revision, []).append(record)

    panel_variants: list[dict[str, object]] = []
    by_name: dict[str, dict[str, dict[str, str]]] = {}
    for revision, records in revision_map.items():
        for record in records:
            by_name.setdefault(str(record["panel_name"]), {})[revision] = dict(record["group_hashes"])
    for name, revisions in sorted(by_name.items()):
        groups: dict[str, set[str]] = {}
        for hashes in revisions.values():
            for group, digest in hashes.items():
                groups.setdefault(group, set()).add(digest)
        panel_variants.append({
            "panel_name": name,
            "revisions": revisions,
            "groups_that_differ": sorted(group for group, values in groups.items() if len(values) > 1),
        })

    overlay_results = try_compile_and_apply_overlays(args.touchgrass, args.phase183_dtb, out)

    high_risk = [row for row in chain_coverage if row["present_in_touchgrass_dt_sources"] and not row["phase183_provider_present"]]
    result = {
        "artifact_type": "a52-touchgrass-effective-dt-display-audit-not-flashable",
        "missing_source_files": missing_sources,
        "display_chain_coverage": chain_coverage,
        "high_risk_missing_providers": high_risk,
        "panel_record_count": len(panels),
        "panel_revision_variants": panel_variants,
        "overlay_compile_apply": overlay_results,
    }

    (out / "effective-dt-audit.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (out / "touchgrass-display-dependencies.json").write_text(json.dumps(source_rows, indent=2, sort_keys=True) + "\n")
    (out / "a52-panel-property-hashes.json").write_text(json.dumps(panels, indent=2, sort_keys=True) + "\n")

    lines = [
        "# TouchGrass A52 effective display DT audit",
        "",
        "This audit compares the display dependencies and board-revision panel data in the exact TouchGrass source against providers compiled into the phase 183 source tree. It does not change any DT data.",
        "",
        "## Display-chain provider coverage",
        "",
        "| Compatible | In TouchGrass display DT | Phase 183 provider |",
        "|---|---:|---:|",
    ]
    for row in chain_coverage:
        lines.append(
            f"| `{row['compatible']}` | `{row['present_in_touchgrass_dt_sources']}` | `{row['phase183_provider_present']}` |"
        )
    lines.extend(["", "## High-risk missing providers", ""])
    if high_risk:
        for row in high_risk:
            lines.append(f"- `{row['compatible']}` is used by TouchGrass display DT data but has no phase 183 provider match.")
    else:
        lines.append("- None in the explicit display initialization chain.")
    lines.extend(["", "## Panel differences across A52 revisions", ""])
    if panel_variants:
        for row in panel_variants:
            differing = ", ".join(row["groups_that_differ"]) or "none"
            lines.append(f"- `{row['panel_name']}`: differing property groups across revisions: `{differing}`")
    else:
        lines.append("- No panel records were found in the board overlays.")
    lines.extend(["", "## Overlay reconstruction", ""])
    for row in overlay_results:
        lines.append(
            f"- `{row.get('revision', 'n/a')}`: compile rc `{row.get('compile_return_code', 'n/a')}`, "
            f"apply rc `{row.get('apply_return_code', 'n/a')}`"
        )
    lines.extend(["", "## Generated evidence", "", "- `touchgrass-display-dependencies.json`", "- `a52-panel-property-hashes.json`", "- `effective-dt-audit.json`"])
    (out / "EFFECTIVE-DT-REPORT.md").write_text("\n".join(lines) + "\n")

    print(json.dumps({"status": "ok", "high_risk": len(high_risk), "panels": len(panels)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
