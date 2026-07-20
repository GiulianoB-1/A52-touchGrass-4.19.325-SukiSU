#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import traceback
import urllib.request
from pathlib import Path


# Keep the exact source identity separate from the URL construction so the
# staging report can state precisely what was imported.
DOWNSTREAM_REGULATOR_COMMIT = "28f2b66a16e50693a0796b323d652ed1669115b1"
DOWNSTREAM_REGULATOR_PATH = "drivers/regulator/rpmh-regulator.c"
DOWNSTREAM_REGULATOR_BLOB = "e6667c59818621a99bc53fc560e353d8c25e14e5"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def function_span(text: str, anchor: str, label: str) -> tuple[int, int, int]:
    start = text.find(anchor)
    if start < 0:
        raise SystemExit(f"{label}: function anchor missing: {anchor}")
    brace = text.find("{", start)
    semi = text.find(";", start)
    if brace < 0 or (semi >= 0 and semi < brace):
        raise SystemExit(f"{label}: anchor did not resolve to a definition")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, brace, pos + 1
    raise SystemExit(f"{label}: closing brace not found")


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def download_downstream_regulator() -> str:
    url = (
        "https://android.googlesource.com/kernel/msm/+/"
        f"{DOWNSTREAM_REGULATOR_COMMIT}/{DOWNSTREAM_REGULATOR_PATH}?format=TEXT"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "a52-rpmh-provider-bridge"})
    with urllib.request.urlopen(request, timeout=90) as response:
        encoded = response.read()
    data = base64.b64decode(encoded)
    actual = git_blob_sha(data)
    if actual != DOWNSTREAM_REGULATOR_BLOB:
        raise SystemExit(
            "downstream RPMh regulator source identity mismatch: "
            f"expected {DOWNSTREAM_REGULATOR_BLOB}, got {actual}"
        )
    return data.decode("utf-8")


def add_helper_declaration(text: str, include_anchor: str, label: str) -> str:
    declaration = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    if declaration in text:
        return text
    return replace_once(text, include_anchor, include_anchor + declaration, label)


