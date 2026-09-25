#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
vidc = kernel / "techpack/video/msm/vidc/msm_v4l2_vidc.c"
dd = kernel / "drivers/base/dd.c"

for path in (vidc, dd):
    if not path.is_file():
        raise SystemExit(f"missing required source: {path}")

vidc_s = vidc.read_text()
dd_s = dd.read_text()

vidc_marker = "A52 P162: restore synchronous VIDC probe ordering"
census_marker = "A52 P162: extend full async census to 12 seconds"

if vidc_marker not in vidc_s:
    old = '''\t.driver = {
\t\t.name = "msm_vidc_v4l2",
\t\t.of_match_table = msm_vidc_dt_match,
'''
    new = '''\t.driver = {
\t\t.name = "msm_vidc_v4l2",
\t\t/*
\t\t * A52 P162: restore synchronous VIDC probe ordering.
\t\t *
\t\t * P161's full 2048-entry census proved msm_vidc_v4l2 is the
\t\t * only remaining FAIL under P153 global async-default, returning
\t\t * -EINVAL on the main qcom,msm-vidc device.  This downstream
\t\t * driver was written for the stock synchronous probe contract and
\t\t * also populates/probes its own context-bank/bus child devices.
\t\t *
\t\t * Restore only this driver's original ordering semantics instead
\t\t * of broadening the global synchronous exception list.
\t\t */
\t\t.probe_type = PROBE_FORCE_SYNCHRONOUS,
\t\t.of_match_table = msm_vidc_dt_match,
'''
    if vidc_s.count(old) != 1:
        raise SystemExit(f"VIDC platform_driver anchor count={vidc_s.count(old)}")
    vidc_s = vidc_s.replace(old, new, 1)

if census_marker not in dd_s:
    old = "#define A52_PROBE_CENSUS_FINAL_MS 8000"
    new = """/*
 * A52 P162: extend full async census to 12 seconds.
 *
 * P161 had one legitimate probe still RUNNING at the 8-second checkpoint:
 * kona-asoc-snd.  Do not classify it as broken merely because the diagnostic
 * reboot cut it off.  Keep 2-second checkpoints but allow 10s and 12s samples
 * before the intentional recovery reboot.
 */
#define A52_PROBE_CENSUS_FINAL_MS 12000"""
    if dd_s.count(old) != 1:
        raise SystemExit(f"P162 final-census anchor count={dd_s.count(old)}")
    dd_s = dd_s.replace(old, new, 1)

for token in (
    vidc_marker,
    ".probe_type = PROBE_FORCE_SYNCHRONOUS",
    '.name = "msm_vidc_v4l2"',
):
    if token not in vidc_s:
        raise SystemExit(f"P162 VIDC audit missing: {token}")

for token in (
    census_marker,
    "#define A52_PROBE_CENSUS_CHECKPOINT_MS 2000",
    "#define A52_PROBE_CENSUS_FINAL_MS 12000",
    "#define A52_PROBE_CENSUS_MAX 2048",
):
    if token not in dd_s:
        raise SystemExit(f"P162 census audit missing: {token}")

if "#define A52_PROBE_CENSUS_FINAL_MS 8000" in dd_s:
    raise SystemExit("P162 old 8-second final census still present")

vidc.write_text(vidc_s)
dd.write_text(dd_s)

print("A52 P162 VIDC ordering + 12-second full census applied")
print("  msm_vidc_v4l2: PROBE_FORCE_SYNCHRONOUS")
print("  all unrelated drivers: retain P153 global async-default")
print("  census capacity: 2048")
print("  checkpoints: 2/4/6/8/10/12 seconds")
print("  final intentional recovery reboot: 12 seconds")
