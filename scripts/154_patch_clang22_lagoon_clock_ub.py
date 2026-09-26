#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 154_patch_clang22_lagoon_clock_ub.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
gpu = root / "drivers/clk/qcom/gpucc-lagoon.c"
clk = root / "drivers/clk/clk.c"

g = gpu.read_text()
old_gx = "static DEFINE_VDD_REGULATORS(vdd_gx, VDD_GX_NUM, 1, vdd_corner);"
new_gx = "static DEFINE_VDD_REGULATORS(vdd_gx, VDD_GX_NUM, 1, vdd_gx_corner);"
old_mx = "static DEFINE_VDD_REGULATORS(vdd_mx, VDD_NUM, 1, vdd_gx_corner);"
new_mx = "static DEFINE_VDD_REGULATORS(vdd_mx, VDD_NUM, 1, vdd_corner);"

for old, new, label in (
    (old_gx, new_gx, "vdd_gx"),
    (old_mx, new_mx, "vdd_mx"),
):
    if new in g:
        continue
    if g.count(old) != 1:
        raise SystemExit(f"{label}: expected one old initializer, found {g.count(old)}")
    g = g.replace(old, new, 1)

gpu.write_text(g)

c = clk.read_text()

old_devm_clk = """	clk_populate_clock_opp_table(dev->of_node, hw);
	return clk;
}
EXPORT_SYMBOL_GPL(devm_clk_register);"""
new_devm_clk = """	/*
	 * Only inspect OPP metadata for a successfully registered clock.
	 * clk_register() may free hw->core on failure while leaving hw->core
	 * stale, so unconditional OPP population is a use-after-free.
	 */
	if (!IS_ERR(clk))
		clk_populate_clock_opp_table(dev->of_node, hw);
	return clk;
}
EXPORT_SYMBOL_GPL(devm_clk_register);"""

old_devm_hw = """	clk_populate_clock_opp_table(dev->of_node, hw);
	return ret;
}
EXPORT_SYMBOL_GPL(devm_clk_hw_register);"""
new_devm_hw = """	/*
	 * clk_hw_register() failure can leave hw->core pointing at released
	 * clock-core storage. Do not dereference it from OPP population.
	 */
	if (!ret)
		clk_populate_clock_opp_table(dev->of_node, hw);
	return ret;
}
EXPORT_SYMBOL_GPL(devm_clk_hw_register);"""

for old, new, label in (
    (old_devm_clk, new_devm_clk, "devm_clk_register"),
    (old_devm_hw, new_devm_hw, "devm_clk_hw_register"),
):
    if new in c:
        continue
    if c.count(old) != 1:
        raise SystemExit(f"{label}: expected one OPP tail, found {c.count(old)}")
    c = c.replace(old, new, 1)

clk.write_text(c)

# Strong textual audit.
g = gpu.read_text()
c = clk.read_text()
checks = {
    "gx_uses_9_level_table": new_gx in g,
    "mx_uses_7_level_table": new_mx in g,
    "gx_old_oob_mapping_absent": old_gx not in g,
    "mx_old_swapped_mapping_absent": old_mx not in g,
    "devm_clk_opp_success_only": "if (!IS_ERR(clk))\n\t\tclk_populate_clock_opp_table(dev->of_node, hw);" in c,
    "devm_hw_opp_success_only": "if (!ret)\n\t\tclk_populate_clock_opp_table(dev->of_node, hw);" in c,
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("P154 audit failed: " + ", ".join(failed))

print("[patched] gpucc-lagoon vdd_gx -> vdd_gx_corner (9 levels)")
print("[patched] gpucc-lagoon vdd_mx -> vdd_corner (7 levels)")
print("[patched] devm clk OPP population only after successful registration")
