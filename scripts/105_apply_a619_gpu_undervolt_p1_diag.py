#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 A619 GPU UV P1 diagnostics: persistent sysfs status"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    gmu_c = root / "drivers/gpu/msm/kgsl_gmu.c"
    gmu_h = root / "drivers/gpu/msm/kgsl_gmu.h"
    pwr_c = root / "drivers/gpu/msm/kgsl_pwrctrl.c"

    gmu_text = gmu_c.read_text()
    hdr_text = gmu_h.read_text()
    pwr_text = pwr_c.read_text()

    if MARKER in gmu_text:
        print(f"[already] {gmu_c}: {MARKER}")
        return

    if "A52 A619 GPU UV P1: top OPP one ARC corner" not in gmu_text:
        raise SystemExit("P1 undervolt marker missing; apply script 104 first")

    diag_anchor = """/*
 * A52 A619 GPU UV P1: top OPP one ARC corner
"""
    diag_block = r'''/*
 * A52 A619 GPU UV P1 diagnostics: persistent sysfs status
 *
 * These values are written only during RPMh vote-table construction and are
 * exposed read-only through KGSL sysfs. They do not change clocks, limits,
 * governors, thermal behavior, Hyper, or the undervolt itself.
 */
static unsigned int a52_gpu_uv_p1_applied;
static unsigned int a52_gpu_uv_p1_top_hz;
static unsigned int a52_gpu_uv_p1_request;
static unsigned int a52_gpu_uv_p1_resolved;
static unsigned int a52_gpu_uv_p1_selected;
static unsigned int a52_gpu_uv_p1_arc_from;
static unsigned int a52_gpu_uv_p1_arc_to;

ssize_t a52_gpu_uv_p1_status(char *buf, size_t size)
{
	return scnprintf(buf, size,
		"applied=%u top_hz=%u request=%u resolved=%u selected=%u arc_from=%u arc_to=%u\n",
		a52_gpu_uv_p1_applied, a52_gpu_uv_p1_top_hz,
		a52_gpu_uv_p1_request, a52_gpu_uv_p1_resolved,
		a52_gpu_uv_p1_selected, a52_gpu_uv_p1_arc_from,
		a52_gpu_uv_p1_arc_to);
}

''' + diag_anchor
    gmu_text = replace_once(gmu_text, diag_anchor, diag_block, "diagnostic block insertion")

    old = """	requested = vlvl_tbl[top];

	for (k = 0; k < gfx_arc->num; k++) {
"""
    new = """	requested = vlvl_tbl[top];
	a52_gpu_uv_p1_top_hz = freq_tbl[top];
	a52_gpu_uv_p1_request = requested;

	for (k = 0; k < gfx_arc->num; k++) {
"""
    gmu_text = replace_once(gmu_text, old, new, "request diagnostics")

    old = """		resolved = gfx_arc->val[k];

		/*
"""
    new = """		resolved = gfx_arc->val[k];
		a52_gpu_uv_p1_resolved = resolved;
		a52_gpu_uv_p1_arc_from = k;

		/*
"""
    gmu_text = replace_once(gmu_text, old, new, "resolved diagnostics")

    old = """		lowered = gfx_arc->val[k - 1];
		vlvl_tbl[top] = lowered;

		dev_info"""
    new = """		lowered = gfx_arc->val[k - 1];
		vlvl_tbl[top] = lowered;
		a52_gpu_uv_p1_selected = lowered;
		a52_gpu_uv_p1_arc_to = k - 1;
		a52_gpu_uv_p1_applied = 1;

		dev_info"""
    gmu_text = replace_once(gmu_text, old, new, "applied diagnostics")

    hdr_anchor = """int gmu_cache_finalize(struct kgsl_device *device);

#endif /* __KGSL_GMU_H */
"""
    hdr_new = """int gmu_cache_finalize(struct kgsl_device *device);

/* A52 A619 GPU UV P1 diagnostics: read-only KGSL sysfs status */
ssize_t a52_gpu_uv_p1_status(char *buf, size_t size);

#endif /* __KGSL_GMU_H */
"""
    hdr_text = replace_once(hdr_text, hdr_anchor, hdr_new, "header declaration")

    include_anchor = """#include "kgsl_device.h"
#include "kgsl_pwrscale.h"
"""
    include_new = """#include "kgsl_device.h"
#include "kgsl_gmu.h"
#include "kgsl_pwrscale.h"
"""
    pwr_text = replace_once(pwr_text, include_anchor, include_new, "kgsl_gmu include")

    attr_anchor = """static DEVICE_ATTR_RO(temp);
static DEVICE_ATTR_RW(gpuclk);
"""
    attr_new = r'''static ssize_t a52_gpu_uv_p1_show(struct device *dev,
			struct device_attribute *attr, char *buf)
{
	return a52_gpu_uv_p1_status(buf, PAGE_SIZE);
}

static DEVICE_ATTR_RO(a52_gpu_uv_p1);
static DEVICE_ATTR_RO(temp);
static DEVICE_ATTR_RW(gpuclk);
'''
    pwr_text = replace_once(pwr_text, attr_anchor, attr_new, "sysfs show function")

    list_anchor = """static const struct attribute *pwrctrl_attr_list[] = {
	&dev_attr_gpuclk.attr,
"""
    list_new = """static const struct attribute *pwrctrl_attr_list[] = {
	&dev_attr_a52_gpu_uv_p1.attr,
	&dev_attr_gpuclk.attr,
"""
    pwr_text = replace_once(pwr_text, list_anchor, list_new, "sysfs attribute list")

    gmu_c.write_text(gmu_text)
    gmu_h.write_text(hdr_text)
    pwr_c.write_text(pwr_text)

    print("[patched] persistent read-only A52 GPU UV P1 diagnostics at /sys/class/kgsl/kgsl-3d0/a52_gpu_uv_p1")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    patch(root)


if __name__ == "__main__":
    main()
