#!/usr/bin/env bash
set -Eeuo pipefail

# Phase283C build-only compatibility fix.
# The read-only shared-path tracer calls __clk_is_enabled(), which is declared
# by the common-clock provider header in this kernel lineage. Patch the
# instrumentation generator deterministically, then run the unchanged full
# Golden handoff-complete Phase283B reconstruction/build.
python3 - <<'PY'
from pathlib import Path

p = Path('scripts/283_apply_shared_engine_phy_trace.py')
s = p.read_text()

if '#include <linux/clk-provider.h>' not in s:
    anchor = (
        "    text = replace_one(\n"
        "        text,\n"
        "        'static atomic_t a52_p282_fifo_inflight = ATOMIC_INIT(0);\\n',\n"
    )
    injection = (
        "    text = replace_one(\n"
        "        text,\n"
        "        '#include <linux/clk.h>\\n',\n"
        "        '#include <linux/clk.h>\\n#include <linux/clk-provider.h>\\n',\n"
        "        'Phase283 clock framework introspection include')\n\n"
    )
    if anchor not in s:
        raise SystemExit('Phase283C: shared tracer insertion anchor missing')
    s = s.replace(anchor, injection + anchor, 1)

    validate = "    required_dsi = [\n        MARK,\n"
    if validate not in s:
        raise SystemExit('Phase283C: validation anchor missing')
    s = s.replace(
        validate,
        "    required_dsi = [\n        MARK,\n        '#include <linux/clk-provider.h>',\n",
        1,
    )
    p.write_text(s)

if '#include <linux/clk-provider.h>' not in p.read_text():
    raise SystemExit('Phase283C: clock provider compatibility patch missing')

print('Phase283C clock framework compatibility patch: PASS')
PY

bash scripts/283b_ci_build.sh
