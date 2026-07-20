#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from a52_diag94_common import declare_helper, triplet


def _try_function_span(
    text: str, anchors: tuple[str, ...]
) -> tuple[str, int, int, int] | None:
    """Find the first real function definition, skipping calls and prototypes."""
    best: tuple[str, int, int] | None = None
    for anchor in anchors:
        search = 0
        while True:
            start = text.find(anchor, search)
            if start < 0:
                break
            brace = text.find("{", start)
            semicolon = text.find(";", start)
            if brace >= 0 and (semicolon < 0 or brace < semicolon):
                if best is None or start < best[1]:
                    best = (anchor, start, brace)
                break
            search = start + len(anchor)

    if best is None:
        return None

    anchor, start, brace = best
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return anchor, start, brace, pos + 1
    raise SystemExit(f"{anchor}: function closing brace not found")


def _require_function_span(
    text: str, anchors: tuple[str, ...], label: str
) -> tuple[str, int, int, int]:
    result = _try_function_span(text, anchors)
    if result is None:
        raise SystemExit(
            f"{label}: no function definition found for {', '.join(anchors)}"
        )
    return result


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


def _body_insertion_point(text: str, brace: int) -> int:
    newline = text.find("\n", brace)
    return brace + 1 if newline < 0 else newline + 1


def _line_start(text: str, position: int) -> int:
    newline = text.rfind("\n", 0, position)
    return 0 if newline < 0 else newline + 1


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _insert_phy_triplet_after(
    phy: str,
    candidates: tuple[str, ...],
    label: str,
    message: str,
    arguments: str,
) -> str:
    _, end = _one_line(phy, candidates, label)
    line_start = phy.rfind("\n", 0, end - 1) + 1
    indentation = _indent(phy[line_start:end])
    return (
        phy[:end]
        + triplet(message, arguments, indentation, prefix="A52PHY")
        + phy[end:]
    )


