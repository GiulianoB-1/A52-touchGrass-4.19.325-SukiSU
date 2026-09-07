#!/usr/bin/env python3
import argparse
from pathlib import Path

MARKER = "A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1"

HELPER = r'''/* A52_PHASE325_TOUCHGRASS_RCG_CURRENT_CONFIG_V1
 * Port the TouchGrass redundant-RCG-update suppression used by clk_byte2_ops
 * and clk_pixel_ops onto GKI's cfg_off-aware register accessors.
 */
static bool clk_rcg2_current_config(struct clk_rcg2 *rcg,
				    const struct freq_tbl *f)
{
	struct clk_hw *hw = &rcg->clkr.hw;
	u32 cfg, mask, new_cfg;
	int index;

	if (rcg->mnd_width) {
		mask = BIT(rcg->mnd_width) - 1;
		regmap_read(rcg->clkr.regmap, RCG_M_OFFSET(rcg), &cfg);
		if ((cfg & mask) != (f->m & mask))
			return false;

		regmap_read(rcg->clkr.regmap, RCG_N_OFFSET(rcg), &cfg);
		if ((cfg & mask) != (~(f->n - f->m) & mask))
			return false;
	}

	mask = (BIT(rcg->hid_width) - 1) | CFG_SRC_SEL_MASK;

	index = qcom_find_src_index(hw, rcg->parent_map, f->src);

	new_cfg = ((f->pre_div << CFG_SRC_DIV_SHIFT) |
		(rcg->parent_map[index].cfg << CFG_SRC_SEL_SHIFT)) & mask;

	regmap_read(rcg->clkr.regmap, RCG_CFG_OFFSET(rcg), &cfg);

	if (new_cfg != (cfg & mask))
		return false;

	return true;
}

'''


def get_function(text: str, name: str):
    needle = name + "("
    pos = text.find(needle)
    if pos < 0:
        raise SystemExit(f"Phase325: missing function {name}")
    start = text.rfind("\n", 0, pos) + 1
    brace = text.find("{", pos)
    semi = text.find(";", pos)
    if brace < 0 or (semi >= 0 and semi < brace):
        raise SystemExit(f"Phase325: first occurrence of {name} is not a definition")
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1, text[start:i + 1]
    raise SystemExit(f"Phase325: unterminated function {name}")


def replace_in_function(text: str, name: str, old: str, new: str) -> str:
    start, end, body = get_function(text, name)
    if body.count(old) != 1:
        raise SystemExit(f"Phase325: {name} anchor count {body.count(old)}")
    body2 = body.replace(old, new, 1)
    return text[:start] + body2 + text[end:]


def validate_baseline(text: str):
    if MARKER in text:
        raise SystemExit("Phase325: marker already present")
    if "static bool clk_rcg2_current_config(" in text:
        raise SystemExit("Phase325: current-config helper already present")
    get_function(text, "clk_byte2_set_rate")
    get_function(text, "clk_pixel_set_rate")
    anchor = (
        "static int __clk_rcg2_configure(struct clk_rcg2 *rcg, "
        "const struct freq_tbl *f)\n"
    )
    if text.count(anchor) != 1:
        raise SystemExit(f"Phase325: helper insertion anchor count {text.count(anchor)}")


def apply(text: str) -> str:
    validate_baseline(text)
    anchor = (
        "static int __clk_rcg2_configure(struct clk_rcg2 *rcg, "
        "const struct freq_tbl *f)\n"
    )
    out = text.replace(anchor, HELPER + anchor, 1)

    byte_old = (
        "\t\tif (cfg == rcg->parent_map[i].cfg) {\n"
        "\t\t\tf.src = rcg->parent_map[i].src;\n"
        "\t\t\treturn clk_rcg2_configure(rcg, &f);\n"
        "\t\t}\n"
    )
    byte_new = (
        "\t\tif (cfg == rcg->parent_map[i].cfg) {\n"
        "\t\t\tf.src = rcg->parent_map[i].src;\n"
        "\t\t\tif (clk_rcg2_current_config(rcg, &f))\n"
        "\t\t\t\treturn 0;\n"
        "\t\t\treturn clk_rcg2_configure(rcg, &f);\n"
        "\t\t}\n"
    )
    out = replace_in_function(out, "clk_byte2_set_rate", byte_old, byte_new)

    pixel_old = (
        "\t\tf.m = frac->num;\n"
        "\t\tf.n = frac->den;\n\n"
        "\t\treturn clk_rcg2_configure(rcg, &f);\n"
    )
    pixel_new = (
        "\t\tf.m = frac->num;\n"
        "\t\tf.n = frac->den;\n\n"
        "\t\tif (clk_rcg2_current_config(rcg, &f))\n"
        "\t\t\treturn 0;\n"
        "\t\treturn clk_rcg2_configure(rcg, &f);\n"
    )
    out = replace_in_function(out, "clk_pixel_set_rate", pixel_old, pixel_new)
    return out


def check(text: str):
    if text.count(MARKER) != 1:
        raise SystemExit("Phase325 check: marker count != 1")
    if text.count("static bool clk_rcg2_current_config(") != 1:
        raise SystemExit("Phase325 check: helper count != 1")
    if text.count("clk_rcg2_current_config(rcg, &f)") != 2:
        raise SystemExit("Phase325 check: guard call count != 2")
    for name in ("clk_byte2_set_rate", "clk_pixel_set_rate"):
        _, _, body = get_function(text, name)
        if body.count("clk_rcg2_current_config(rcg, &f)") != 1:
            raise SystemExit(f"Phase325 check: guard not isolated to {name}")
    print("Phase325 TouchGrass RCG current-config semantic port check: PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    path = Path(args.file)
    text = path.read_text()
    if args.check_only:
        check(text)
        return
    after = apply(text)
    path.write_text(after)
    check(after)
    print("Phase325 TouchGrass RCG current-config semantic port: PASS")


if __name__ == "__main__":
    main()
