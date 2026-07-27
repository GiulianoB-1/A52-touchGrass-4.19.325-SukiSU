#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path

PATTERN = re.compile(r"(lagoon|lito|a52|a52xq|r0q|sm7125|sde|dsi|panel)", re.I)
PROPERTIES = (
    "compatible",
    "status",
    "clocks",
    "clock-names",
    "clock-rate",
    "clock-max-rate",
    "power-domains",
    "interconnects",
    "interconnect-names",
    "qcom,cont-splash-enabled",
    "qcom,mdss-dsi-panel-name",
    "qcom,mdss-dsi-panel-controller",
    "qcom,dsi-select-clocks",
    "qcom,platform-regulator-settings",
    "qcom,platform-enable-gpio",
    "qcom,platform-reset-gpio",
    "qcom,platform-bklight-en-gpio",
    "qcom,panel-supply-entries",
    "qcom,mdss-dsi-bl-pmic-control-type",
    "qcom,mdss-dsi-panel-status-check-mode",
    "qcom,ulps-enabled",
    "qcom,suspend-ulps-enabled",
    "qcom,mdss-dsi-lp11-init",
    "qcom,mdss-dsi-init-delay-us",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def normalize(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    text = text.replace("arch/arm64/boot/dts/vendor/qcom/", "arch/arm64/boot/dts/qcom/")
    text = re.sub(r"\s+", "", text)
    return text


def extract_properties(text: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for prop in PROPERTIES:
        values = []
        pattern = re.compile(r"\b" + re.escape(prop) + r"\b\s*(?:=\s*)?([^;{}]*);", re.S)
        for match in pattern.finditer(text):
            values.append(re.sub(r"\s+", " ", match.group(1)).strip())
        if values:
            result[prop] = values
    return result


def file_set(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".dts", ".dtsi"}:
            continue
        rel = path.relative_to(root)
        if PATTERN.search(str(rel)):
            out[str(rel)] = path
    return out


def best_match(rel: str, candidates: dict[str, Path]) -> tuple[str, Path] | None:
    if rel in candidates:
        return rel, candidates[rel]
    base = Path(rel).name
    matches = [(r, p) for r, p in candidates.items() if Path(r).name == base]
    if len(matches) == 1:
        return matches[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ack", type=Path, required=True)
    ap.add_argument("--touchgrass", type=Path, required=True)
    ap.add_argument("--ack-config", type=Path, required=True)
    ap.add_argument("--touch-config", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    tg_root = args.touchgrass / "arch/arm64/boot/dts/vendor/qcom"
    ack_root = args.ack / "arch/arm64/boot/dts/qcom"
    tg_files = file_set(tg_root)
    ack_files = file_set(ack_root)

    comparisons = []
    matched_ack: set[str] = set()
    for rel, tg_path in sorted(tg_files.items()):
        match = best_match(rel, ack_files)
        entry: dict[str, object] = {"touchgrass_rel": rel, "touchgrass_path": str(tg_path)}
        if not match:
            entry["ack_present"] = False
            comparisons.append(entry)
            continue
        ack_rel, ack_path = match
        matched_ack.add(ack_rel)
        tg_text = read(tg_path)
        ack_text = read(ack_path)
        tg_props = extract_properties(tg_text)
        ack_props = extract_properties(ack_text)
        diff = list(difflib.unified_diff(
            tg_text.splitlines(), ack_text.splitlines(),
            fromfile=f"touchgrass/{rel}", tofile=f"ack/{ack_rel}", lineterm=""
        ))
        entry.update({
            "ack_present": True,
            "ack_rel": ack_rel,
            "ack_path": str(ack_path),
            "byte_identical": tg_text == ack_text,
            "normalized_equal": normalize(tg_text) == normalize(ack_text),
            "touchgrass_sha256": sha(tg_text),
            "ack_sha256": sha(ack_text),
            "property_differences": {
                key: {"touchgrass": tg_props.get(key, []), "ack": ack_props.get(key, [])}
                for key in sorted(set(tg_props) | set(ack_props))
                if tg_props.get(key, []) != ack_props.get(key, [])
            },
            "diff_excerpt": diff[:160],
        })
        comparisons.append(entry)
        if diff:
            (args.output / (Path(rel).name + ".diff")).write_text(
                "\n".join(diff[:3000]) + "\n", encoding="utf-8"
            )

    extra_ack = sorted(set(ack_files) - matched_ack)

    def display_config(path: Path) -> dict[str, str]:
        result = {}
        for line in read(path).splitlines():
            if not line.startswith("CONFIG_") and not line.startswith("# CONFIG_"):
                continue
            if re.search(r"(DRM|SDE|DSI|DISPLAY|FB|BACKLIGHT|PM_RUNTIME|PM_GENERIC|INTERCONNECT|REGULATOR|COMMON_CLK_QCOM|QCOM_SCM)", line):
                key = line.split("=", 1)[0].replace("# ", "").replace(" is not set", "")
                result[key] = line
        return result

    tg_cfg = display_config(args.touch_config)
    ack_cfg = display_config(args.ack_config)
    config_diff = {
        key: {"touchgrass": tg_cfg.get(key), "ack": ack_cfg.get(key)}
        for key in sorted(set(tg_cfg) | set(ack_cfg))
        if tg_cfg.get(key) != ack_cfg.get(key)
    }

    report = {
        "status": "a52-display-dt-touchgrass-parity-audit-v1",
        "touchgrass_root": str(tg_root),
        "ack_root": str(ack_root),
        "files": comparisons,
        "extra_ack_files": extra_ack,
        "display_config_differences": config_diff,
        "summary": {
            "touchgrass_candidate_files": len(tg_files),
            "ack_candidate_files": len(ack_files),
            "matched_files": sum(1 for x in comparisons if x.get("ack_present")),
            "normalized_equal_files": sum(1 for x in comparisons if x.get("normalized_equal")),
            "normalized_different_files": sum(1 for x in comparisons if x.get("normalized_equal") is False),
            "missing_ack_files": [x["touchgrass_rel"] for x in comparisons if not x.get("ack_present")],
            "files_with_property_differences": [
                x["touchgrass_rel"] for x in comparisons if x.get("property_differences")
            ],
            "display_config_difference_count": len(config_diff),
        },
    }
    (args.output / "a52-display-dt-touchgrass-parity-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
