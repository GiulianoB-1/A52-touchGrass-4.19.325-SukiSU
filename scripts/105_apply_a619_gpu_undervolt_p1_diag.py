#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

P1_MARKER = "A52 A619 GPU UV P1 diagnostics: persistent sysfs status"
P2_MARKER = "A52 A619 GPU UV P2: 650 MHz one ARC corner"
P3_MARKER = "A52 A619 GPU UV P3: 565 MHz one ARC corner"


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

    if P3_MARKER in gmu_text:
        print(f"[already] {gmu_c}: {P3_MARKER}")
        return

    if "A52 A619 GPU UV P1: top OPP one ARC corner" not in gmu_text:
        raise SystemExit("P1 undervolt marker missing; apply script 104 first")

    # Preserve the already-validated P1 logic exactly and add the persistent
    # P1 status node if this is a clean build workspace.
    if P1_MARKER not in gmu_text:
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
        gmu_text = replace_once(gmu_text, diag_anchor, diag_block,
                                "P1 diagnostic block insertion")

        old = """	requested = vlvl_tbl[top];

	for (k = 0; k < gfx_arc->num; k++) {
"""
        new = """	requested = vlvl_tbl[top];
	a52_gpu_uv_p1_top_hz = freq_tbl[top];
	a52_gpu_uv_p1_request = requested;

	for (k = 0; k < gfx_arc->num; k++) {
"""
        gmu_text = replace_once(gmu_text, old, new, "P1 request diagnostics")

        old = """		resolved = gfx_arc->val[k];

		/*
"""
        new = """		resolved = gfx_arc->val[k];
		a52_gpu_uv_p1_resolved = resolved;
		a52_gpu_uv_p1_arc_from = k;

		/*
"""
        gmu_text = replace_once(gmu_text, old, new, "P1 resolved diagnostics")

        old = """		lowered = gfx_arc->val[k - 1];
		vlvl_tbl[top] = lowered;

		dev_info"""
        new = """		lowered = gfx_arc->val[k - 1];
		vlvl_tbl[top] = lowered;
		a52_gpu_uv_p1_selected = lowered;
		a52_gpu_uv_p1_arc_to = k - 1;
		a52_gpu_uv_p1_applied = 1;

		dev_info"""
        gmu_text = replace_once(gmu_text, old, new, "P1 applied diagnostics")

        hdr_anchor = """int gmu_cache_finalize(struct kgsl_device *device);

#endif /* __KGSL_GMU_H */
"""
        hdr_new = """int gmu_cache_finalize(struct kgsl_device *device);

/* A52 A619 GPU UV P1 diagnostics: read-only KGSL sysfs status */
ssize_t a52_gpu_uv_p1_status(char *buf, size_t size);

#endif /* __KGSL_GMU_H */
"""
        hdr_text = replace_once(hdr_text, hdr_anchor, hdr_new,
                                "P1 header declaration")

        include_anchor = """#include "kgsl_device.h"
#include "kgsl_pwrscale.h"
"""
        include_new = """#include "kgsl_device.h"
#include "kgsl_gmu.h"
#include "kgsl_pwrscale.h"
"""
        pwr_text = replace_once(pwr_text, include_anchor, include_new,
                                "kgsl_gmu include")

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
        pwr_text = replace_once(pwr_text, attr_anchor, attr_new,
                                "P1 sysfs show function")

        list_anchor = """static const struct attribute *pwrctrl_attr_list[] = {
	&dev_attr_gpuclk.attr,
"""
        list_new = """static const struct attribute *pwrctrl_attr_list[] = {
	&dev_attr_a52_gpu_uv_p1.attr,
	&dev_attr_gpuclk.attr,
"""
        pwr_text = replace_once(pwr_text, list_anchor, list_new,
                                "P1 sysfs attribute list")

    # P2: keep the validated 800 MHz P1 vote untouched and lower only the
    # 650 MHz OPP by exactly one valid runtime gfx ARC corner.
    helper_anchor = """/*
 * rpmh_arc_votes_init() - initialized GX RPMh votes needed for rails
"""
    helper = r'''/*
 * A52 A619 GPU UV P2: 650 MHz one ARC corner
 *
 * P1 remains exactly as validated at 800 MHz. P2 targets only the 650 MHz
 * A619 OPP and lowers its resolved runtime gfx ARC vote by one valid hardware
 * corner. Frequencies, governor, thermal policy, Samsung Hyper and DCVS are
 * unchanged. setup_volt_dependency_tbl() still rebuilds the normal GX->MX
 * dependency table after both P1 and P2 adjustments.
 */
#define A52_GPU_UV_P2_FREQ_HZ 650000000U

static unsigned int a52_gpu_uv_p2_applied;
static unsigned int a52_gpu_uv_p2_freq_hz;
static unsigned int a52_gpu_uv_p2_request;
static unsigned int a52_gpu_uv_p2_resolved;
static unsigned int a52_gpu_uv_p2_selected;
static unsigned int a52_gpu_uv_p2_arc_from;
static unsigned int a52_gpu_uv_p2_arc_to;

ssize_t a52_gpu_uv_p2_status(char *buf, size_t size)
{
	return scnprintf(buf, size,
		"applied=%u freq_hz=%u request=%u resolved=%u selected=%u arc_from=%u arc_to=%u\n",
		a52_gpu_uv_p2_applied, a52_gpu_uv_p2_freq_hz,
		a52_gpu_uv_p2_request, a52_gpu_uv_p2_resolved,
		a52_gpu_uv_p2_selected, a52_gpu_uv_p2_arc_from,
		a52_gpu_uv_p2_arc_to);
}

static void a52_a619_uv_p2_adjust_650_opp(struct kgsl_device *device,
		struct gmu_device *gmu, struct rpmh_arc_vals *gfx_arc,
		unsigned int *freq_tbl, u16 *vlvl_tbl, unsigned int num_freqs)
{
	unsigned int i, k, target = num_freqs;
	u16 requested, resolved, lowered;

	if (!adreno_is_a619(ADRENO_DEVICE(device)) || num_freqs < 2)
		return;

	for (i = 0; i < num_freqs; i++) {
		if (freq_tbl[i] == A52_GPU_UV_P2_FREQ_HZ) {
			target = i;
			break;
		}
	}

	if (target == num_freqs) {
		dev_warn(&gmu->pdev->dev,
			"A52 GPU UV P2: 650 MHz OPP not present, leaving table unchanged\n");
		return;
	}

	if (!vlvl_tbl[target] || gfx_arc->num < 2)
		return;

	requested = vlvl_tbl[target];
	a52_gpu_uv_p2_freq_hz = freq_tbl[target];
	a52_gpu_uv_p2_request = requested;

	for (k = 0; k < gfx_arc->num; k++) {
		if (gfx_arc->val[k] < requested)
			continue;

		resolved = gfx_arc->val[k];
		a52_gpu_uv_p2_resolved = resolved;
		a52_gpu_uv_p2_arc_from = k;

		if (k == 0 || !gfx_arc->val[k - 1] ||
				gfx_arc->val[k - 1] >= resolved) {
			dev_info(&gmu->pdev->dev,
				"A52 GPU UV P2: 650=%u Hz stock VLVL=%u, no safe lower ARC corner\n",
				freq_tbl[target], requested);
			return;
		}

		lowered = gfx_arc->val[k - 1];
		vlvl_tbl[target] = lowered;
		a52_gpu_uv_p2_selected = lowered;
		a52_gpu_uv_p2_arc_to = k - 1;
		a52_gpu_uv_p2_applied = 1;

		dev_info(&gmu->pdev->dev,
			"A52 GPU UV P2: 650=%u Hz VLVL request=%u resolved=%u -> %u (ARC %u -> %u)\n",
			freq_tbl[target], requested, resolved, lowered, k, k - 1);
		return;
	}

	dev_warn(&gmu->pdev->dev,
		"A52 GPU UV P2: 650=%u Hz VLVL=%u has no matching gfx ARC corner\n",
		freq_tbl[target], requested);
}

''' + helper_anchor
    gmu_text = replace_once(gmu_text, helper_anchor, helper,
                            "P2 helper insertion")

    call_anchor = """	a52_a619_uv_p1_adjust_top_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);

	return setup_volt_dependency_tbl(gmu->rpmh_votes.gx_votes, pri_rail,
"""
    call_new = """	a52_a619_uv_p1_adjust_top_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);
	a52_a619_uv_p2_adjust_650_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);

	return setup_volt_dependency_tbl(gmu->rpmh_votes.gx_votes, pri_rail,
"""
    gmu_text = replace_once(gmu_text, call_anchor, call_new,
                            "P2 call insertion")

    hdr_anchor = """ssize_t a52_gpu_uv_p1_status(char *buf, size_t size);

#endif /* __KGSL_GMU_H */
"""
    hdr_new = """ssize_t a52_gpu_uv_p1_status(char *buf, size_t size);
/* A52 A619 GPU UV P2 diagnostics: read-only 650 MHz status */
ssize_t a52_gpu_uv_p2_status(char *buf, size_t size);

#endif /* __KGSL_GMU_H */
"""
    hdr_text = replace_once(hdr_text, hdr_anchor, hdr_new,
                            "P2 header declaration")

    attr_anchor = """static DEVICE_ATTR_RO(a52_gpu_uv_p1);
static DEVICE_ATTR_RO(temp);
"""
    attr_new = r'''static DEVICE_ATTR_RO(a52_gpu_uv_p1);

static ssize_t a52_gpu_uv_p2_show(struct device *dev,
			struct device_attribute *attr, char *buf)
{
	return a52_gpu_uv_p2_status(buf, PAGE_SIZE);
}

static DEVICE_ATTR_RO(a52_gpu_uv_p2);
static DEVICE_ATTR_RO(temp);
'''
    pwr_text = replace_once(pwr_text, attr_anchor, attr_new,
                            "P2 sysfs show function")

    list_anchor = """	&dev_attr_a52_gpu_uv_p1.attr,
	&dev_attr_gpuclk.attr,
"""
    list_new = """	&dev_attr_a52_gpu_uv_p1.attr,
	&dev_attr_a52_gpu_uv_p2.attr,
	&dev_attr_gpuclk.attr,
"""
    pwr_text = replace_once(pwr_text, list_anchor, list_new,
                            "P2 sysfs attribute list")

    # P3: preserve validated P1 and P2 exactly; lower only the 565 MHz OPP
    # by one valid runtime gfx ARC corner.
    helper_anchor = """/*
 * rpmh_arc_votes_init() - initialized GX RPMh votes needed for rails
"""
    helper = r'''/*
 * A52 A619 GPU UV P3: 565 MHz one ARC corner
 *
 * P1 (800 MHz) and P2 (650 MHz) remain unchanged. P3 targets only the
 * 565 MHz A619 OPP and lowers its resolved runtime gfx ARC vote by one valid
 * hardware corner. Frequencies, governor, thermal policy, Samsung Hyper and
 * DCVS remain untouched. Normal GX->MX dependency handling is retained.
 */
#define A52_GPU_UV_P3_FREQ_HZ 565000000U

static unsigned int a52_gpu_uv_p3_applied;
static unsigned int a52_gpu_uv_p3_freq_hz;
static unsigned int a52_gpu_uv_p3_request;
static unsigned int a52_gpu_uv_p3_resolved;
static unsigned int a52_gpu_uv_p3_selected;
static unsigned int a52_gpu_uv_p3_arc_from;
static unsigned int a52_gpu_uv_p3_arc_to;

ssize_t a52_gpu_uv_p3_status(char *buf, size_t size)
{
	return scnprintf(buf, size,
		"applied=%u freq_hz=%u request=%u resolved=%u selected=%u arc_from=%u arc_to=%u\n",
		a52_gpu_uv_p3_applied, a52_gpu_uv_p3_freq_hz,
		a52_gpu_uv_p3_request, a52_gpu_uv_p3_resolved,
		a52_gpu_uv_p3_selected, a52_gpu_uv_p3_arc_from,
		a52_gpu_uv_p3_arc_to);
}

static void a52_a619_uv_p3_adjust_565_opp(struct kgsl_device *device,
		struct gmu_device *gmu, struct rpmh_arc_vals *gfx_arc,
		unsigned int *freq_tbl, u16 *vlvl_tbl, unsigned int num_freqs)
{
	unsigned int i, k, target = num_freqs;
	u16 requested, resolved, lowered;

	if (!adreno_is_a619(ADRENO_DEVICE(device)) || num_freqs < 2)
		return;

	for (i = 0; i < num_freqs; i++) {
		if (freq_tbl[i] == A52_GPU_UV_P3_FREQ_HZ) {
			target = i;
			break;
		}
	}

	if (target == num_freqs) {
		dev_warn(&gmu->pdev->dev,
			"A52 GPU UV P3: 565 MHz OPP not present, leaving table unchanged\n");
		return;
	}

	if (!vlvl_tbl[target] || gfx_arc->num < 2)
		return;

	requested = vlvl_tbl[target];
	a52_gpu_uv_p3_freq_hz = freq_tbl[target];
	a52_gpu_uv_p3_request = requested;

	for (k = 0; k < gfx_arc->num; k++) {
		if (gfx_arc->val[k] < requested)
			continue;

		resolved = gfx_arc->val[k];
		a52_gpu_uv_p3_resolved = resolved;
		a52_gpu_uv_p3_arc_from = k;

		if (k == 0 || !gfx_arc->val[k - 1] ||
				gfx_arc->val[k - 1] >= resolved) {
			dev_info(&gmu->pdev->dev,
				"A52 GPU UV P3: 565=%u Hz stock VLVL=%u, no safe lower ARC corner\n",
				freq_tbl[target], requested);
			return;
		}

		lowered = gfx_arc->val[k - 1];
		vlvl_tbl[target] = lowered;
		a52_gpu_uv_p3_selected = lowered;
		a52_gpu_uv_p3_arc_to = k - 1;
		a52_gpu_uv_p3_applied = 1;

		dev_info(&gmu->pdev->dev,
			"A52 GPU UV P3: 565=%u Hz VLVL request=%u resolved=%u -> %u (ARC %u -> %u)\n",
			freq_tbl[target], requested, resolved, lowered, k, k - 1);
		return;
	}

	dev_warn(&gmu->pdev->dev,
		"A52 GPU UV P3: 565=%u Hz VLVL=%u has no matching gfx ARC corner\n",
		freq_tbl[target], requested);
}

''' + helper_anchor
    gmu_text = replace_once(gmu_text, helper_anchor, helper,
                            "P3 helper insertion")

    call_anchor = """	a52_a619_uv_p1_adjust_top_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);
	a52_a619_uv_p2_adjust_650_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);

	return setup_volt_dependency_tbl(gmu->rpmh_votes.gx_votes, pri_rail,
"""
    call_new = """	a52_a619_uv_p1_adjust_top_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);
	a52_a619_uv_p2_adjust_650_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);
	a52_a619_uv_p3_adjust_565_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);

	return setup_volt_dependency_tbl(gmu->rpmh_votes.gx_votes, pri_rail,
"""
    gmu_text = replace_once(gmu_text, call_anchor, call_new,
                            "P3 call insertion")

    hdr_anchor = """ssize_t a52_gpu_uv_p2_status(char *buf, size_t size);

#endif /* __KGSL_GMU_H */
"""
    hdr_new = """ssize_t a52_gpu_uv_p2_status(char *buf, size_t size);
/* A52 A619 GPU UV P3 diagnostics: read-only 565 MHz status */
ssize_t a52_gpu_uv_p3_status(char *buf, size_t size);

#endif /* __KGSL_GMU_H */
"""
    hdr_text = replace_once(hdr_text, hdr_anchor, hdr_new,
                            "P3 header declaration")

    attr_anchor = """static DEVICE_ATTR_RO(a52_gpu_uv_p2);
static DEVICE_ATTR_RO(temp);
"""
    attr_new = r'''static DEVICE_ATTR_RO(a52_gpu_uv_p2);

static ssize_t a52_gpu_uv_p3_show(struct device *dev,
			struct device_attribute *attr, char *buf)
{
	return a52_gpu_uv_p3_status(buf, PAGE_SIZE);
}

static DEVICE_ATTR_RO(a52_gpu_uv_p3);
static DEVICE_ATTR_RO(temp);
'''
    pwr_text = replace_once(pwr_text, attr_anchor, attr_new,
                            "P3 sysfs show function")

    list_anchor = """	&dev_attr_a52_gpu_uv_p2.attr,
	&dev_attr_gpuclk.attr,
"""
    list_new = """	&dev_attr_a52_gpu_uv_p2.attr,
	&dev_attr_a52_gpu_uv_p3.attr,
	&dev_attr_gpuclk.attr,
"""
    pwr_text = replace_once(pwr_text, list_anchor, list_new,
                            "P3 sysfs attribute list")

    gmu_c.write_text(gmu_text)
    gmu_h.write_text(hdr_text)
    pwr_c.write_text(pwr_text)

    print("[patched] P1/P2 retained; A619 565 MHz P3 lowered one runtime gfx ARC corner")
    print("[patched] read-only status: /sys/class/kgsl/kgsl-3d0/a52_gpu_uv_p1")
    print("[patched] read-only status: /sys/class/kgsl/kgsl-3d0/a52_gpu_uv_p2")
    print("[patched] read-only status: /sys/class/kgsl/kgsl-3d0/a52_gpu_uv_p3")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    patch(root)


if __name__ == "__main__":
    main()
