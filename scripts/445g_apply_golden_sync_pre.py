#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE445G_GOLDEN_SYNC_PRE_V1"

def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase445G: {label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)

def main() -> None:
    ap = argparse.ArgumentParser(description="Apply Phase445G Golden synchronous PRE on top of Phase444")
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    p444 = root / "techpack/display/msm/a52_phase444.c"
    disp = root / "techpack/display/msm/dsi/dsi_display.c"
    for p in (p444, disp):
        if not p.is_file():
            raise SystemExit(f"missing {p}")

    a = p444.read_text()
    d = disp.read_text()

    if MARK in a and MARK in d:
        print("Phase445G: source audit PASS")
        return
    if args.check_only:
        raise SystemExit("Phase445G: markers missing")

    a = replace_once(
        a,
        "static void p444_checkpoint(void);\nstatic void p444_predeep_workfn(struct work_struct *work);",
        """static void p444_checkpoint(void);

/* A52_PHASE445G_GOLDEN_SYNC_PRE_V1
 * Matched synchronous PRE for the continuous-splash enable path.
 * Reuses the proven Phase444 deep capture and recorder; no recovery gate
 * or R1/R2 logic is compiled into the Golden twin.
 */
void a52_p444_predeep_now(struct dsi_ctrl *ctrl)
{
    if (!ctrl || atomic_cmpxchg(&p444_predeep_once, 0, 1) != 0)
        return;
    if (atomic_read(&p444_state) != 0)
        return;

    p444_ctrl = ctrl;
    if (!p444_phy)
        p444_phy = ioremap(0x0ae94000ULL, 0x1000U);

    p444_hdr()->predeep_ns = ktime_get_ns();
    p444_deep(P444_STAGE_PRE_DEEP, ctrl);
    p444_checkpoint();
#if P444_KIND == 2
    pr_info("P445G PRENOW done sec=%u used=%u crc=%08x\\n",
        p444_hdr()->section_count, p444_hdr()->used_bytes,
        p444_hdr()->hdr_crc32);
#endif
}
EXPORT_SYMBOL_GPL(a52_p444_predeep_now);

static void p444_predeep_workfn(struct work_struct *work);""",
        "Phase444 recorder PRE insertion",
    )

    d = replace_once(
        d,
        "extern void a52_p444_schedule_predeep(struct dsi_ctrl *ctrl);",
        """extern void a52_p444_schedule_predeep(struct dsi_ctrl *ctrl);
/* A52_PHASE445G_GOLDEN_SYNC_PRE_V1 */
extern void a52_p444_predeep_now(struct dsi_ctrl *ctrl);""",
        "display extern",
    )

    d = replace_once(
        d,
        """		mutex_lock(&display->display_lock);

		dsi_panel_enable(display->panel);""",
        """		mutex_lock(&display->display_lock);

		/* A52_PHASE445G_GOLDEN_SYNC_PRE_V1:
		 * splash cleanup has completed; capture the exact inherited
		 * controller/PHY/SDE state immediately before Samsung on_pre/F0.
		 */
		a52_p444_predeep_now(display->ctrl[0].ctrl);

		dsi_panel_enable(display->panel);""",
        "continuous-splash PRE call",
    )

    p444.write_text(a)
    disp.write_text(d)

    # Strict postconditions.
    aa = p444.read_text()
    dd = disp.read_text()
    if aa.count(MARK) != 1:
        raise SystemExit("Phase445G: recorder marker count mismatch")
    if dd.count(MARK) != 2:
        raise SystemExit("Phase445G: display marker count mismatch")
    if dd.count("a52_p444_predeep_now(display->ctrl[0].ctrl);") != 1:
        raise SystemExit("Phase445G: PRE call count mismatch")
    print("Phase445G: synchronous Golden PRE applied")

if __name__ == "__main__":
    main()