def patch_clock_driver(text: str) -> str:
    if "qcom,lagoon-rpmh-clk" in text:
        raise SystemExit("Lagoon RPMh clock provider is already present")

    text = add_helper_declaration(
        text,
        "#include <linux/platform_device.h>\n",
        "declare persistent helper in clk-rpmh.c",
    )

    if "#define __DEFINE_CLK_RPMH(" not in text:
        raise SystemExit("clk-rpmh.c does not expose the platform-prefixed RPMh macros")

    provider_block = r'''/*
 * Samsung's Lagoon DT uses the downstream RPMh clock provider compatible and
 * baked numeric IDs. Keep the IDs explicit: changing the common binding header
 * cannot change cells already stored in the shipped DTB.
 */
enum a52_lagoon_rpmh_clock_ids {
	A52_LAGOON_RPMH_CXO_CLK = 0,
	A52_LAGOON_RPMH_CXO_CLK_A = 1,
	A52_LAGOON_RPMH_LN_BB_CLK2 = 4,
	A52_LAGOON_RPMH_LN_BB_CLK2_A = 5,
	A52_LAGOON_RPMH_LN_BB_CLK3 = 6,
	A52_LAGOON_RPMH_LN_BB_CLK3_A = 7,
	A52_LAGOON_RPMH_QLINK_CLK = 20,
	A52_LAGOON_RPMH_QLINK_CLK_A = 21,
	A52_LAGOON_RPMH_NUM_CLKS = 22,
};

DEFINE_CLK_RPMH_ARC(lagoon, bi_tcxo, bi_tcxo_ao, "xo.lvl", 0x3, 4);
DEFINE_CLK_RPMH_ARC(lagoon, qlink, qlink_ao, "qphy.lvl", 0x1, 4);
DEFINE_CLK_RPMH_VRM(lagoon, ln_bb_clk2, ln_bb_clk2_ao, "lnbclkg2", 4);
DEFINE_CLK_RPMH_VRM(lagoon, ln_bb_clk3, ln_bb_clk3_ao, "lnbclkg3", 4);

static struct clk_hw *lagoon_rpmh_clocks[A52_LAGOON_RPMH_NUM_CLKS] = {
	[A52_LAGOON_RPMH_CXO_CLK] = &lagoon_bi_tcxo.hw,
	[A52_LAGOON_RPMH_CXO_CLK_A] = &lagoon_bi_tcxo_ao.hw,
	[A52_LAGOON_RPMH_LN_BB_CLK2] = &lagoon_ln_bb_clk2.hw,
	[A52_LAGOON_RPMH_LN_BB_CLK2_A] = &lagoon_ln_bb_clk2_ao.hw,
	[A52_LAGOON_RPMH_LN_BB_CLK3] = &lagoon_ln_bb_clk3.hw,
	[A52_LAGOON_RPMH_LN_BB_CLK3_A] = &lagoon_ln_bb_clk3_ao.hw,
	[A52_LAGOON_RPMH_QLINK_CLK] = &lagoon_qlink.hw,
	[A52_LAGOON_RPMH_QLINK_CLK_A] = &lagoon_qlink_ao.hw,
};

static const struct clk_rpmh_desc clk_rpmh_lagoon = {
	.clks = lagoon_rpmh_clocks,
	.num_clks = ARRAY_SIZE(lagoon_rpmh_clocks),
};

'''
    provider_anchor = "static struct clk_hw *of_clk_rpmh_hw_get("
    pos = text.find(provider_anchor)
    if pos < 0:
        raise SystemExit("clk-rpmh.c provider getter anchor missing")
    text = text[:pos] + provider_block + text[pos:]

    table_anchor = "static const struct of_device_id clk_rpmh_match_table[] = {"
    table_start = text.find(table_anchor)
    if table_start < 0:
        raise SystemExit("clk-rpmh.c match table missing")
    table_end = text.find("\n};", table_start)
    if table_end < 0:
        raise SystemExit("clk-rpmh.c match table end missing")
    table = text[table_start:table_end]
    sentinel_matches = list(re.finditer(r"^\s*\{\s*\}\s*,?\s*$", table, re.MULTILINE))
    if len(sentinel_matches) != 1:
        raise SystemExit(
            f"clk-rpmh.c match sentinel: expected one, found {len(sentinel_matches)}"
        )
    insert = table_start + sentinel_matches[0].start()
    match_line = (
        '\t{ .compatible = "qcom,lagoon-rpmh-clk", '
        ".data = &clk_rpmh_lagoon },\n"
    )
    text = text[:insert] + match_line + text[insert:]

    probe_start, _, probe_end = function_span(text, "static int clk_rpmh_probe(", "clock probe")
    probe = text[probe_start:probe_end]
    desc_anchor = "\tdesc = of_device_get_match_data(&pdev->dev);\n"
    if desc_anchor not in probe:
        raise SystemExit("clk-rpmh.c probe match-data anchor missing")
    probe = probe.replace(
        desc_anchor,
        desc_anchor
        + '\ta52_persistent_diag_mark("A52RPMHCLK PROBE dev=%s compat=%s desc=%p\\n",\n'
        + '\t\tdev_name(&pdev->dev), pdev->dev.of_node ?\n'
        + '\t\tof_get_property(pdev->dev.of_node, "compatible", NULL) : "<none>", desc);\n',
        1,
    )
    returns = list(re.finditer(r"^\treturn 0;\s*$", probe, re.MULTILINE))
    if not returns:
        raise SystemExit("clk-rpmh.c probe success return missing")
    return_pos = returns[-1].start()
    probe = (
        probe[:return_pos]
        + '\ta52_persistent_diag_mark("A52RPMHCLK READY dev=%s clocks=%zu\\n",\n'
        + "\t\tdev_name(&pdev->dev), desc->num_clks);\n"
        + probe[return_pos:]
    )
    text = text[:probe_start] + probe + text[probe_end:]
    return text


