#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52 A619 GPU UV P1: top OPP one ARC corner"
TARGET = Path("drivers/gpu/msm/kgsl_gmu.c")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch(root: Path) -> None:
    path = root / TARGET
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: {MARKER}")
        return

    helper_anchor = """/*
 * rpmh_arc_votes_init() - initialized GX RPMh votes needed for rails
"""

    helper = r'''/*
 * A52 A619 GPU UV P1: top OPP one ARC corner
 *
 * Keep the complete stock frequency table and all governor/thermal behavior.
 * Only the highest A619 GPU OPP is changed. Resolve its stock VLVL against
 * the runtime gfx ARC table and vote exactly one valid hardware corner lower.
 * The dependent MX vote is then rebuilt by setup_volt_dependency_tbl(), so
 * normal GX->MX dependency handling remains intact.
 */
static void a52_a619_uv_p1_adjust_top_opp(struct kgsl_device *device,
		struct gmu_device *gmu, struct rpmh_arc_vals *gfx_arc,
		unsigned int *freq_tbl, u16 *vlvl_tbl, unsigned int num_freqs)
{
	unsigned int i, k, top = 0;
	u16 requested, resolved, lowered;

	if (!adreno_is_a619(ADRENO_DEVICE(device)) || num_freqs < 2)
		return;

	/* Do not rely on table ordering. Find the highest real GPU frequency. */
	for (i = 1; i < num_freqs; i++) {
		if (freq_tbl[i] > freq_tbl[top])
			top = i;
	}

	if (!freq_tbl[top] || !vlvl_tbl[top] || gfx_arc->num < 2)
		return;

	requested = vlvl_tbl[top];

	for (k = 0; k < gfx_arc->num; k++) {
		if (gfx_arc->val[k] < requested)
			continue;

		resolved = gfx_arc->val[k];

		/*
		 * Never drop to the zero/off corner, and never accept a malformed
		 * non-increasing ARC table.
		 */
		if (k == 0 || !gfx_arc->val[k - 1] ||
				gfx_arc->val[k - 1] >= resolved) {
			dev_info(&gmu->pdev->dev,
				"A52 GPU UV P1: top=%u Hz stock VLVL=%u, no safe lower ARC corner\n",
				freq_tbl[top], requested);
			return;
		}

		lowered = gfx_arc->val[k - 1];
		vlvl_tbl[top] = lowered;

		dev_info(&gmu->pdev->dev,
			"A52 GPU UV P1: top=%u Hz VLVL request=%u resolved=%u -> %u (ARC %u -> %u)\n",
			freq_tbl[top], requested, resolved, lowered, k, k - 1);
		return;
	}

	dev_warn(&gmu->pdev->dev,
		"A52 GPU UV P1: top=%u Hz VLVL=%u has no matching gfx ARC corner\n",
		freq_tbl[top], requested);
}

''' + helper_anchor

    text = replace_once(text, helper_anchor, helper, "UV helper insertion")

    old_return = """	return setup_volt_dependency_tbl(gmu->rpmh_votes.gx_votes, pri_rail,
						sec_rail, vlvl_tbl, num_freqs);
"""

    new_return = """	a52_a619_uv_p1_adjust_top_opp(device, gmu, pri_rail,
		freq_tbl, vlvl_tbl, num_freqs);

	return setup_volt_dependency_tbl(gmu->rpmh_votes.gx_votes, pri_rail,
						sec_rail, vlvl_tbl, num_freqs);
"""

    text = replace_once(text, old_return, new_return, "UV call insertion")
    path.write_text(text)
    print(f"[patched] {path}: highest A619 OPP lowered by one runtime gfx ARC corner")


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    patch(root)


if __name__ == "__main__":
    main()