def _instrument_bulk_dependency_helpers(phy: str) -> str:
    clk_return = "return devm_clk_bulk_get(dev, num, qmp->clks);"
    clk_start, clk_end = _one_line(
        phy,
        (clk_return,),
        "instrument QMP PHY clock dependency lookup",
    )
    clk_indent = _indent(phy[clk_start:clk_end])
    clk_block = (
        clk_indent + "{\n"
        + clk_indent + "\tstruct clk *a52_clk;\n"
        + clk_indent + "\tint a52_item_ret;\n"
        + clk_indent + "\tint a52_ret;\n\n"
        + clk_indent + "\ta52_ret = devm_clk_bulk_get(dev, num, qmp->clks);\n"
        + triplet(
            "CLK_BULK ret=%d count=%d",
            "a52_ret, num",
            clk_indent + "\t",
            prefix="A52PHY",
        )
        + clk_indent + "\tif (a52_ret) {\n"
        + clk_indent + "\t\tfor (i = 0; i < num; i++) {\n"
        + clk_indent + "\t\t\ta52_clk = devm_clk_get(dev, cfg->clk_list[i]);\n"
        + clk_indent + "\t\t\ta52_item_ret = IS_ERR(a52_clk) ? PTR_ERR(a52_clk) : 0;\n"
        + triplet(
            "CLK_ITEM index=%d name=%s ret=%d",
            "i, cfg->clk_list[i], a52_item_ret",
            clk_indent + "\t\t\t",
            prefix="A52PHY",
        )
        + clk_indent + "\t\t}\n"
        + clk_indent + "\t}\n"
        + clk_indent + "\treturn a52_ret;\n"
        + clk_indent + "}\n"
    )
    phy = phy[:clk_start] + clk_block + phy[clk_end:]

    vreg_return = "return devm_regulator_bulk_get(dev, num, qmp->vregs);"
    vreg_start, vreg_end = _one_line(
        phy,
        (vreg_return,),
        "instrument QMP PHY regulator dependency lookup",
    )
    vreg_indent = _indent(phy[vreg_start:vreg_end])
    vreg_block = (
        vreg_indent + "{\n"
        + vreg_indent + "\tstruct regulator *a52_vreg;\n"
        + vreg_indent + "\tint a52_item_ret;\n"
        + vreg_indent + "\tint a52_ret;\n\n"
        + vreg_indent + "\ta52_ret = devm_regulator_bulk_get(dev, num, qmp->vregs);\n"
        + triplet(
            "VREG_BULK ret=%d count=%d",
            "a52_ret, num",
            vreg_indent + "\t",
            prefix="A52PHY",
        )
        + vreg_indent + "\tif (a52_ret) {\n"
        + vreg_indent + "\t\tfor (i = 0; i < num; i++) {\n"
        + vreg_indent + "\t\t\ta52_vreg = devm_regulator_get(dev, cfg->vreg_list[i]);\n"
        + vreg_indent + "\t\t\ta52_item_ret = IS_ERR(a52_vreg) ? PTR_ERR(a52_vreg) : 0;\n"
        + triplet(
            "VREG_ITEM index=%d name=%s ret=%d",
            "i, cfg->vreg_list[i], a52_item_ret",
            vreg_indent + "\t\t\t",
            prefix="A52PHY",
        )
        + vreg_indent + "\t\t}\n"
        + vreg_indent + "\t}\n"
        + vreg_indent + "\treturn a52_ret;\n"
        + vreg_indent + "}\n"
    )
    return phy[:vreg_start] + vreg_block + phy[vreg_end:]


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
    indentation = _indent(compat_line)
    bridge = (
        indentation + '.compatible = "qcom,ufs-phy-qmp-v3",\n'
        + indentation + ".data = &sdm845_ufsphy_cfg,\n"
        + "\t}, {\n"
    )
    phy = phy[:compat_start] + bridge + compat_line + phy[compat_end:]

    cfg_start, cfg_end = _one_line(
        phy,
        (
            "qmp->cfg = of_device_get_match_data(dev);",
            "cfg = of_device_get_match_data(dev);",
        ),
        "instrument QMP PHY match-data selection",
    )
    cfg_line = phy[cfg_start:cfg_end]
    cfg_expression = "qmp->cfg" if "qmp->cfg" in cfg_line else "cfg"
    phy = phy[:cfg_end] + triplet(
        "MATCH dev=%s compat=%s cfg=%p children=%u",
        'dev_name(dev), dev->of_node ? of_get_property(dev->of_node, "compatible", NULL) : "<none>", '
        f"{cfg_expression}, dev->of_node ? of_get_available_child_count(dev->of_node) : 0",
        _indent(cfg_line),
        prefix="A52PHY",
    ) + phy[cfg_end:]

    phy = _instrument_bulk_dependency_helpers(phy)
    phy = _insert_phy_triplet_after(
        phy,
        ("usb_serdes = serdes = devm_platform_ioremap_resource(pdev, 0);",),
        "instrument QMP PHY primary resource mapping",
        "STAGE map0 ret=%ld",
        "IS_ERR(serdes) ? PTR_ERR(serdes) : 0L",
    )
    phy = _insert_phy_triplet_after(
        phy,
        ("ret = qcom_qmp_phy_clk_init(dev, cfg);",),
        "instrument QMP PHY clock stage",
        "STAGE clocks ret=%d count=%d",
        "ret, cfg->num_clks",
    )
    phy = _insert_phy_triplet_after(
        phy,
        ("ret = qcom_qmp_phy_reset_init(dev, cfg);",),
        "instrument QMP PHY reset stage",
        "STAGE resets ret=%d count=%d",
        "ret, cfg->num_resets",
    )
    phy = _insert_phy_triplet_after(
        phy,
        ("ret = qcom_qmp_phy_vreg_init(dev, cfg);",),
        "instrument QMP PHY regulator stage",
        "STAGE vregs ret=%d count=%d",
        "ret, cfg->num_vregs",
    )
    phy = _insert_phy_triplet_after(
        phy,
        ("num = of_get_available_child_count(dev->of_node);",),
        "instrument QMP PHY child count",
        "STAGE children num=%d expected=%d",
        "num, expected_phys",
    )
    phy = _insert_phy_triplet_after(
        phy,
        ("ret = qcom_qmp_phy_create(dev, child, id, serdes, cfg);",),
        "instrument QMP PHY lane creation",
        "STAGE create id=%d node=%s ret=%d",
        'id, child ? child->full_name : "<none>", ret',
    )
    phy = _insert_phy_triplet_after(
        phy,
        ("phy_provider = devm_of_phy_provider_register(dev, of_phy_simple_xlate);",),
        "instrument QMP PHY provider registration",
        "STAGE provider ret=%ld",
        "PTR_ERR_OR_ZERO(phy_provider)",
    )
    return phy


