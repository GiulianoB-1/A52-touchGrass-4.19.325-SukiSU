#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import base64
import re
import zlib
from pathlib import Path

MARK = "A52_PHASE446_TG_GOLDEN_HEARTBEAT_V1"


def die(msg: str) -> None:
    raise SystemExit("Phase446G: " + msg)


def read(p: Path) -> str:
    return p.read_text(errors="replace")


def write(p: Path, s: str) -> None:
    p.write_text(s)


def one(s: str, old: str, new: str, label: str) -> str:
    n = s.count(old)
    if n != 1:
        die(f"{label}: expected 1 anchor, found {n}")
    return s.replace(old, new, 1)


def optional_one(s: str, old: str, new: str) -> str:
    if old in s:
        return s.replace(old, new, 1)
    return s


def canonical_p446_c(repo_root: Path) -> str:
    z = repo_root / "scripts/446_apply_heartbeat_cb2.py.z64"
    src = zlib.decompress(base64.b64decode(z.read_text().strip())).decode("utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "P446_C":
                    return ast.literal_eval(node.value)
    die("canonical GKI446 P446_C not found")


def build_tg_c(repo_root: Path) -> str:
    c = canonical_p446_c(repo_root)

    c = c.replace(
        "extern void a52_ackfr_record(const char *fmt, ...);",
        "static void a52_ackfr_record(const char *fmt, ...) { (void)fmt; }",
    )

    c = re.sub(
        r"extern void a52_p427_bus_get\(u32 slot, u32 \*valid, u32 \*handle, s32 \*curr,\s*"
        r"u32 \*num_paths, u64 \*ab, u64 \*ib\);",
        """static void a52_p427_bus_get(u32 slot, u32 *valid, u32 *handle, s32 *curr,
                             u32 *num_paths, u64 *ab, u64 *ib)
{
    (void)slot;
    *valid = 0; *handle = 0; *curr = -1; *num_paths = 0; *ab = 0; *ib = 0;
}""",
        c,
        flags=re.S,
    )

    c = c.replace(
        "static atomic_t p446_drop = ATOMIC_INIT(0);",
        "static atomic_t p446_drop = ATOMIC_INIT(0);\n"
        "static atomic_t p446_terminal_seen = ATOMIC_INIT(0);",
    )

    # 4.19 exposes the same CLOCK_BOOTTIME value as ktime_get_boot_ns().
    # Keep the Phase446 timestamp semantics identical without depending on the
    # newer ktime_get_boottime_ns() helper symbol.
    c = c.replace("ktime_get_boottime_ns()", "ktime_get_boot_ns()")

    old_work = """static void p446_workfn(struct work_struct *w)
{
    u64 age; unsigned long d; (void)w; a52_p446_mark(P446_EVT_PERIODIC,0,0); age=ktime_get_boottime_ns()-p446_start_ns;
    d=(age < 10000000000ULL)?msecs_to_jiffies(10):msecs_to_jiffies(1000); mod_delayed_work(system_unbound_wq,&p446_work,d);
}"""
    new_work = """static void p446_workfn(struct work_struct *w)
{
    u64 age;
    unsigned long d;
    (void)w;
    if (atomic_read(&p446_terminal_seen))
        return;
    a52_p446_mark(P446_EVT_PERIODIC,0,0);
    if (atomic_read(&p446_terminal_seen))
        return;
    age=ktime_get_boottime_ns()-p446_start_ns;
    d=(age < 10000000000ULL)?msecs_to_jiffies(10):msecs_to_jiffies(1000);
    mod_delayed_work(system_unbound_wq,&p446_work,d);
}"""
    if old_work not in c:
        die("canonical periodic work body changed")
    c = c.replace(old_work, new_work, 1)

    marker = 'static const char p446_marker[] __used = "A52_PHASE446_EARLY_SPLASH_HEARTBEAT_V1";'
    terminal = r'''
void a52_p446_terminal(u32 event,u32 aux0,u32 aux1)
{
    if (atomic_xchg(&p446_terminal_seen,1))
        return;
    a52_p446_mark(event,aux0,aux1);
}
EXPORT_SYMBOL_GPL(a52_p446_terminal);
'''
    if marker not in c:
        die("canonical terminal insertion marker missing")
    c = c.replace(marker, terminal + "\n" + marker, 1)

    c = c.replace(
        "static const struct proc_ops p446_raw_ops={.proc_read=p446_raw_read,.proc_lseek=default_llseek};",
        "static const struct file_operations p446_raw_ops={.owner=THIS_MODULE,.read=p446_raw_read,.llseek=default_llseek};",
    )
    c = c.replace(
        "static const struct proc_ops p446_ops={.proc_open=p446_open,.proc_read=seq_read,.proc_lseek=seq_lseek,.proc_release=single_release};",
        "static const struct file_operations p446_ops={.owner=THIS_MODULE,.open=p446_open,.read=seq_read,.llseek=seq_lseek,.release=single_release};",
    )

    if "struct proc_ops" in c:
        die("4.19 proc_ops adaptation incomplete")
    if "a52_phase446_raw" not in c or "P446_MAX_REC 2048U" not in c:
        die("canonical heartbeat contract incomplete")

    inc = r'''// SPDX-License-Identifier: GPL-2.0-only
/* A52_PHASE446_TG_GOLDEN_HEARTBEAT_V1
 * TouchGrass 4.19 observation-only twin of GKI Phase446.
 * No gate, ladder, handoff, CB selection, mapping, clock, bus, or display behavior is changed.
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/types.h>
#include <linux/atomic.h>
#include <linux/crc32.h>
#include <linux/io.h>
#include <linux/ktime.h>
#include <linux/proc_fs.h>
#include <linux/random.h>
#include <linux/seq_file.h>
#include <linux/sizes.h>
#include <linux/spinlock.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>
#include <linux/workqueue.h>
#include <asm/cacheflush.h>
#include "dsi/dsi_ctrl_reg.h"

'''
    return inc + c


def patch_make(s: str) -> str:
    if MARK in s:
        return s
    anchor = "obj-$(CONFIG_DRM_MSM)\t+= msm_drm.o\n"
    if anchor not in s:
        die("display Makefile msm_drm anchor missing")
    return s.replace(anchor, "msm_drm-y += a52_phase446g.o\n# " + MARK + "\n" + anchor, 1)


def add_extern(s: str, anchor: str, decl: str, label: str) -> str:
    if decl in s:
        return s
    if anchor not in s:
        die(label + ": extern anchor missing")
    return s.replace(anchor, decl + "\n" + anchor, 1)


def patch_dsi_ctrl(s: str) -> str:
    marker = "A52_PHASE446_TG_DSI_BASE"
    if marker in s:
        return s
    anchor = "static int dsi_ctrl_init_regmap(struct platform_device *pdev,\n"
    s = add_extern(
        s, anchor,
        "extern void a52_p446_register_dsi(void __iomem *base);\n/* " + marker + " */",
        "dsi ctrl",
    )
    old = '\tctrl->hw.base = ptr;\n\tDSI_CTRL_DEBUG(ctrl, "map dsi_ctrl registers to %pK\\n", ctrl->hw.base);\n'
    new = '\tctrl->hw.base = ptr;\n\ta52_p446_register_dsi(ctrl->hw.base);\n\tDSI_CTRL_DEBUG(ctrl, "map dsi_ctrl registers to %pK\\n", ctrl->hw.base);\n'
    return one(s, old, new, "dsi base register")


def patch_msm_drv(s: str) -> str:
    marker = "A52_PHASE446_TG_DRM_MARKERS"
    if marker in s:
        return s
    decl = "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* " + marker + " */\n"
    include_matches = list(re.finditer(r"(?m)^#include[^\n]*\n", s))
    if not include_matches:
        die("msm drv include block missing")
    decl_pos = include_matches[-1].end()
    s = s[:decl_pos] + "\n" + decl + s[decl_pos:]

    old_bind = """static int msm_drm_bind(struct device *dev)
{
\treturn msm_drm_init(dev, &msm_driver);
}"""
    new_bind = """static int msm_drm_bind(struct device *dev)
{
\tint rc;
\ta52_p446_mark(3U,0U,0U);
\trc = msm_drm_init(dev, &msm_driver);
\ta52_p446_mark(3U,1U,(u32)rc);
\treturn rc;
}"""
    s = one(s, old_bind, new_bind, "msm drm bind")

    s = one(
        s,
        "\tret = sde_power_resource_init(pdev, &priv->phandle);\n",
        "\ta52_p446_mark(4U,0U,0U);\n"
        "\tret = sde_power_resource_init(pdev, &priv->phandle);\n"
        "\ta52_p446_mark(5U,(u32)ret,0U);\n",
        "power init",
    )

    s = one(
        s,
        "\tkms->funcs->irq_preinstall(kms);\n",
        "\ta52_p446_mark(15U,0U,0U);\n"
        "\tkms->funcs->irq_preinstall(kms);\n"
        "\ta52_p446_mark(15U,2U,0U);\n",
        "irq preinstall wrapper",
    )

    s = one(
        s,
        "\t\tret = kms->funcs->cont_splash_config(kms);\n",
        "\t\ta52_p446_mark(16U,0U,0U);\n"
        "\t\tret = kms->funcs->cont_splash_config(kms);\n"
        "\t\ta52_p446_mark(16U,1U,(u32)ret);\n",
        "cont splash wrapper",
    )
    return s


def patch_msm_smmu(s: str) -> str:
    marker = "A52_PHASE446_TG_MSM_SMMU"
    if marker in s:
        return s
    anchor = "static int msm_smmu_probe(struct platform_device *pdev)\n"
    s = add_extern(
        s, anchor,
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* " + marker + " */",
        "msm smmu",
    )
    old = "\tclient->domain = iommu_get_domain_for_dev(client->dev);\n"
    new = (
        "\ta52_p446_mark(0x72U,0U,0U);\n"
        "\tclient->domain = iommu_get_domain_for_dev(client->dev);\n"
        "\ta52_p446_mark(0x73U,client->domain ? 1U : 0U,0U);\n"
    )
    return one(s, old, new, "iommu_get_domain_for_dev")


def patch_kms(s: str) -> str:
    marker = "A52_PHASE446_TG_KMS_MARKERS"
    if marker in s:
        return s
    anchor = "static int _sde_kms_mmu_init(struct sde_kms *sde_kms);\n"
    s = add_extern(
        s, anchor,
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* " + marker + " */",
        "kms",
    )

    sig = "static int _sde_kms_mmu_init(struct sde_kms *sde_kms)\n{\n"
    s = one(s, sig, sig + "\ta52_p446_mark(6U,0U,0U);\n", "kms mmu entry")

    splash = """\t\tif ((i == MSM_SMMU_DOMAIN_UNSECURE) &&
\t\t\t\tsde_kms->splash_data.num_splash_regions) {
\t\t\tret = _sde_kms_map_all_splash_regions(sde_kms);"""
    splash_new = """\t\tif ((i == MSM_SMMU_DOMAIN_UNSECURE) &&
\t\t\t\tsde_kms->splash_data.num_splash_regions) {
\t\t\ta52_p446_mark(10U,(u32)i,0U);
\t\t\tret = _sde_kms_map_all_splash_regions(sde_kms);
\t\t\ta52_p446_mark(11U,(u32)i,(u32)ret);"""
    s = one(s, splash, splash_new, "splash map pre/post")

    early = """\t\tret = mmu->funcs->set_attribute(mmu, DOMAIN_ATTR_EARLY_MAP,
\t\t\t\t &early_map);"""
    early_new = """\t\ta52_p446_mark(12U,(u32)i,0U);
\t\tret = mmu->funcs->set_attribute(mmu, DOMAIN_ATTR_EARLY_MAP,
\t\t\t\t &early_map);
\t\ta52_p446_mark(13U,(u32)i,(u32)ret);"""
    s = one(s, early, early_new, "early map pre/post")

    s = one(
        s,
        "\tsde_kms->base.aspace = sde_kms->aspace[0];\n\n\treturn 0;\n",
        "\tsde_kms->base.aspace = sde_kms->aspace[0];\n"
        "\ta52_p446_mark(7U,0U,0U);\n\n\treturn 0;\n",
        "kms mmu exit",
    )

    s = one(
        s,
        "\t\tsde_irq_update(msm_kms, true);\n\t\tsde_vbif_init_memtypes(sde_kms);\n",
        "\t\tsde_irq_update(msm_kms, true);\n"
        "\t\ta52_p446_mark(14U,0U,0U);\n"
        "\t\tsde_vbif_init_memtypes(sde_kms);\n"
        "\t\ta52_p446_mark(0x78U,0U,0U);\n",
        "vbif post enable",
    )

    for token in ("sde_rsc_init(", "sde_rsc_client_create("):
        if token in s:
            pos = s.find(token)
            line = s.rfind("\n", 0, pos) + 1
            s = s[:line] + "\ta52_p446_mark(0x79U,0U,0U);\n" + s[line:]
            break

    return s


def patch_power(s: str) -> str:
    marker = "A52_PHASE446_TG_POWER_MARKERS"
    if marker in s:
        return s
    # Quota hooks occur before sde_power_resource_init() in downstream 4.19,
    # so the prototype must live with the includes, before every call.
    anchor = '#include "sde_dbg.h"\n'
    s = add_extern(
        s, anchor,
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* " + marker + " */",
        "power",
    )

    s = one(
        s,
        "\trc = msm_dss_get_clk(&pdev->dev, mp->clk_config, mp->num_clk);\n",
        "\ta52_p446_mark(0x42U,0U,0U);\n"
        "\trc = msm_dss_get_clk(&pdev->dev, mp->clk_config, mp->num_clk);\n"
        "\ta52_p446_mark(0x43U,(u32)rc,(u32)mp->num_clk);\n",
        "clock get",
    )

    old_clk = "\t\trc = msm_dss_enable_clk(mp->clk_config, mp->num_clk, enable);\n"
    new_clk = (
        "\t\ta52_p446_mark(0x44U,(u32)enable,(u32)mp->num_clk);\n"
        "\t\trc = msm_dss_enable_clk(mp->clk_config, mp->num_clk, enable);\n"
        "\t\ta52_p446_mark(0x45U,(u32)rc,(u32)enable);\n"
    )
    if old_clk not in s:
        die("clock enable anchor missing")
    s = s.replace(old_clk, new_clk, 1)

    fn = "int sde_power_data_bus_set_quota(struct sde_power_handle *phandle,\n"
    if fn in s:
        pos = s.find(fn)
        brace = s.find("{", pos)
        ins = s.find("\n", brace) + 1
        s = s[:ins] + "\ta52_p446_mark(0x60U,bus_id,0U);\n" + s[ins:]
        ret_anchor = "\treturn rc;\n}"
        rp = s.find(ret_anchor, ins)
        if rp >= 0:
            s = s[:rp] + "\ta52_p446_mark(0x61U,bus_id,(u32)rc);\n" + s[rp:]
    return s


def patch_core_irq(s: str) -> str:
    marker = "A52_PHASE446_TG_IRQ_CLEAR"
    if marker in s:
        return s
    anchor = "void sde_core_irq_preinstall(struct sde_kms *sde_kms)\n"
    s = add_extern(
        s, anchor,
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* " + marker + " */",
        "core irq",
    )

    # 4.19 has more than one sde_clear_all_irqs() call in this file.
    # Instrument only the one inside sde_core_irq_preinstall().
    start = s.find(anchor)
    if start < 0:
        die("core irq: preinstall definition missing after extern insertion")
    brace = s.find("{", start)
    if brace < 0:
        die("core irq: preinstall opening brace missing")
    depth = 0
    end = -1
    for i in range(brace, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        die("core irq: preinstall closing brace missing")

    block = s[start:end]
    old = "\tsde_clear_all_irqs(sde_kms);\n"
    if block.count(old) < 1:
        die("after irq clear in preinstall: anchor missing")
    # The reconstructed 4.19.200 Golden tree can contain more than one matching
    # clear in this function after stable/vendor replay. The checkpoint is the
    # first preinstall clear, so instrument that one only.
    block = block.replace(
        old,
        old + "\ta52_p446_mark(15U,1U,0U);\n",
        1,
    )
    return s[:start] + block + s[end:]


def patch_dsi_display(s: str) -> str:
    marker = "A52_PHASE446_TG_DSI_STEPS"
    if marker in s:
        return s
    decl = (
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n"
        "extern void a52_p446_terminal(u32 event,u32 aux0,u32 aux1);\n"
        "/* " + marker + " */\n"
    )
    include_matches = list(re.finditer(r"(?m)^#include[^\n]*\n", s))
    if not include_matches:
        die("dsi display include block missing")
    decl_pos = include_matches[-1].end()
    s = s[:decl_pos] + "\n" + decl + s[decl_pos:]

    sig = "int dsi_display_cont_splash_config(void *dsi_display)\n{\n"
    s = one(s, sig, sig + "\ta52_p446_mark(0x80U,0U,0U);\n", "cont splash entry")

    steps = [
        ("\trc = pm_runtime_get_sync(display->drm_dev->dev);\n",
         "\ta52_p446_mark(0x81U,0U,0U);\n\trc = pm_runtime_get_sync(display->drm_dev->dev);\n\ta52_p446_mark(0x82U,(u32)rc,0U);\n"),
        ("\tdsi_display_ctrl_isr_configure(display, true);\n",
         "\ta52_p446_mark(0x83U,0U,0U);\n\tdsi_display_ctrl_isr_configure(display, true);\n\ta52_p446_mark(0x84U,0U,0U);\n"),
        ("\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n\t\t\tDSI_ALL_CLKS, DSI_CLK_ON);\n",
         "\ta52_p446_mark(0x85U,0U,0U);\n\trc = dsi_display_clk_ctrl(display->dsi_clk_handle,\n\t\t\tDSI_ALL_CLKS, DSI_CLK_ON);\n\ta52_p446_mark(0x86U,(u32)rc,0U);\n"),
        ("\trc = dsi_pwr_enable_regulator(&display->panel->power_info, true);\n",
         "\ta52_p446_mark(0x87U,0U,0U);\n\trc = dsi_pwr_enable_regulator(&display->panel->power_info, true);\n\ta52_p446_mark(0x88U,(u32)rc,0U);\n"),
        ("\tdsi_config_host_engine_state_for_cont_splash(display);\n",
         "\ta52_p446_mark(0x89U,0U,0U);\n\tdsi_config_host_engine_state_for_cont_splash(display);\n\ta52_p446_mark(0x8aU,0U,0U);\n"),
    ]
    for old,new in steps:
        if old not in s:
            die("dsi cont splash step anchor missing: " + old.splitlines()[0])
        s = s.replace(old,new,1)

    old_panel = "\t\tdsi_panel_enable(display->panel);\n"
    new_panel = (
        "\t\ta52_p446_mark(0x90U,0U,0U); /* PRE: after P445G synchronous PRE, before panel/F0 */\n"
        "\t\tdsi_panel_enable(display->panel);\n"
        "\t\ta52_p446_terminal(0x91U,0U,0U); /* terminal: after panel/F0 and existing HOT sampler */\n"
    )
    return one(s, old_panel, new_panel, "splash panel/F0 terminal")


def patch_bus_arb(s: str) -> str:
    marker = "A52_PHASE446_TG_BUS_HANDOFF"
    if marker in s:
        return s
    anchor = "int bcm_remove_handoff_req(struct device *dev, void *data)\n"
    s = add_extern(
        s, anchor,
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* " + marker + " */",
        "bus arb",
    )
    head = """int bcm_remove_handoff_req(struct device *dev, void *data)
{
\tstruct msm_bus_node_device_type *bus_dev = NULL;
\tstruct msm_bus_node_device_type *cur_bcm = NULL;
\tstruct msm_bus_node_device_type *cur_rsc = NULL;
\tint ret = 0;
"""
    return one(s, head, head + "\ta52_p446_mark(0x50U,0U,0U);\n", "bus handoff entry")


def patch_bus_fabric(s: str) -> str:
    marker = "A52_PHASE446_TG_BUS_LATE"
    if marker in s:
        return s
    anchor = "int __init msm_bus_device_late_init(void)\n"
    s = add_extern(
        s, anchor,
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n/* " + marker + " */",
        "bus fabric",
    )
    old = """int __init msm_bus_device_late_init(void)
{
\tcommit_late_init_data(true);
\tMSM_BUS_ERR("msm_bus_late_init: Remove handoff bw requests\\n");
\tinit_time = false;
\treturn commit_late_init_data(false);
}"""
    new = """int __init msm_bus_device_late_init(void)
{
\tint rc;
\ta52_p446_mark(0x52U,0U,0U);
\tcommit_late_init_data(true);
\tMSM_BUS_ERR("msm_bus_late_init: Remove handoff bw requests\\n");
\tinit_time = false;
\ta52_p446_mark(0x53U,0U,0U);
\trc = commit_late_init_data(false);
\ta52_p446_mark(0x54U,(u32)rc,0U);
\treturn rc;
}"""
    return one(s, old, new, "bus late handoff")


def patch_bus_proxy(s: str) -> str:
    marker = "A52_PHASE446_TG_BUS_PROXY"
    if marker in s:
        return s
    # Observation-only text trace. Avoid a display-symbol dependency in this early bus client.
    anchor = "proxy_client_info.pdata = msm_bus_cl_get_pdata(pdev);\n"
    if anchor not in s:
        die("bus proxy pdata anchor missing")
    s = s.replace(anchor, '/* ' + marker + ' */\n\tpr_info("P446 BUSPROXY get_pdata dev=%s\\n", dev_name(&pdev->dev));\n\t' + anchor, 1)
    update = "msm_bus_scale_client_update_request("
    if update in s:
        p=s.find(update)
        line=s.rfind("\n",0,p)+1
        s=s[:line]+'\tpr_info("P446 BUSPROXY handoff vote update\\n");\n'+s[line:]
    return s


def patch_clk(s: str) -> str:
    marker = "A52_PHASE446_TG_CLK_UNUSED_NAMES"
    if marker in s:
        return s
    old = """\tif (clk_core_is_enabled(core)) {
\t\ttrace_clk_disable(core);"""
    new = """\tif (clk_core_is_enabled(core)) {
\t\t/* A52_PHASE446_TG_CLK_UNUSED_NAMES: observation only */
\t\tif (core->name && (strnstr(core->name, "disp", strlen(core->name)) ||
\t\t\t\t  strnstr(core->name, "mdp", strlen(core->name)) ||
\t\t\t\t  strnstr(core->name, "dsi", strlen(core->name)) ||
\t\t\t\t  strnstr(core->name, "gcc", strlen(core->name))))
\t\t\tpr_info("P446 CLKDIS name=%s rate=%lu\\n", core->name, core->rate);
\t\ttrace_clk_disable(core);"""
    return one(s, old, new, "clk disable-unused trace")


def patch_arm_smmu(s: str) -> str:
    marker = "A52_PHASE446_TG_ARM_SMMU"
    if marker in s:
        return s

    helper_anchor = "static void arm_smmu_write_s2cr"
    helper_pos = s.find(helper_anchor)
    if helper_pos < 0:
        die("arm_smmu_write_s2cr missing")

    helper = r'''
/* A52_PHASE446_TG_ARM_SMMU
 * Observation only. Reads are performed while the existing SMMU power vote is held.
 */
extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);
extern void a52_p446_note_smmu(u32 event,u32 smr,u32 s2cr,u32 cb,u32 sctlr,
                              u64 ttbr0,u32 tcr,u32 fsr,u32 aux);

static bool a52_p446g_sid800_sme(struct arm_smmu_device *smmu, int idx)
{
    return smmu && smmu->smrs && idx >= 0 && idx < smmu->num_mapping_groups &&
           smmu->smrs[idx].valid && smmu->smrs[idx].id == 0x800;
}

static void a52_p446g_emit_smmu(struct arm_smmu_device *smmu, u32 event,
                               int sme, u32 cb, u32 aux)
{
    void __iomem *cb_base;
    u32 smr = ~0U, s2cr = ~0U, sctlr = ~0U, tcr = ~0U, fsr = ~0U;
    u64 ttbr0 = ~0ULL;

    if (!smmu || sme < 0 || sme >= smmu->num_mapping_groups ||
        cb >= smmu->num_context_banks)
        return;

    if (smmu->smrs)
        smr = readl_relaxed(ARM_SMMU_GR0(smmu) + ARM_SMMU_GR0_SMR(sme));
    s2cr = readl_relaxed(ARM_SMMU_GR0(smmu) + ARM_SMMU_GR0_S2CR(sme));
    cb_base = ARM_SMMU_CB(smmu, cb);
    sctlr = readl_relaxed(cb_base + ARM_SMMU_CB_SCTLR);
    tcr = readl_relaxed(cb_base + ARM_SMMU_CB_TTBCR);
    fsr = readl_relaxed(cb_base + ARM_SMMU_CB_FSR);
#ifdef readq_relaxed
    ttbr0 = readq_relaxed(cb_base + ARM_SMMU_CB_TTBR0);
#else
    ttbr0 = (u64)readl_relaxed(cb_base + ARM_SMMU_CB_TTBR0);
    ttbr0 |= (u64)readl_relaxed(cb_base + ARM_SMMU_CB_TTBR0 + 4) << 32;
#endif
    a52_p446_note_smmu(event, smr, s2cr, cb, sctlr, ttbr0, tcr, fsr, aux);
}

'''
    s = s[:helper_pos] + helper + s[helper_pos:]

    # arm_smmu_handoff_cbs() appears before the helper definition in this 4.19
    # tree, so provide a file-scope prototype immediately after the include
    # block. Do not depend on a vendor-specific header being present.
    proto = (
        "struct arm_smmu_device;\n"
        "static void a52_p446g_emit_smmu(struct arm_smmu_device *smmu, u32 event,\n"
        "                               int sme, u32 cb, u32 aux);\n"
    )
    include_matches = list(re.finditer(r"(?m)^#include[^\n]*\n", s))
    if not include_matches:
        die("a52_p446g_emit_smmu include block missing")
    proto_pos = include_matches[-1].end()
    s = s[:proto_pos] + "\n" + proto + s[proto_pos:]

    # Earliest apps-SMMU probe and native firmware handoff checkpoint.
    s = one(
        s,
        "\terr = arm_smmu_handoff_cbs(smmu);\n",
        "\ta52_p446_mark(0x70U,0U,0U);\n"
        "\terr = arm_smmu_handoff_cbs(smmu);\n"
        "\ta52_p446_mark(0x70U,1U,(u32)err);\n",
        "arm handoff call",
    )

    # Capture the bootloader's own SID 0x800 route and CB registers while the probe power vote is live.
    old_handoff = """\t\tsmmu->smrs[i] = smr;
\t\tsmmu->s2crs[i] = s2cr;
\t\tbitmap_set(smmu->context_map, s2cr.cbndx, 1);"""
    new_handoff = """\t\tsmmu->smrs[i] = smr;
\t\tsmmu->s2crs[i] = s2cr;
\t\tbitmap_set(smmu->context_map, s2cr.cbndx, 1);
\t\tif (smr.id == 0x800)
\t\t\ta52_p446g_emit_smmu(smmu,1U,i,s2cr.cbndx,0U);"""
    s = one(s, old_handoff, new_handoff, "native handoff capture")

    # Record which handed-off CB arm_smmu_alloc_cb returns. Do not change selection logic.
    old_return = """\tmutex_unlock(&smmu->stream_map_mutex);

\treturn cb;
}

static int arm_smmu_handoff_cbs"""
    new_return = """\tmutex_unlock(&smmu->stream_map_mutex);

\tif (fwspec && fwspec->num_ids && ((u16)fwspec->ids[0] == 0x800)) {
\t\ta52_p446_mark(0x71U,(u32)cb,0U);
\t\tif (cb >= 0)
\t\t\ta52_p446g_emit_smmu(smmu,0x71U,fwspec_smendx(fwspec,0),cb,1U);
\t}
\treturn cb;
}

static int arm_smmu_handoff_cbs"""
    s = one(s, old_return, new_return, "alloc cb return")

    # Context-bank programming with EARLY_MAP. Record before/after the existing write.
    old_cbwrite = """\t\tarm_smmu_init_context_bank(smmu_domain,
\t\t\t\t\t\t&smmu_domain->pgtbl_cfg);
\t\tarm_smmu_write_context_bank(smmu, cfg->cbndx,
\t\t\t\t\t    smmu_domain->attributes);"""
    new_cbwrite = """\t\tarm_smmu_init_context_bank(smmu_domain,
\t\t\t\t\t\t&smmu_domain->pgtbl_cfg);
\t\tif (dev->iommu_fwspec && dev->iommu_fwspec->num_ids &&
\t\t    ((u16)dev->iommu_fwspec->ids[0] == 0x800))
\t\t\ta52_p446g_emit_smmu(smmu,0x74U,fwspec_smendx(dev->iommu_fwspec,0),
\t\t\t\tcfg->cbndx,smmu_domain->attributes);
\t\tarm_smmu_write_context_bank(smmu, cfg->cbndx,
\t\t\t\t\t    smmu_domain->attributes);
\t\tif (dev->iommu_fwspec && dev->iommu_fwspec->num_ids &&
\t\t    ((u16)dev->iommu_fwspec->ids[0] == 0x800))
\t\t\ta52_p446g_emit_smmu(smmu,0x75U,fwspec_smendx(dev->iommu_fwspec,0),
\t\t\t\tcfg->cbndx,smmu_domain->attributes);"""
    s = one(s, old_cbwrite, new_cbwrite, "context bank write")

    # Any S2CR write for the SID 0x800 stream, matching GKI events 8/9.
    old_s2 = """\twritel_relaxed(reg, ARM_SMMU_GR0(smmu) + ARM_SMMU_GR0_S2CR(idx));
}"""
    new_s2 = """\tif (a52_p446g_sid800_sme(smmu, idx))
\t\ta52_p446g_emit_smmu(smmu,8U,idx,s2cr->cbndx,reg);
\twritel_relaxed(reg, ARM_SMMU_GR0(smmu) + ARM_SMMU_GR0_S2CR(idx));
\tif (a52_p446g_sid800_sme(smmu, idx))
\t\ta52_p446g_emit_smmu(smmu,9U,idx,s2cr->cbndx,reg);
}"""
    # Only replace the arm_smmu_write_s2cr instance by searching around its function.
    p = s.find("static void arm_smmu_write_s2cr")
    q = s.find("static void arm_smmu_write_sme", p)
    block = s[p:q]
    if old_s2 not in block:
        die("S2CR write anchor missing")
    block = block.replace(old_s2, new_s2, 1)
    s = s[:p] + block + s[q:]

    # M bit transition when EARLY_MAP is cleared.
    old_enable = """\treg = readl_relaxed(cb_base + ARM_SMMU_CB_SCTLR);
\treg |= SCTLR_M;

\twritel_relaxed(reg, cb_base + ARM_SMMU_CB_SCTLR);"""
    new_enable = """\treg = readl_relaxed(cb_base + ARM_SMMU_CB_SCTLR);
\tif (cfg->cbndx == 2)
\t\ta52_p446_mark(12U,cfg->cbndx,reg);
\treg |= SCTLR_M;

\twritel_relaxed(reg, cb_base + ARM_SMMU_CB_SCTLR);
\tif (cfg->cbndx == 2) {
\t\tu32 now = readl_relaxed(cb_base + ARM_SMMU_CB_SCTLR);
\t\ta52_p446_mark(13U,cfg->cbndx,now);
\t}"""
    s = one(s, old_enable, new_enable, "enable S1 translations")

    return s


def apply(root: Path, repo_root: Path) -> None:
    paths = {
        "make": root / "techpack/display/msm/Makefile",
        "central": root / "techpack/display/msm/a52_phase446g.c",
        "dsi_ctrl": root / "techpack/display/msm/dsi/dsi_ctrl.c",
        "dsi_display": root / "techpack/display/msm/dsi/dsi_display.c",
        "msm_drv": root / "techpack/display/msm/msm_drv.c",
        "msm_smmu": root / "techpack/display/msm/msm_smmu.c",
        "kms": root / "techpack/display/msm/sde/sde_kms.c",
        "power": root / "techpack/display/msm/sde_power_handle.c",
        "core_irq": root / "techpack/display/msm/sde/sde_core_irq.c",
        "arm": root / "drivers/iommu/arm-smmu.c",
        "bus_arb": root / "drivers/soc/qcom/msm_bus/msm_bus_arb_rpmh.c",
        "bus_fabric": root / "drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c",
        "bus_proxy": root / "drivers/soc/qcom/msm_bus/msm_bus_proxy_client.c",
        "clk": root / "drivers/clk/clk.c",
    }
    for name,p in paths.items():
        if name != "central" and not p.exists():
            die(f"{name} missing: {p}")

    if not paths["central"].exists():
        write(paths["central"], build_tg_c(repo_root))

    funcs = {
        "make": patch_make,
        "dsi_ctrl": patch_dsi_ctrl,
        "dsi_display": patch_dsi_display,
        "msm_drv": patch_msm_drv,
        "msm_smmu": patch_msm_smmu,
        "kms": patch_kms,
        "power": patch_power,
        "core_irq": patch_core_irq,
        "arm": patch_arm_smmu,
        "bus_arb": patch_bus_arb,
        "bus_fabric": patch_bus_fabric,
        "bus_proxy": patch_bus_proxy,
        "clk": patch_clk,
    }
    for name,fn in funcs.items():
        p=paths[name]
        write(p, fn(read(p)))


def check(root: Path) -> None:
    req = {
        root / "techpack/display/msm/a52_phase446g.c": [
            MARK, "P446_MAX_REC 2048U", "P446_RAM_PHYS 0xB1800000ULL",
            'proc_create("a52_phase446_raw",0444', 'proc_create("a52_phase446",0444',
            "struct p446_rec", "a52_p446_terminal", "P446_EVT_PERIODIC",
        ],
        root / "drivers/iommu/arm-smmu.c": [
            "A52_PHASE446_TG_ARM_SMMU", "arm_smmu_handoff_cbs",
            "cb_handoff", "a52_p446g_emit_smmu", "0x800", "a52_p446_mark(12U",
            "a52_p446_mark(13U",
        ],
        root / "techpack/display/msm/dsi/dsi_display.c": [
            "A52_PHASE446_TG_DSI_STEPS", "a52_p446_mark(0x90U",
            "a52_p446_terminal(0x91U",
        ],
        root / "techpack/display/msm/sde/sde_core_irq.c": [
            "A52_PHASE446_TG_IRQ_CLEAR", "a52_p446_mark(15U,1U",
        ],
        root / "drivers/clk/clk.c": ["A52_PHASE446_TG_CLK_UNUSED_NAMES", "P446 CLKDIS"],
    }
    missing=[]
    for p,toks in req.items():
        s=read(p)
        for tok in toks:
            if tok not in s:
                missing.append(f"{p}:{tok}")
    if missing:
        die("contract missing " + ", ".join(missing))

    arm=read(root/"drivers/iommu/arm-smmu.c")
    forbidden=("a52.cb2_reuse","A52_PHASE446_CB2_SWITCH","a52_p427_sid800_dev")
    bad=[x for x in forbidden if x in arm]
    if bad:
        die("TG observation-only contract violated: " + ", ".join(bad))

    dsi=read(root/"techpack/display/msm/dsi/dsi_display.c")
    if "a52_p444_hot" in dsi and dsi.find("a52_p446_mark(0x90U") > dsi.find("dsi_panel_enable(display->panel);"):
        die("PRE marker not before panel enable")

    p444 = root / "techpack/display/msm/a52_phase444.c"
    if p444.exists():
        s=read(p444)
        if "P444_MAX_SECTIONS 120U" not in s:
            die("Phase445G v2 section capacity regression")

    print("Phase446G TouchGrass golden heartbeat contract: PASS")


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--repo-root",type=Path,default=Path.cwd())
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    if not a.check_only:
        apply(a.root,a.repo_root)
    check(a.root)


if __name__ == "__main__":
    main()
