#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from a52_diag94_common import declare_helper, triplet


def _function_span(text: str, anchor: str, label: str) -> tuple[int, int, int]:
    """Return function start, opening brace, and end, skipping prototypes."""
    search = 0
    while True:
        start = text.find(anchor, search)
        if start < 0:
            raise SystemExit(f"{label}: function definition anchor not found")
        brace = text.find("{", start)
        semicolon = text.find(";", start)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            break
        search = start + len(anchor)

    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, brace, pos + 1
    raise SystemExit(f"{label}: function closing brace not found")


def _normalized_line_spans(
    text: str,
    candidates: tuple[str, ...],
    start: int = 0,
    end: int | None = None,
) -> list[tuple[int, int]]:
    if end is None:
        end = len(text)
    wanted = {candidate.strip() for candidate in candidates}
    spans: list[tuple[int, int]] = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        line_end = cursor + len(line)
        if line.strip() in wanted:
            spans.append((cursor, line_end))
        cursor = line_end
    return spans


def _one_line(
    text: str,
    candidates: tuple[str, ...],
    label: str,
    start: int = 0,
    end: int | None = None,
) -> tuple[int, int]:
    spans = _normalized_line_spans(text, candidates, start, end)
    if len(spans) != 1:
        raise SystemExit(f"{label}: expected one normalized line, found {len(spans)}")
    return spans[0]


def _function_body_insertion_point(text: str, brace: int) -> int:
    newline = text.find("\n", brace)
    return brace + 1 if newline < 0 else newline + 1


def instrument_qmp_phy(phy: str) -> str:
    phy = declare_helper(
        phy,
        ("#include <linux/kernel.h>\n", "#include <linux/module.h>\n"),
        "declare persistent helper in phy-qcom-qmp.c",
    )

    compat_start, compat_end = _one_line(
        phy,
        ('.compatible = "qcom,sdm845-qmp-ufs-phy",',),
        "bridge Samsung downstream QMP-v3 UFS PHY compatible",
    )
    compat_line = phy[compat_start:compat_end]
    indentation = compat_line[: len(compat_line) - len(compat_line.lstrip())]
    bridge = (
        indentation + '.compatible = "qcom,ufs-phy-qmp-v3",\n'
        + indentation + ".data = &sdm845_ufsphy_cfg,\n"
        + "\t}, {\n"
    )
    phy = phy[:compat_start] + bridge + compat_line + phy[compat_end:]

    cfg_candidates = (
        "qmp->cfg = of_device_get_match_data(dev);",
        "cfg = of_device_get_match_data(dev);",
    )
    cfg_start, cfg_end = _one_line(
        phy,
        cfg_candidates,
        "instrument QMP PHY match-data selection",
    )
    cfg_line = phy[cfg_start:cfg_end]
    cfg_expression = "qmp->cfg" if "qmp->cfg" in cfg_line else "cfg"
    phy = phy[:cfg_end] + triplet(
        "MATCH dev=%s node=%s compat=%s cfg=%p children=%u",
        'dev_name(dev), dev->of_node ? dev->of_node->full_name : "<none>", '
        'dev->of_node ? of_get_property(dev->of_node, "compatible", NULL) : "<none>", '
        f"{cfg_expression}, dev->of_node ? of_get_available_child_count(dev->of_node) : 0",
        cfg_line[: len(cfg_line) - len(cfg_line.lstrip())],
        prefix="A52PHY",
    ) + phy[cfg_end:]
    return phy


