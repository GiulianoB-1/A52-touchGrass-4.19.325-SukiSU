#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARK = "A52_PHASE446D_CLOCK_POWER_MICROSCOPE_V1"

def die(msg: str) -> None:
    raise SystemExit("Phase446d: " + msg)

def one(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return s.replace(old, new, 1)

def add_decl_after_includes(s: str, decl: str, label: str) -> str:
    if decl in s:
        return s
    ms = list(re.finditer(r"(?m)^#include[^\n]*\n", s))
    if not ms:
        die(label + ": include block missing")
    p = ms[-1].end()
    return s[:p] + "\n" + decl + "\n" + s[p:]

def function_region(s: str, needle: str) -> tuple[int, int]:
    p = 0
    while True:
        start = s.find(needle, p)
        if start < 0:
            die("function missing: " + needle)
        brace = s.find("{", start)
        semi = s.find(";", start)
        if brace >= 0 and (semi < 0 or brace < semi):
            break
        p = start + len(needle)
    depth = 0
    i = brace
    while i < len(s):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    die("unterminated function: " + needle)

def patch_central(s: str) -> str:
    if MARK in s:
        return s

    decl = r'''extern char *saved_command_line;

static u32 p446d_cmd_hash(void)
{
    const unsigned char *p = (const unsigned char *)saved_command_line;
    u32 h = 2166136261U;
    unsigned int n = 0;

    if (!p)
        return 0;
    while (*p && n++ < 1024U) {
        h ^= *p++;
        h *= 16777619U;
    }
    return h;
}

static u32 p446d_cmd_flags(void)
{
    const char *s = saved_command_line;
    u32 f = 0;

    if (!s)
        return 0;
    if (strstr(s, "a52.keep_earlymap=1"))
        f |= BIT(0);
    if (strstr(s, "a52.skip_post_enable=1"))
        f |= BIT(1);
    if (strstr(s, "clk_ignore_unused"))
        f |= BIT(2);
    if (strstr(s, "a52.cb2_reuse=1"))
        f |= BIT(3);
    return f;
}
'''
    s = add_decl_after_includes(s, decl, "central cmdline proof")

    start, end = function_region(s, "void a52_p446_mark(")
    body = s[start:end]
    crc = "    r.crc32=p446_crc(&r,offsetof(struct p446_rec,crc32));"
    if crc not in body:
        die("central CRC anchor missing")

    inject = r'''    /* Phase446d: prove the image and the command line actually seen by Linux.
     * aux1 high bits are a compile-time identity; low nibble reports tokens.
     */
    if (event == P446_EVT_INIT) {
        r.aux0 = p446d_cmd_hash();
        r.aux1 = 0x446d0000U | p446d_cmd_flags();
    }

    /* Phase446d raw DISPCC/PLL microscope.
     * Preserve the 508-byte schema by reusing bus[2] and bus[3], which are
     * not needed for the clock-focused comparison:
     * bus2: mdp CMD/CFG/M/N, D, byte0 CMD, pclk0 CMD, esc0 CMD
     * bus3: vsync CMD, ahb CMD, pll MODE/L/FRAC/USER_CTL/STATUS/OPMODE
     */
    r.bus[2].valid = p446_r(p446_dispcc,0x107c);
    r.bus[2].handle = p446_r(p446_dispcc,0x1080);
    r.bus[2].curr = (s32)p446_r(p446_dispcc,0x1084);
    r.bus[2].num_paths = p446_r(p446_dispcc,0x1088);
    r.bus[2].ab = ((u64)p446_r(p446_dispcc,0x10c4) << 32) |
                  p446_r(p446_dispcc,0x108c);
    r.bus[2].ib = ((u64)p446_r(p446_dispcc,0x10e0) << 32) |
                  p446_r(p446_dispcc,0x1064);

    r.bus[3].valid = p446_r(p446_dispcc,0x10ac);
    r.bus[3].handle = p446_r(p446_dispcc,0x115c);
    r.bus[3].curr = (s32)p446_r(p446_dispcc,0x0000);
    r.bus[3].num_paths = p446_r(p446_dispcc,0x0004);
    r.bus[3].ab = ((u64)p446_r(p446_dispcc,0x000c) << 32) |
                  p446_r(p446_dispcc,0x0038);
    r.bus[3].ib = ((u64)p446_r(p446_dispcc,0x002c) << 32) |
                  p446_r(p446_dispcc,0x0024);

'''
    body = body.replace(crc, inject + crc, 1)
    s = s[:start] + body + s[end:]

    marker = 'static const char p446d_marker[] __used = "' + MARK + '";'
    anchor = 'static const char p446b_micro_marker[] __used = "A52_PHASE446B_FIRST_COMMIT_MICROSCOPE_V1";'
    if anchor in s:
        s = s.replace(anchor, anchor + "\n" + marker, 1)
    else:
        s += "\n" + marker + "\n"
    return s

def patch_msm_drv(s: str) -> str:
    if "A52_PHASE446D_MSM_KMS_CORRIDOR" in s:
        return s
    decl = 'extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* A52_PHASE446D_MSM_KMS_CORRIDOR */'
    s = add_decl_after_includes(s, decl, "msm drv")

    old = """\tcase KMS_SDE:
\t\tkms = sde_kms_init(ddev);
\t\tbreak;
"""
    new = """\tcase KMS_SDE:
\t\ta52_p446_mark(0x170U,0U,0U);
\t\tkms = sde_kms_init(ddev);
\t\ta52_p446_mark(0x171U,IS_ERR_OR_NULL(kms) ? 1U : 0U,0U);
\t\tbreak;
"""
    s = one(s, old, new, "sde_kms_init pre/post")

    old = "\tret = (kms)->funcs->hw_init(kms);\n"
    new = ("\ta52_p446_mark(0x172U,0U,0U);\n"
           "\tret = (kms)->funcs->hw_init(kms);\n"
           "\ta52_p446_mark(0x173U,(u32)ret,0U);\n")
    s = one(s, old, new, "kms hw_init pre/post")
    return s

def patch_kms(s: str) -> str:
    if "A52_PHASE446D_KMS_HW_INIT_CORRIDOR" in s:
        return s
    decl = 'extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* A52_PHASE446D_KMS_HW_INIT_CORRIDOR */'
    s = add_decl_after_includes(s, decl, "sde kms")

    sig = "static int sde_kms_hw_init(struct msm_kms *kms)\n{\n"
    s = one(s, sig, sig + "\ta52_p446_mark(0x174U,0U,0U);\n", "hw init entry")

    old = "\trc = _sde_kms_hw_init_ioremap(sde_kms, platformdev);\n"
    new = ("\ta52_p446_mark(0x175U,0U,0U);\n"
           "\trc = _sde_kms_hw_init_ioremap(sde_kms, platformdev);\n"
           "\ta52_p446_mark(0x176U,(u32)rc,0U);\n")
    s = one(s, old, new, "ioremap pre/post")

    old = "\trc = _sde_kms_get_splash_data(&sde_kms->splash_data);\n"
    new = ("\ta52_p446_mark(0x177U,0U,0U);\n"
           "\trc = _sde_kms_get_splash_data(&sde_kms->splash_data);\n"
           "\ta52_p446_mark(0x178U,(u32)rc,(u32)sde_kms->splash_data.num_splash_regions);\n")
    s = one(s, old, new, "splash data pre/post")

    old = "\trc = pm_runtime_get_sync(sde_kms->dev->dev);\n"
    new = ("\ta52_p446_mark(0x179U,0U,0U);\n"
           "\trc = pm_runtime_get_sync(sde_kms->dev->dev);\n"
           "\ta52_p446_mark(0x17aU,(u32)rc,0U);\n")
    s = one(s, old, new, "pm runtime pre/post")

    old = "\trc = _sde_kms_hw_init_blocks(sde_kms, dev, priv);\n"
    new = ("\ta52_p446_mark(0x17bU,0U,0U);\n"
           "\trc = _sde_kms_hw_init_blocks(sde_kms, dev, priv);\n"
           "\ta52_p446_mark(0x17cU,(u32)rc,0U);\n")
    s = one(s, old, new, "hw blocks pre/post")
    return s

def patch_power(s: str) -> str:
    if "A52_PHASE446D_POWER_ENABLE_STEPS" in s:
        return s
    decl = 'extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* A52_PHASE446D_POWER_ENABLE_STEPS */'
    s = add_decl_after_includes(s, decl, "power")

    old = """\t\tfor (i = 0; i < SDE_POWER_HANDLE_DBUS_ID_MAX &&
\t\t     phandle->data_bus_handle[i].data_bus_hdl; i++) {
\t\t\trc = _sde_power_data_bus_set_quota(
"""
    new = """\t\tfor (i = 0; i < SDE_POWER_HANDLE_DBUS_ID_MAX &&
\t\t     phandle->data_bus_handle[i].data_bus_hdl; i++) {
\t\t\ta52_p446_mark(0x190U,(u32)i,0U);
\t\t\trc = _sde_power_data_bus_set_quota(
"""
    s = one(s, old, new, "data bus pre")
    old = """\t\t\t\tSDE_POWER_HANDLE_ENABLE_BUS_IB_QUOTA);
\t\t\tif (rc) {
"""
    new = """\t\t\t\tSDE_POWER_HANDLE_ENABLE_BUS_IB_QUOTA);
\t\t\ta52_p446_mark(0x191U,(u32)i,(u32)rc);
\t\t\tif (rc) {
"""
    s = one(s, old, new, "data bus post")

    old = """\t\trc = msm_dss_enable_vreg(mp->vreg_config, mp->num_vreg,
\t\t\t\tenable);
"""
    new = """\t\ta52_p446_mark(0x192U,(u32)mp->num_vreg,0U);
\t\trc = msm_dss_enable_vreg(mp->vreg_config, mp->num_vreg,
\t\t\t\tenable);
\t\ta52_p446_mark(0x193U,(u32)mp->num_vreg,(u32)rc);
"""
    s = one(s, old, new, "vreg pre/post")

    old = "\t\trc = sde_power_scale_reg_bus(phandle, VOTE_INDEX_LOW, true);\n"
    new = ("\t\ta52_p446_mark(0x194U,0U,0U);\n"
           "\t\trc = sde_power_scale_reg_bus(phandle, VOTE_INDEX_LOW, true);\n"
           "\t\ta52_p446_mark(0x195U,(u32)rc,0U);\n")
    s = one(s, old, new, "reg bus pre/post")

    old = "\t\trc = sde_power_rsc_update(phandle, true);\n"
    new = ("\t\ta52_p446_mark(0x196U,0U,0U);\n"
           "\t\trc = sde_power_rsc_update(phandle, true);\n"
           "\t\ta52_p446_mark(0x197U,(u32)rc,0U);\n")
    s = one(s, old, new, "rsc pre/post")

    old = "\t\trc = msm_dss_enable_clk(mp->clk_config, mp->num_clk, enable);\n"
    new = ("\t\ta52_p446_mark(0x198U,(u32)mp->num_clk,0U);\n"
           "\t\trc = msm_dss_enable_clk(mp->clk_config, mp->num_clk, enable);\n"
           "\t\ta52_p446_mark(0x199U,(u32)mp->num_clk,(u32)rc);\n")
    s = one(s, old, new, "clock enable aggregate")

    old = """\t\tsde_power_event_trigger_locked(phandle,
\t\t\t\tSDE_POWER_EVENT_POST_ENABLE);
"""
    new = """\t\ta52_p446_mark(0x19aU,0U,0U);
\t\tsde_power_event_trigger_locked(phandle,
\t\t\t\tSDE_POWER_EVENT_POST_ENABLE);
\t\ta52_p446_mark(0x19bU,0U,0U);
"""
    s = one(s, old, new, "post enable event")
    return s

def patch_io(s: str) -> str:
    if "A52_PHASE446D_CLOCK_VREG_INNER_STEPS" in s:
        return s
    decl = r'''extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);
/* A52_PHASE446D_CLOCK_VREG_INNER_STEPS */
static u32 a52_p446d_name4(const char *s)
{
    u32 v = 0;
    int i;
    if (!s)
        return 0;
    for (i = 0; i < 4 && s[i]; i++)
        v |= (u32)(u8)s[i] << (8 * i);
    return v;
}
'''
    s = add_decl_after_includes(s, decl, "sde io")

    old = "\t\trc = clk_set_rate(clk->clk, clk->rate);\n"
    new = ("\t\ta52_p446_mark(0x1a2U,a52_p446d_name4(clk->clk_name),(u32)clk->rate);\n"
           "\t\trc = clk_set_rate(clk->clk, clk->rate);\n"
           "\t\ta52_p446_mark(0x1a3U,a52_p446d_name4(clk->clk_name),(u32)rc);\n")
    s = one(s, old, new, "clk set rate")

    old = "\t\t\t\trc = clk_prepare_enable(clk_arry[i].clk);\n"
    new = ("\t\t\t\ta52_p446_mark(0x1a0U,a52_p446d_name4(clk_arry[i].clk_name),(u32)i);\n"
           "\t\t\t\trc = clk_prepare_enable(clk_arry[i].clk);\n"
           "\t\t\t\ta52_p446_mark(0x1a1U,a52_p446d_name4(clk_arry[i].clk_name),(u32)rc);\n")
    s = one(s, old, new, "clk prepare enable")

    old = "\t\t\trc = regulator_enable(in_vreg[i].vreg);\n"
    new = ("\t\t\ta52_p446_mark(0x1a4U,a52_p446d_name4(in_vreg[i].vreg_name),(u32)i);\n"
           "\t\t\trc = regulator_enable(in_vreg[i].vreg);\n"
           "\t\t\ta52_p446_mark(0x1a5U,a52_p446d_name4(in_vreg[i].vreg_name),(u32)rc);\n")
    s = one(s, old, new, "regulator enable")
    return s

def patch_component(s: str) -> str:
    if "A52_PHASE446D_COMPONENT_BIND_STEPS" in s:
        return s
    decl = r'''#include <linux/of.h>
extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);
/* A52_PHASE446D_COMPONENT_BIND_STEPS */
static u64 a52_p446d_name8(const char *s)
{
    u64 v = 0;
    int i;
    if (!s)
        return 0;
    for (i = 0; i < 8 && s[i]; i++)
        v |= (u64)(u8)s[i] << (8 * i);
    return v;
}
'''
    ms = list(re.finditer(r"(?m)^#include[^\n]*\n", s))
    if not ms:
        die("component include block missing")
    p = ms[-1].end()
    s = s[:p] + "\n" + decl + "\n" + s[p:]

    old = """\t\tc = master->match->compare[i].component;
\t\t\tret = component_bind(c, master, data);
\t\t\tif (ret)
"""
    new = """\t\tc = master->match->compare[i].component;
\t\t\tif (master_dev->of_node &&
\t\t\t    of_device_is_compatible(master_dev->of_node, "qcom,sde-kms")) {
\t\t\t\tu64 n8 = a52_p446d_name8(dev_name(c->dev));
\t\t\t\ta52_p446_mark(0x160U + (((u32)i & 0xFU) << 1),
\t\t\t\t\t(u32)n8, (u32)(n8 >> 32));
\t\t\t}
\t\t\tret = component_bind(c, master, data);
\t\t\tif (master_dev->of_node &&
\t\t\t    of_device_is_compatible(master_dev->of_node, "qcom,sde-kms"))
\t\t\t\ta52_p446_mark(0x161U + (((u32)i & 0xFU) << 1),
\t\t\t\t\t(u32)i, (u32)ret);
\t\t\tif (ret)
"""
    s = one(s, old, new, "component bind loop")
    return s

def paths(root: Path, kind: str):
    if kind == "gki":
        base = root / "drivers/a52_display/msm"
        return {
            "central": base / "a52_phase445.c",
            "msm": base / "msm_drv.c",
            "kms": base / "sde/sde_kms.c",
            "power": base / "sde_power_handle.c",
            "io": base / "sde_io_util.c",
            "component": root / "drivers/base/component.c",
        }
    return {
        "central": root / "techpack/display/msm/a52_phase446g.c",
        "msm": root / "techpack/display/msm/msm_drv.c",
        "kms": root / "techpack/display/msm/sde/sde_kms.c",
        "power": root / "techpack/display/msm/sde_power_handle.c",
        "io": root / "techpack/display/msm/sde_io_util.c",
        "component": root / "drivers/base/component.c",
    }

def apply(root: Path, kind: str) -> None:
    ps = paths(root, kind)
    funcs = {
        "central": patch_central,
        "msm": patch_msm_drv,
        "kms": patch_kms,
        "power": patch_power,
        "io": patch_io,
        "component": patch_component,
    }
    for k, fn in funcs.items():
        p = ps[k]
        if not p.is_file():
            die("missing " + str(p))
        p.write_text(fn(p.read_text(errors="replace")))

def validate(root: Path, kind: str) -> None:
    ps = paths(root, kind)
    checks = {
        "central": [MARK, "0x446d0000U", "0x107c", "0x0038", "r.bus[3].ib"],
        "msm": ["0x170U", "0x172U", "A52_PHASE446D_MSM_KMS_CORRIDOR"],
        "kms": ["0x174U", "0x179U", "0x17bU", "A52_PHASE446D_KMS_HW_INIT_CORRIDOR"],
        "power": ["0x190U", "0x198U", "0x19bU", "A52_PHASE446D_POWER_ENABLE_STEPS"],
        "io": ["0x1a0U", "0x1a2U", "0x1a4U", "A52_PHASE446D_CLOCK_VREG_INNER_STEPS"],
        "component": ["0x160U", "0x161U", "A52_PHASE446D_COMPONENT_BIND_STEPS"],
    }
    for k, toks in checks.items():
        s = ps[k].read_text(errors="replace")
        for tok in toks:
            if tok not in s:
                die(f"{ps[k]}: missing {tok}")
    print(f"Phase446d {kind}: clock/power microscope PASS")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--kind", choices=("gki","tg"), required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root.resolve()
    if not ns.check_only:
        apply(root, ns.kind)
    validate(root, ns.kind)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