def _add_probe_callback_trace(dd: str) -> tuple[str, str]:
    modern = _try_function_span(dd, ("call_driver_probe(",))
    if modern is not None:
        _, start, _, end = modern
        _, declaration_end = _one_line(
            dd,
            ("int ret = 0;", "int ret;"),
            "instrument call_driver_probe entry",
            start,
            end,
        )
        location = "call_driver_probe"
    else:
        _, start, _, end = _require_function_span(
            dd,
            ("really_probe(",),
            "locate legacy really_probe path",
        )
        _, declaration_end = _one_line(
            dd,
            ("atomic_inc(&probe_count);",),
            "instrument really_probe entry",
            start,
            end,
        )
        location = "really_probe"

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
    dd = dd[:declaration_end] + call_trace + dd[declaration_end:]

    _, start, _, end = _require_function_span(
        dd,
        (("call_driver_probe(" if location == "call_driver_probe" else "really_probe("),),
        f"relocate {location} after entry trace",
    )
    returns = _normalized_line_spans(dd, ("return ret;",), start, end)
    if not returns:
        raise SystemExit(f"instrument {location} return: return ret not found")
    return_start, _ = returns[-1]
    ret_trace = (
        "\tif (a52_storage_probe_device(dev)) {\n"
        + triplet(
            "RET dev=%s driver=%s ret=%d",
            "dev_name(dev), drv->name, ret",
            "\t\t",
            prefix="A52DEV",
        )
        + "\t}\n"
    )
    dd = dd[:return_start] + ret_trace + dd[return_start:]
    return dd, location


def instrument_device_core(dd: str) -> tuple[str, str]:
    dd = declare_helper(
        dd,
        ("#include <linux/device.h>\n", "#include <linux/module.h>\n"),
        "declare persistent helper in drivers/base/dd.c",
    )
    if "#include <linux/string.h>\n" not in dd:
        include_anchor = "#include <linux/device.h>\n"
        if include_anchor not in dd:
            raise SystemExit(
                "declare string helper in drivers/base/dd.c: include anchor missing"
            )
        dd = dd.replace(
            include_anchor,
            include_anchor + "#include <linux/string.h>\n",
            1,
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
    _, deferred_start, deferred_brace, _ = _require_function_span(
        dd,
        ("driver_deferred_probe_add(",),
        "locate deferred-probe helper",
    )
    declaration_start = _line_start(dd, deferred_start)
    dd = dd[:declaration_start] + storage_helper + dd[declaration_start:]
    deferred_brace += len(storage_helper)
    insert_at = _body_insertion_point(dd, deferred_brace)
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

    dd, callback_location = _add_probe_callback_trace(dd)

    reason_spans = _normalized_line_spans(
        dd,
        ('dev->p->deferred_probe_reason = kasprintf(GFP_KERNEL, "%pV", vaf);',),
    )
    if len(reason_spans) == 1:
        reason_start, reason_end = reason_spans[0]
        indentation = _indent(dd[reason_start:reason_end])
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
    return dd, callback_location


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
    dd, callback_location = instrument_device_core(
        dd_path.read_text(encoding="utf-8")
    )

    checks = {
        "phy_compatible_bridge": phy.count('"qcom,ufs-phy-qmp-v3"') == 1
        and "&sdm845_ufsphy_cfg" in phy,
        "phy_match_triplet": all(
            phy.count(f"A52PHY copy={copy} MATCH") == 1 for copy in (1, 2, 3)
        ),
        "phy_clock_bulk_triplet": all(
            phy.count(f"A52PHY copy={copy} CLK_BULK") == 1 for copy in (1, 2, 3)
        ),
        "phy_clock_item_triplet": all(
            phy.count(f"A52PHY copy={copy} CLK_ITEM") == 1 for copy in (1, 2, 3)
        ),
        "phy_vreg_bulk_triplet": all(
            phy.count(f"A52PHY copy={copy} VREG_BULK") == 1 for copy in (1, 2, 3)
        ),
        "phy_vreg_item_triplet": all(
            phy.count(f"A52PHY copy={copy} VREG_ITEM") == 1 for copy in (1, 2, 3)
        ),
        "phy_stage_triplets": all(
            all(phy.count(f"A52PHY copy={copy} STAGE {stage}") == 1 for copy in (1, 2, 3))
            for stage in ("map0", "clocks", "resets", "vregs", "children", "create", "provider")
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
                    "bridge qcom,ufs-phy-qmp-v3 to upstream QMP-v3 UFS support, "
                    "trace exact QMP dependency failures, and capture device-core "
                    "calls, returns, deferrals and reasons"
                ),
                "compatibility_bridge": {
                    "from": "qcom,ufs-phy-qmp-v3",
                    "to_configuration": "sdm845_ufsphy_cfg",
                },
                "callback_trace_location": callback_location,
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