def instrument_device_core(dd: str) -> str:
    dd = declare_helper(
        dd,
        ("#include <linux/device.h>\n", "#include <linux/module.h>\n"),
        "declare persistent helper in drivers/base/dd.c",
    )

    storage_helper = r'''static bool a52_storage_probe_device(const struct device *dev)
{
	const char *name;

	if (!dev)
		return false;
	name = dev_name(dev);
	return name && (strstr(name, "ufs") || strstr(name, "scsi") ||
			strstr(name, "sdhci") || strstr(name, "1d84000") ||
			strstr(name, "1d87000"));
}

'''
    deferred_start, deferred_brace, _ = _function_span(
        dd,
        "driver_deferred_probe_add(",
        "locate deferred-probe helper",
    )
    dd = dd[:deferred_start] + storage_helper + dd[deferred_start:]
    deferred_brace += len(storage_helper)
    insert_at = _function_body_insertion_point(dd, deferred_brace)
    defer_trace = (
        "\tif (a52_storage_probe_device(dev)) {\n"
        + triplet(
            "DEFER dev=%s driver=%s",
            'dev_name(dev), dev->driver ? dev->driver->name : "<none>"',
            "\t\t",
            prefix="A52DEV",
        )
        + "\t}\n"
    )
    dd = dd[:insert_at] + defer_trace + dd[insert_at:]

    probe_start, _, probe_end = _function_span(
        dd,
        "really_probe(",
        "locate legacy driver probe function",
    )
    entry_start, entry_end = _one_line(
        dd,
        ("atomic_inc(&probe_count);",),
        "instrument legacy driver probe entry",
        probe_start,
        probe_end,
    )
    del entry_start
    call_trace = (
        "\tif (a52_storage_probe_device(dev)) {\n"
        + triplet(
            "CALL dev=%s driver=%s",
            "dev_name(dev), drv->name",
            "\t\t",
            prefix="A52DEV",
        )
        + "\t}\n"
    )
    dd = dd[:entry_end] + call_trace + dd[entry_end:]

    probe_start, _, probe_end = _function_span(
        dd,
        "really_probe(",
        "relocate legacy driver probe function after entry trace",
    )
    return_spans = _normalized_line_spans(
        dd,
        ("return ret;",),
        probe_start,
        probe_end,
    )
    if not return_spans:
        raise SystemExit("instrument legacy driver probe return: return ret not found")
    return_start, _ = return_spans[-1]
    return_trace = (
        "\tif (a52_storage_probe_device(dev)) {\n"
        + triplet(
            "RET dev=%s driver=%s ret=%d",
            "dev_name(dev), drv->name, ret",
            "\t\t",
            prefix="A52DEV",
        )
        + "\t}\n"
    )
    dd = dd[:return_start] + return_trace + dd[return_start:]

    reason_candidates = (
        'dev->p->deferred_probe_reason = kasprintf(GFP_KERNEL, "%pV", vaf);',
    )
    reason_spans = _normalized_line_spans(dd, reason_candidates)
    if len(reason_spans) == 1:
        reason_start, reason_end = reason_spans[0]
        reason_line = dd[reason_start:reason_end]
        indentation = reason_line[: len(reason_line) - len(reason_line.lstrip())]
        reason_trace = (
            indentation + "if (a52_storage_probe_device(dev)) {\n"
            + triplet(
                "REASON dev=%s reason=%pV",
                "dev_name(dev), vaf",
                indentation + "\t",
                prefix="A52DEV",
            )
            + indentation + "}\n"
        )
        dd = dd[:reason_end] + reason_trace + dd[reason_end:]
    return dd


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Add the A52 downstream QMP-v3 UFS PHY compatibility bridge and "
            "device-core bind/defer tracing to the single-shot storage recorder."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    phy_path = gki / "drivers/phy/qualcomm/phy-qcom-qmp.c"
    dd_path = gki / "drivers/base/dd.c"
    if not phy_path.is_file() or not dd_path.is_file():
        raise SystemExit("pinned QMP PHY or device-core source is missing")

    phy = instrument_qmp_phy(phy_path.read_text(encoding="utf-8"))
    dd = instrument_device_core(dd_path.read_text(encoding="utf-8"))

    checks = {
        "phy_compatible_bridge": phy.count('"qcom,ufs-phy-qmp-v3"') == 1
        and "&sdm845_ufsphy_cfg" in phy,
        "phy_match_triplet": all(
            phy.count(f"A52PHY copy={copy} MATCH") == 1 for copy in (1, 2, 3)
        ),
        "device_call_triplet": all(
            dd.count(f"A52DEV copy={copy} CALL") == 1 for copy in (1, 2, 3)
        ),
        "device_return_triplet": all(
            dd.count(f"A52DEV copy={copy} RET") == 1 for copy in (1, 2, 3)
        ),
        "device_defer_triplet": all(
            dd.count(f"A52DEV copy={copy} DEFER") == 1 for copy in (1, 2, 3)
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("UFS PHY bridge staging audit failed: " + ", ".join(failed))

    phy_path.write_text(phy, encoding="utf-8")
    dd_path.write_text(dd, encoding="utf-8")
    (output / "patched-phy-qcom-qmp.c").write_text(phy, encoding="utf-8")
    (output / "patched-drivers-base-dd.c").write_text(dd, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "purpose": (
                    "bridge qcom,ufs-phy-qmp-v3 to upstream QMP-v3 UFS support "
                    "and capture driver calls, returns, deferrals and reasons"
                ),
                "compatibility_bridge": {
                    "from": "qcom,ufs-phy-qmp-v3",
                    "to_configuration": "sdm845_ufsphy_cfg",
                },
                "redundancy": 3,
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


def write_failure_trace() -> None:
    output: Path | None = None
    for index, value in enumerate(sys.argv[:-1]):
        if value == "--output":
            output = Path(sys.argv[index + 1]).resolve()
            break
    if output is None:
        return
    output.mkdir(parents=True, exist_ok=True)
    (output / "stage-error.txt").write_text(traceback.format_exc(), encoding="utf-8")


if __name__ == "__main__":
    try:
        result = main()
    except BaseException:
        write_failure_trace()
        raise
    raise SystemExit(result)
