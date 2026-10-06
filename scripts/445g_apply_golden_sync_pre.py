#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = "A52_PHASE445G_GOLDEN_SYNC_PRE_V2"
OLD_MARK = "A52_PHASE445G_GOLDEN_SYNC_PRE_V1"

def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase445G: {label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)

def append_once(path: Path, marker: str, body: str) -> None:
    s = path.read_text()
    if marker in s:
        return
    path.write_text(s + "\n" + body.rstrip() + "\n")

def patch_regulator_core(root: Path) -> None:
    p = root / "drivers/regulator/core.c"
    if not p.is_file():
        raise SystemExit(f"missing {p}")
    append_once(p, "A52_PHASE445G_REGULATOR_SNAPSHOT_V1", r'''
/* A52_PHASE445G_REGULATOR_SNAPSHOT_V1
 * Passive snapshot only. READ_ONCE works with both the downstream 4.19
 * regulator mutex layout and the 5.10 ww_mutex layout used by GKI.
 */
int a52_p445g_regulator_state(struct regulator *reg, int *enabled,
        int *use_count, int *voltage)
{
    struct regulator_dev *rdev;

    if (!reg || !reg->rdev)
        return -ENODEV;

    rdev = reg->rdev;
    if (enabled)
        *enabled = regulator_is_enabled(reg);
    if (voltage)
        *voltage = regulator_get_voltage(reg);
    if (use_count)
        *use_count = (int)READ_ONCE(rdev->use_count);
    return 0;
}
EXPORT_SYMBOL_GPL(a52_p445g_regulator_state);
''')

