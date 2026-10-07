#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE446G_SKIP_TE_IRQ_TLMM23_V1"

def die(msg: str) -> None:
    raise SystemExit("Phase446g: " + msg)

def one(s: str, old: str, new: str, what: str) -> str:
    n = s.count(old)
    if n != 1:
        die(f"{what}: expected 1 anchor, found {n}")
    return s.replace(old, new, 1)

def patch_central(s: str) -> str:
    if MARK in s:
        return s

    s = one(
        s,
        "    u32 pll0[9];\n    u32 crc32, commit;\n",
        "    u32 pll0[9];\n"
        "    /* Phase446g: live TLMM gpio23 TE pad. */\n"
        "    u32 te23_cfg, te23_intr_cfg;\n"
        "    u32 crc32, commit;\n",
        "record TE fields",
    )

    s = one(
        s,
        "static void __iomem *p446_refgen, *p446_phy, *p446_dsi;\n",
        "static void __iomem *p446_refgen, *p446_phy, *p446_dsi, *p446_tlmm23;\n",
        "TLMM mapping pointer",
    )

    anchor = '''        for (gi = 0; gi < ARRAY_SIZE(p446d_pll_off); gi++)
            r.pll0[gi] = p446_r(p446_dispcc, p446d_pll_off[gi]);
    }
    r.commit=P446_REC_COMMIT;'''
    repl = '''        for (gi = 0; gi < ARRAY_SIZE(p446d_pll_off); gi++)
            r.pll0[gi] = p446_r(p446_dispcc, p446d_pll_off[gi]);
    }
    /* GPIO23: CFG @ 0x0F117000, INTR_CFG @ +0x8. FUNC_SEL is bits[5:2].
     * Lagoon gpio23 funcs[0]=GPIO, funcs[1]=MDP_VSYNC.
     */
    r.te23_cfg=p446_r(p446_tlmm23,0x0U);
    r.te23_intr_cfg=p446_r(p446_tlmm23,0x8U);
    r.commit=P446_REC_COMMIT;'''
    s = one(s, anchor, repl, "per-record TLMM23 snapshot")

    old_init = '''    p446_dispcc=ioremap(P446_DISPCC_PHYS,P446_DISPCC_BYTES); p446_gcc=ioremap(P446_GCC_PHYS,P446_GCC_BYTES); p446_refgen=ioremap(P446_REFGEN_PHYS,P446_REFGEN_BYTES); p446_phy=ioremap(P446_PHY_PHYS,P446_PHY_BYTES);
    h=p446_hdr();'''
    new_init = '''    p446_dispcc=ioremap(P446_DISPCC_PHYS,P446_DISPCC_BYTES); p446_gcc=ioremap(P446_GCC_PHYS,P446_GCC_BYTES); p446_refgen=ioremap(P446_REFGEN_PHYS,P446_REFGEN_BYTES); p446_phy=ioremap(P446_PHY_PHYS,P446_PHY_BYTES);
    p446_tlmm23=ioremap(0x0F117000ULL,0x10U);
    BUILD_BUG_ON(sizeof(struct p446_rec) != 672U);
    h=p446_hdr();'''
    s = one(s, old_init, new_init, "TLMM23 ioremap + record size guard")

    marker_anchor = 'static const char p446d_marker[] __used = "A52_PHASE446D_POWER_CLOCK_MICROSCOPE_V1:GKI";'
    s = one(
        s,
        marker_anchor,
        marker_anchor + '\nstatic const char p446g_marker[] __used = "' + MARK + '";',
        "retained Phase446g marker",
    )
    return s

def patch_dsi(s: str) -> str:
    if "A52_PHASE446G_TE_IRQ_SKIP" in s:
        return s

    bind = "static int dsi_display_bind(struct device *dev,\n"
    decl = (
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n"
        "/* A52_PHASE446G_TE_IRQ_SKIP */\n"
    )
    s = one(s, bind, decl + bind, "dsi bind declaration")

    old = '''\t/* register te irq handler */
\tdsi_display_register_te_irq(display);
'''
    new = '''\t/* Phase446g: test the Linux 5.10 GPIO-IRQ request-resources remux theory.
\t * Do not request the TE GPIO IRQ. The panel TE pad should remain in the
\t * MDP_VSYNC mux selected by the panel-active pinctrl state.
\t */
\ta52_p446_mark(0x191U, (u32)display->disp_te_gpio, 0U);
\t/* dsi_display_register_te_irq(display); */
\ta52_p446_mark(0x192U, (u32)display->disp_te_gpio, 1U);
'''
    return one(s, old, new, "TE IRQ registration call")

def check(central: str, dsi: str) -> None:
    required_c = (
        MARK,
        "u32 te23_cfg, te23_intr_cfg;",
        "p446_tlmm23=ioremap(0x0F117000ULL,0x10U);",
        "BUILD_BUG_ON(sizeof(struct p446_rec) != 672U);",
        "r.te23_cfg=p446_r(p446_tlmm23,0x0U);",
        "r.te23_intr_cfg=p446_r(p446_tlmm23,0x8U);",
    )
    required_d = (
        "A52_PHASE446G_TE_IRQ_SKIP",
        "a52_p446_mark(0x191U, (u32)display->disp_te_gpio, 0U);",
        "a52_p446_mark(0x192U, (u32)display->disp_te_gpio, 1U);",
        "/* dsi_display_register_te_irq(display); */",
    )
    missing=[x for x in required_c if x not in central] + [x for x in required_d if x not in dsi]
    if missing:
        die("contract missing: " + ", ".join(missing))

    a=dsi.find("static int dsi_display_bind(")
    z=dsi.find("\nstatic void dsi_display_unbind(",a)
    if a < 0 or z < 0:
        die("dsi_display_bind bounds missing")
    body=dsi[a:z]
    active=[
        line for line in body.splitlines()
        if "dsi_display_register_te_irq(display);" in line and not line.lstrip().startswith("/*")
    ]
    if active:
        die("active TE IRQ registration still present in dsi_display_bind")

def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    central=a.root/"drivers/a52_display/msm/a52_phase445.c"
    dsi=a.root/"drivers/a52_display/msm/dsi/dsi_display.c"
    if not central.is_file() or not dsi.is_file():
        die("required GKI display source missing")

    cs=central.read_text(errors="replace")
    ds=dsi.read_text(errors="replace")
    if not a.check_only:
        central.write_text(patch_central(cs))
        dsi.write_text(patch_dsi(ds))
    check(central.read_text(errors="replace"),dsi.read_text(errors="replace"))
    print("Phase446g GKI: TE IRQ skipped + GPIO23 TLMM sampling PASS")

if __name__=="__main__":
    main()