def compatibility_defines() -> str:
    return r'''#include <dt-bindings/regulator/qcom,rpmh-regulator.h>

/* Samsung downstream DT set selectors omitted by the upstream binding header. */
#ifndef RPMH_REGULATOR_SET_SLEEP
#define RPMH_REGULATOR_SET_SLEEP	1
#define RPMH_REGULATOR_SET_ACTIVE	2
#define RPMH_REGULATOR_SET_ALL		3
#endif
#ifndef RPMH_REGULATOR_MODE_PASS
#define RPMH_REGULATOR_MODE_PASS	4
#endif

extern void a52_persistent_diag_mark(const char *fmt, ...);
'''


def patch_arc_aux_reader(text: str) -> str:
    start, _, end = function_span(
        text,
        "static int\nrpmh_regulator_load_arc_level_mapping(",
        "downstream ARC level reader",
    )
    replacement = r'''static int
rpmh_regulator_load_arc_level_mapping(struct rpmh_aggr_vreg *aggr_vreg)
{
	const u8 *buf;
	size_t len;
	int i;

	buf = cmd_db_read_aux_data(aggr_vreg->resource_name, &len);
	if (IS_ERR(buf))
		return PTR_ERR(buf);
	if (!buf || len < RPMH_ARC_LEVEL_SIZE)
		return -EINVAL;

	len = min_t(size_t, len,
		    RPMH_ARC_MAX_LEVELS * RPMH_ARC_LEVEL_SIZE);
	aggr_vreg->level_count = len / RPMH_ARC_LEVEL_SIZE;
	for (i = 0; i < aggr_vreg->level_count; i++) {
		aggr_vreg->level[i] = buf[i * RPMH_ARC_LEVEL_SIZE] |
			(buf[i * RPMH_ARC_LEVEL_SIZE + 1] << 8);

		/* Command DB may pad the map with zero entries. */
		if (i > 0 && aggr_vreg->level[i] == 0) {
			aggr_vreg->level_count = i;
			break;
		}
	}

	return aggr_vreg->level_count ? 0 : -EINVAL;
}'''
    return text[:start] + replacement + text[end:]


def patch_downstream_regulator(text: str) -> str:
    text = replace_once(
        text,
        "#include <dt-bindings/regulator/qcom,rpmh-regulator-levels.h>\n",
        compatibility_defines(),
        "replace downstream-only RPMh regulator binding header",
    )
    text = patch_arc_aux_reader(text)
    text = replace_once(
        text,
        '.name = "qcom,rpmh-regulator",',
        '.name = "a52-rpmh-regulator-downstream",',
        "give downstream regulator driver a unique name",
    )

    probe_start, _, probe_end = function_span(
        text, "static int rpmh_regulator_probe(", "downstream regulator probe"
    )
    probe = text[probe_start:probe_end]
    declarations = "\tint rc, i, sid;\n"
    if declarations not in probe:
        raise SystemExit("downstream regulator probe declaration anchor missing")
    probe = probe.replace(
        declarations,
        declarations
        + '\ta52_persistent_diag_mark("A52RPMHREG PROBE dev=%s compat=%s\\n",\n'
        + '\t\tdev_name(dev), dev->of_node ?\n'
        + '\t\tof_get_property(dev->of_node, "compatible", NULL) : "<none>");\n',
        1,
    )
    addr_anchor = "\taggr_vreg->addr = cmd_db_read_addr(aggr_vreg->resource_name);\n"
    if addr_anchor not in probe:
        raise SystemExit("downstream regulator command-db address anchor missing")
    probe = probe.replace(
        addr_anchor,
        addr_anchor
        + '\ta52_persistent_diag_mark("A52RPMHREG RESOURCE dev=%s name=%s addr=0x%x\\n",\n'
        + "\t\tdev_name(dev), aggr_vreg->resource_name, aggr_vreg->addr);\n",
        1,
    )
    returns = list(re.finditer(r"^\treturn rc;\s*$", probe, re.MULTILINE))
    if not returns:
        raise SystemExit("downstream regulator probe final return missing")
    final_return = returns[-1].start()
    probe = (
        probe[:final_return]
        + '\ta52_persistent_diag_mark("A52RPMHREG READY dev=%s name=%s count=%d ret=%d\\n",\n'
        + "\t\tdev_name(dev), aggr_vreg->resource_name,\n"
        + "\t\taggr_vreg->vreg_count, rc);\n"
        + probe[final_return:]
    )
    return text[:probe_start] + probe + text[probe_end:]