def patch_refgen(root: Path) -> None:
    p = root / "drivers/regulator/refgen.c"
    if not p.is_file():
        raise SystemExit(f"missing {p}")
    s = p.read_text()
    if "A52_PHASE445G_REFGEN_SNAPSHOT_V1" in s:
        return

    st = s.find("struct refgen {")
    en = s.find("\n};", st)
    if st < 0 or en < 0:
        raise SystemExit("Phase445G: refgen struct anchor missing")
    en += len("\n};")
    s = s[:en] + r'''

/* A52_PHASE445G_REFGEN_SNAPSHOT_V1 */
static struct refgen *a52_p445g_refgen;
''' + s[en:]

    probe = s.find("static int refgen_probe(")
    if probe < 0:
        raise SystemExit("Phase445G: refgen_probe missing")
    brace = s.find("{", probe)
    depth = 0
    end = -1
    for i in range(brace, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise SystemExit("Phase445G: refgen_probe end missing")
    ret = s.rfind("\treturn 0;", brace, end)
    if ret < 0:
        ret = s.rfind("return 0;", brace, end)
    if ret < 0:
        raise SystemExit("Phase445G: refgen successful return missing")

    # Save only the display REFGEN resource we are comparing (0x088e7000).
    hook = (
        "\tif (res && res->start == 0x088e7000ULL)\n"
        "\t\ta52_p445g_refgen = vreg;\n\n"
    )
    s = s[:ret] + hook + s[ret:]

    s += r'''

int a52_p445g_refgen_snapshot(u32 *registered, u32 *enabled,
        u32 *use_count, u32 *pwrdwn)
{
    struct refgen *v = a52_p445g_refgen;
    u32 val;

    if (registered)
        *registered = !!v;
    if (!v || !v->rdev || !v->addr)
        return -ENODEV;

    val = readl_relaxed(v->addr + REFGEN_REG_PWRDWN_CTRL5);
    if (enabled)
        *enabled = !!(val & REFGEN_PWRDWN_CTRL5_MASK);
    if (use_count)
        *use_count = READ_ONCE(v->rdev->use_count);
    if (pwrdwn)
        *pwrdwn = val;
    return 0;
}
EXPORT_SYMBOL_GPL(a52_p445g_refgen_snapshot);
'''
    p.write_text(s)

def main() -> None:
    ap = argparse.ArgumentParser(description="Apply Phase445G Golden matched PRE + power snapshot on top of Phase444")
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
        if args.check_only:
            for token in ("P444_MAX_SECTIONS 120U", "dropped_sections",
                          "POWER_PRE", "SUPPLY_PRE",
                          "a52_p445g_refgen_snapshot"):
                if token not in a:
                    raise SystemExit(f"Phase445G: audit missing {token}")
        print("Phase445G: source audit PASS")
        return

    if args.check_only:
        raise SystemExit("Phase445G: V2 markers missing")

    # Upgrade an older V1 application in-place if a cached source ever contains it.
    if OLD_MARK in a or OLD_MARK in d:
        raise SystemExit("Phase445G: source already contains obsolete V1 patch; start from clean Phase444 source")

    # The Golden capture proved 68 sections are attempted. Give it the same
    # capacity class as FCOMP445 and count any future drops explicitly.
    a = replace_once(a, "#define P444_HEADER_BYTES SZ_4K",
                     "#define P444_HEADER_BYTES SZ_8K", "header size")
    a = replace_once(a, "#define P444_MAX_SECTIONS 56U",
                     "#define P444_MAX_SECTIONS 120U", "section capacity")
    a = replace_once(
        a,
        "    u8 reserved[128];\n    struct p444_section sec[P444_MAX_SECTIONS];",
        "    u32 dropped_sections, section_capacity;\n"
        "    u8 reserved[120];\n"
        "    struct p444_section sec[P444_MAX_SECTIONS];",
        "drop fields",
    )
    a = replace_once(
        a,
        "static atomic_t p444_sections = ATOMIC_INIT(0);",
        "static atomic_t p444_sections = ATOMIC_INIT(0);\n"
        "static atomic_t p444_dropped = ATOMIC_INIT(0);",
        "drop atomic",
    )
    a = replace_once(
        a,
        "    h->section_count = (u32)atomic_read(&p444_sections);\n"
        "    h->used_bytes = (u32)atomic_read(&p444_used);",
        "    h->section_count = (u32)atomic_read(&p444_sections);\n"
        "    h->used_bytes = (u32)atomic_read(&p444_used);\n"
        "    h->dropped_sections = (u32)atomic_read(&p444_dropped);\n"
        "    h->section_capacity = P444_MAX_SECTIONS;",
        "drop sync",
    )
    a = replace_once(
        a,
        "        p444_hdr()->flags |= P444_F_OVERFLOW;\n"
        "        p444_sync_header();\n"
        "        return -ENOSPC;",
        "        atomic_inc(&p444_dropped);\n"
        "        p444_hdr()->flags |= P444_F_OVERFLOW;\n"
        "        p444_hdr()->dropped_sections = (u32)atomic_read(&p444_dropped);\n"
        "        p444_hdr()->section_capacity = P444_MAX_SECTIONS;\n"
        "        pr_err(\"P445G DROP section idx=%u cap=%u off=%x len=%x dropped=%u\\n\",\n"
        "            idx, P444_MAX_SECTIONS, off, aligned,\n"
        "            (u32)atomic_read(&p444_dropped));\n"
        "        p444_sync_header();\n"
        "        return -ENOSPC;",
        "drop path",
    )

    a = replace_once(
        a,
        '#include <linux/delay.h>\n',
        '#include <linux/delay.h>\n#include <linux/regulator/consumer.h>\n',
        "regulator include",
    )
    a = replace_once(
        a,
        '#include "dsi/dsi_ctrl.h"\n',
        '#include "dsi/dsi_ctrl.h"\n'
        '#include "dsi/dsi_display.h"\n'
        '#include "dsi/dsi_panel.h"\n'
        '#include "dsi/dsi_phy.h"\n',
        "display includes",
    )

    power = r'''
/* A52_PHASE445G_GOLDEN_SYNC_PRE_V2
 * Matched passive power snapshot for the working kernel. No writes are issued.
 */
struct p445g_power_meta {
    u32 tag;
    u32 panel_initialized, ulps_feature, ulps_suspend;
    u32 panel_supply_refcount;
    u32 ctrl_digital_refcount, ctrl_host_refcount;
    u32 phy_digital_refcount, phy_pwr_refcount;
    u32 refgen_registered, refgen_enabled, refgen_use_count, refgen_pwrdwn;
} __packed;

#define P445G_SUPPLY_PANEL        1U
#define P445G_SUPPLY_CTRL_DIGITAL 2U
#define P445G_SUPPLY_CTRL_HOST    3U
#define P445G_SUPPLY_PHY_DIGITAL  4U
#define P445G_SUPPLY_PHY_PWR      5U

struct p445g_supply_rec {
    char name[32];
    u32 group, group_refcount;
    s32 enabled, use_count, voltage;
    u32 min_uv, max_uv;
} __packed;

extern int a52_p445g_regulator_state(struct regulator *reg, int *enabled,
        int *use_count, int *voltage);
extern int a52_p445g_refgen_snapshot(u32 *registered, u32 *enabled,
        u32 *use_count, u32 *pwrdwn);

static void p445g_collect_regs(struct p445g_supply_rec *r, u32 cap, u32 *n,
        struct dsi_regulator_info *info, u32 group)
{
    u32 i;
    int en, use, uv;

    if (!r || !n || !info)
        return;

    for (i = 0; i < info->count && *n < cap; i++) {
        struct dsi_vreg *v = &info->vregs[i];
        struct p445g_supply_rec *o;

        if (!v->vreg)
            continue;

        o = &r[*n];
        en = use = uv = -ENODATA;
        a52_p445g_regulator_state(v->vreg, &en, &use, &uv);
        memset(o, 0, sizeof(*o));
        strlcpy(o->name, v->vreg_name, sizeof(o->name));
        o->group = group;
        o->group_refcount = info->refcount;
        o->enabled = en;
        o->use_count = use;
        o->voltage = uv;
        o->min_uv = v->min_voltage;
        o->max_uv = v->max_voltage;
        (*n)++;
    }
}

void a52_p445g_power_pre(struct dsi_display *display)
{
    struct p445g_power_meta m;
    struct p445g_supply_rec r[32];
    struct dsi_panel *panel;
    struct dsi_ctrl *ctrl = NULL;
    struct msm_dsi_phy *phy = NULL;
    u32 n = 0;

    if (!display || !display->panel)
        return;

    panel = display->panel;
    if (display->ctrl_count) {
        ctrl = display->ctrl[0].ctrl;
        phy = display->ctrl[0].phy;
    }

    memset(&m, 0, sizeof(m));
    memset(r, 0, sizeof(r));
    m.tag = 1U;
    m.panel_initialized = panel->panel_initialized;
    m.ulps_feature = panel->ulps_feature_enabled;
    m.ulps_suspend = panel->ulps_suspend_enabled;
    m.panel_supply_refcount = panel->power_info.refcount;
    if (ctrl) {
        m.ctrl_digital_refcount = ctrl->pwr_info.digital.refcount;
        m.ctrl_host_refcount = ctrl->pwr_info.host_pwr.refcount;
    }
    if (phy) {
        m.phy_digital_refcount = phy->pwr_info.digital.refcount;
        m.phy_pwr_refcount = phy->pwr_info.phy_pwr.refcount;
    }

    a52_p445g_refgen_snapshot(&m.refgen_registered, &m.refgen_enabled,
        &m.refgen_use_count, &m.refgen_pwrdwn);

    a52_p444_store_section(P444_STAGE_PRE_DEEP, P444_TYPE_META,
        "POWER_PRE", 1U, 0U, &m, sizeof(m));

    p445g_collect_regs(r, ARRAY_SIZE(r), &n, &panel->power_info,
        P445G_SUPPLY_PANEL);
    if (ctrl) {
        p445g_collect_regs(r, ARRAY_SIZE(r), &n, &ctrl->pwr_info.digital,
            P445G_SUPPLY_CTRL_DIGITAL);
        p445g_collect_regs(r, ARRAY_SIZE(r), &n, &ctrl->pwr_info.host_pwr,
            P445G_SUPPLY_CTRL_HOST);
    }
    if (phy) {
        p445g_collect_regs(r, ARRAY_SIZE(r), &n, &phy->pwr_info.digital,
            P445G_SUPPLY_PHY_DIGITAL);
        p445g_collect_regs(r, ARRAY_SIZE(r), &n, &phy->pwr_info.phy_pwr,
            P445G_SUPPLY_PHY_PWR);
    }
    if (n)
        a52_p444_store_section(P444_STAGE_PRE_DEEP, P444_TYPE_META,
            "SUPPLY_PRE", n, 0U, r, n * sizeof(r[0]));
}
EXPORT_SYMBOL_GPL(a52_p445g_power_pre);

'''

    insert_at = a.find("static void p444_checkpoint(void);")
    if insert_at < 0:
        raise SystemExit("Phase445G: checkpoint anchor missing")
    a = a[:insert_at] + power + "\n" + a[insert_at:]

    a = replace_once(
        a,
        "static void p444_checkpoint(void);\nstatic void p444_predeep_workfn(struct work_struct *work);",
        """static void p444_checkpoint(void);

/* Matched synchronous PRE for the continuous-splash enable path.
 * Reuses the proven Phase444 deep capture; no recovery gate or R1/R2 logic
 * is compiled into the Golden twin.
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
    pr_info("P445G PRENOW done sec=%u used=%u drop=%u/%u crc=%08x\\n",
        p444_hdr()->section_count, p444_hdr()->used_bytes,
        p444_hdr()->dropped_sections, p444_hdr()->section_capacity,
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
/* A52_PHASE445G_GOLDEN_SYNC_PRE_V2 */
extern void a52_p444_predeep_now(struct dsi_ctrl *ctrl);
extern void a52_p445g_power_pre(struct dsi_display *display);""",
        "display extern",
    )

    d = replace_once(
        d,
        """\t\tmutex_lock(&display->display_lock);

\t\tdsi_panel_enable(display->panel);""",
        """\t\tmutex_lock(&display->display_lock);

\t\t/* A52_PHASE445G_GOLDEN_SYNC_PRE_V2:
\t\t * splash cleanup has completed; capture inherited hardware and power
\t\t * state immediately before Samsung on_pre/F0.
\t\t */
\t\ta52_p444_predeep_now(display->ctrl[0].ctrl);
\t\ta52_p445g_power_pre(display);

\t\tdsi_panel_enable(display->panel);""",
        "continuous-splash PRE call",
    )

    p444.write_text(a)
    disp.write_text(d)
    patch_regulator_core(root)
    patch_refgen(root)

    aa = p444.read_text()
    dd = disp.read_text()
    for token in (MARK, "P444_MAX_SECTIONS 120U", "dropped_sections",
                  "POWER_PRE", "SUPPLY_PRE", "a52_p445g_refgen_snapshot"):
        if token not in aa and token not in dd:
            raise SystemExit(f"Phase445G: postcondition missing {token}")
    if dd.count("a52_p444_predeep_now(display->ctrl[0].ctrl);") != 1:
        raise SystemExit("Phase445G: PRE call count mismatch")
    if dd.count("a52_p445g_power_pre(display);") != 1:
        raise SystemExit("Phase445G: power call count mismatch")
    print("Phase445G: Golden PRE + power + 120-section recorder applied")

if __name__ == "__main__":
    main()
