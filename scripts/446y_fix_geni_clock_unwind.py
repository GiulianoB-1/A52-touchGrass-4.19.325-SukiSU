#!/usr/bin/env python3
"""Fix Clang -Wdangling-else on 446Y GENI clock error unwind only.

The previous P446Y transform nests an inner if/else beneath an unbraced
outer if (ret). Enclose only the outer error block, preserving behavior.
The exact one-instance anchor and post-patch contract are mandatory.
"""
import argparse
from pathlib import Path

REL = Path('drivers/soc/qcom/qcom-geni-se.c')
OLD = (
    '\tret = clk_prepare_enable(se->clk);\n'
    '\tif (ret)\n'
    '\t\tif (se->a52_legacy_ahb)\n'
    '\t\t\tclk_bulk_disable_unprepare(2, se->a52_legacy_ahb_clks);\n'
    '\t\telse\n'
    '\t\t\tclk_bulk_disable_unprepare(ARRAY_SIZE(wrapper->ahb_clks),\n'
    '\t\t\t\t\t\t\twrapper->ahb_clks);\n'
    '\treturn ret;'
)
NEW = OLD.replace('\tif (ret)\n', '\tif (ret) {\n', 1).replace(
    '\treturn ret;', '\t}\n\treturn ret;', 1)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--check-only', action='store_true')
    a = p.parse_args()
    dest = a.root / REL
    source = dest.read_text()
    if a.check_only:
        if source.count(NEW) != 1 or OLD in source:
            raise SystemExit('P446Y GENI clock unwind braces contract FAIL')
    else:
        if source.count(OLD) != 1 or NEW in source:
            raise SystemExit('P446Y GENI clock unwind anchor changed: fail closed')
        source = source.replace(OLD, NEW, 1)
        dest.write_text(source)
    print('P446Y GENI CLOCK_UNWIND BRACES PASS: outer if(ret) scoped, both AHB unwind paths intact')

if __name__ == '__main__':
    main()