def patch_regulator_makefile(text: str) -> str:
    object_line = "obj-$(CONFIG_REGULATOR_QCOM_RPMH) += qcom-rpmh-regulator.o\n"
    addition = (
        object_line
        + "obj-$(CONFIG_REGULATOR_QCOM_RPMH) += a52-rpmh-regulator-downstream.o\n"
    )
    return replace_once(
        text,
        object_line,
        addition,
        "compile downstream RPMh resource regulator",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bridge Samsung Lagoon downstream RPMh clock and per-resource "
            "regulator bindings into the pinned Android common 5.10 kernel."
        )
    )
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    clock_path = gki / "drivers/clk/qcom/clk-rpmh.c"
    regulator_makefile_path = gki / "drivers/regulator/Makefile"
    downstream_path = gki / "drivers/regulator/a52-rpmh-regulator-downstream.c"
    if not clock_path.is_file() or not regulator_makefile_path.is_file():
        raise SystemExit("pinned RPMh clock driver or regulator Makefile is missing")
    if downstream_path.exists():
        raise SystemExit("downstream RPMh compatibility regulator already exists")

    clock = patch_clock_driver(clock_path.read_text(encoding="utf-8"))
    regulator = patch_downstream_regulator(download_downstream_regulator())
    makefile = patch_regulator_makefile(
        regulator_makefile_path.read_text(encoding="utf-8")
    )

    checks = {
        "lagoon_clock_match": '"qcom,lagoon-rpmh-clk"' in clock,
        "lagoon_qlink_resource": '"qphy.lvl"' in clock,
        "lagoon_baked_qlink_ids": (
            "A52_LAGOON_RPMH_QLINK_CLK = 20" in clock
            and "A52_LAGOON_RPMH_QLINK_CLK_A = 21" in clock
        ),
        "clock_runtime_trace": (
            "A52RPMHCLK PROBE" in clock and "A52RPMHCLK READY" in clock
        ),
        "resource_vrm_match": '"qcom,rpmh-vrm-regulator"' in regulator,
        "resource_arc_match": '"qcom,rpmh-arc-regulator"' in regulator,
        "resource_driver_unique_name": (
            '"a52-rpmh-regulator-downstream"' in regulator
        ),
        "resource_runtime_trace": (
            "A52RPMHREG PROBE" in regulator
            and "A52RPMHREG RESOURCE" in regulator
            and "A52RPMHREG READY" in regulator
        ),
        "modern_cmd_db_aux_api": (
            "cmd_db_read_aux_data(aggr_vreg->resource_name, &len)" in regulator
        ),
        "makefile_object": "a52-rpmh-regulator-downstream.o" in makefile,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("RPMh provider bridge staging audit failed: " + ", ".join(failed))

    clock_path.write_text(clock, encoding="utf-8")
    downstream_path.write_text(regulator, encoding="utf-8")
    regulator_makefile_path.write_text(makefile, encoding="utf-8")

    (output / "patched-clk-rpmh.c").write_text(clock, encoding="utf-8")
    (output / "a52-rpmh-regulator-downstream.c").write_text(
        regulator, encoding="utf-8"
    )
    (output / "patched-regulator-Makefile").write_text(makefile, encoding="utf-8")
    (output / "stage-report.json").write_text(
        json.dumps(
            {
                "status": "staged",
                "purpose": (
                    "bind Samsung Lagoon RPMh clock and per-resource regulator "
                    "nodes without aliasing them to incompatible upstream layouts"
                ),
                "downstream_regulator_source": {
                    "repository": "android/kernel/msm",
                    "commit": DOWNSTREAM_REGULATOR_COMMIT,
                    "path": DOWNSTREAM_REGULATOR_PATH,
                    "git_blob_sha1": DOWNSTREAM_REGULATOR_BLOB,
                },
                "lagoon_clock_compatible": "qcom,lagoon-rpmh-clk",
                "lagoon_qlink_ids": {"normal": 20, "active_only": 21},
                "checks": checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        traceback.print_exc()
        raise
