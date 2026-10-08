#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE446H_PANEL_F0_LIFECYCLE_V1"

def die(msg: str) -> None:
    raise SystemExit("Phase446h: " + msg)

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
        "static atomic_t p446_burst = ATOMIC_INIT(0);\n",
        "static atomic_t p446_burst = ATOMIC_INIT(0);\n"
        "/* Phase446h: after panel handoff, never let recorder runtime-PM\n"
        " * sampling block record commit. Cached SMMU state remains available.\n"
        " */\n"
        "static atomic_t p446h_late_safe = ATOMIC_INIT(0);\n",
        "late-safe state",
    )

    s = one(
        s,
        "        if(!smmu_internal)\n"
        "            lrc=a52_p446_read_cb2(&l_smr,&l_s2cr,&l_cb,&l_sctlr,&l_ttbr0,&l_tcr,&l_fsr);\n",
        "        if(!smmu_internal && !atomic_read(&p446h_late_safe))\n"
        "            lrc=a52_p446_read_cb2(&l_smr,&l_s2cr,&l_cb,&l_sctlr,&l_ttbr0,&l_tcr,&l_fsr);\n",
        "late-safe live-CB gate",
    )

    anchor = '''void a52_p446_register_dsi(void __iomem *base)
{
    WRITE_ONCE(p446_dsi,base);
    a52_ackfr_record("P446 DSI base=%px",base);
}
EXPORT_SYMBOL_GPL(a52_p446_register_dsi);
'''
    repl = anchor + '''
void a52_p446_set_late_safe(u32 enable)
{
    atomic_set(&p446h_late_safe, !!enable);
}
EXPORT_SYMBOL_GPL(a52_p446_set_late_safe);
'''
    s = one(s, anchor, repl, "late-safe export")

    marker_anchor = 'static const char p446g_marker[] __used = "A52_PHASE446G_SKIP_TE_IRQ_TLMM23_V1";'
    s = one(
        s, marker_anchor,
        marker_anchor + '\nstatic const char p446h_marker[] __used = "' + MARK + '";',
        "retained Phase446h marker",
    )
    return s

def patch_display(s: str) -> str:
    if "A52_PHASE446H_DISPLAY_HANDOFF" in s:
        return s
    s = one(
        s,
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n"
        "/* A52_PHASE446B_H13_PANEL_ENABLE */\n",
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n"
        "extern void a52_p446_set_late_safe(u32 enable);\n"
        "/* A52_PHASE446B_H13_PANEL_ENABLE */\n"
        "/* A52_PHASE446H_DISPLAY_HANDOFF */\n",
        "display extern",
    )
    s = one(
        s,
        "\t\ta52_p446_mark(0x12dU,1U,(u32)display->ctrl_count);\n"
        "\t\trc = dsi_panel_enable(display->panel);\n",
        "\t\ta52_p446_set_late_safe(1U);\n"
        "\t\ta52_p446_mark(0x193U,1U,(u32)display->ctrl_count);\n"
        "\t\ta52_p446_mark(0x12dU,1U,(u32)display->ctrl_count);\n"
        "\t\trc = dsi_panel_enable(display->panel);\n"
        "\t\ta52_p446_mark(0x19dU,(u32)rc,(u32)display->ctrl_count);\n",
        "display panel call",
    )
    return s

def patch_panel(s: str) -> str:
    if "A52_PHASE446H_PANEL_STEPS" in s:
        return s

    anchor = "int dsi_panel_enable(struct dsi_panel *panel)\n"
    s = one(
        s, anchor,
        "extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);\n"
        "/* A52_PHASE446H_PANEL_STEPS */\n" + anchor,
        "panel declaration",
    )

    s = one(
        s,
        '\ta52_ackfr_record("DISP PANEL_ENABLE p=%s init=%d",\n'
        '\t\tpanel->name, panel->panel_initialized);\n\n'
        '\tmutex_lock(&panel->panel_lock);\n',
        '\ta52_ackfr_record("DISP PANEL_ENABLE p=%s init=%d",\n'
        '\t\tpanel->name, panel->panel_initialized);\n'
        '\ta52_p446_mark(0x194U,(u32)panel->panel_initialized,0U);\n\n'
        '\tmutex_lock(&panel->panel_lock);\n'
        '\ta52_p446_mark(0x195U,1U,0U);\n',
        "panel enter/lock",
    )

    # Phase445 changed the early return to -ECANCELED when its Samsung gate
    # blocks subsequent traffic. Bracket the actual current form, preserving it.
    old_pre = '''\tif (!ss_panel_on_pre(panel->panel_private)) {
\t\tmutex_unlock(&panel->panel_lock);
\t\tif (!a52_p445_samsung_blocked())
\t\t\tpanel->panel_initialized = true;
\t\treturn a52_p445_samsung_blocked() ? -ECANCELED : 0;
\t}
'''
    new_pre = '''\ta52_p446_mark(0x196U,0U,0U);
\tif (!ss_panel_on_pre(panel->panel_private)) {
\t\ta52_p446_mark(0x197U,0U,(u32)a52_p445_samsung_blocked());
\t\tmutex_unlock(&panel->panel_lock);
\t\tif (!a52_p445_samsung_blocked())
\t\t\tpanel->panel_initialized = true;
\t\treturn a52_p445_samsung_blocked() ? -ECANCELED : 0;
\t}
\ta52_p446_mark(0x197U,1U,(u32)a52_p445_samsung_blocked());
'''
    s = one(s, old_pre, new_pre, "ss_panel_on_pre bracket")

    old_on = '''\t/* skip cmds during splash booting */
\tif (vdd->skip_cmd_set_on_splash_enabled && vdd->samsung_splash_enabled) {
\t\tLCD_INFO(vdd, "skip send DSI_CMD_SET_ON during splash booting\\n");
\t\trc = 0;
\t} else {
\t\trc = dsi_panel_tx_cmd_set(panel, DSI_CMD_SET_ON);
\t}
'''
    new_on = '''\t/* skip cmds during splash booting */
\ta52_p446_mark(0x198U,0U,(u32)vdd->samsung_splash_enabled);
\tif (vdd->skip_cmd_set_on_splash_enabled && vdd->samsung_splash_enabled) {
\t\tLCD_INFO(vdd, "skip send DSI_CMD_SET_ON during splash booting\\n");
\t\trc = 0;
\t} else {
\t\trc = dsi_panel_tx_cmd_set(panel, DSI_CMD_SET_ON);
\t}
\ta52_p446_mark(0x199U,(u32)rc,(u32)a52_p445_samsung_blocked());
'''
    s = one(s, old_on, new_on, "DSI_CMD_SET_ON bracket")

    old_post = '''\tif (!a52_p445_samsung_blocked())
\t\tss_panel_on_post(panel->panel_private);
\tLCD_INFO(vdd, "--\\n");
'''
    new_post = '''\tif (!a52_p445_samsung_blocked()) {
\t\ta52_p446_mark(0x19aU,(u32)rc,0U);
\t\tss_panel_on_post(panel->panel_private);
\t\ta52_p446_mark(0x19bU,(u32)rc,0U);
\t}
\tLCD_INFO(vdd, "--\\n");
'''
    s = one(s, old_post, new_post, "panel on_post bracket")

    s = one(
        s,
        '\tmutex_unlock(&panel->panel_lock);\n\treturn rc;\n}\n\nint dsi_panel_post_enable',
        '\ta52_p446_mark(0x19cU,(u32)rc,(u32)panel->panel_initialized);\n'
        '\tmutex_unlock(&panel->panel_lock);\n\treturn rc;\n}\n\nint dsi_panel_post_enable',
        "panel return",
    )
    return s

def patch_ctrl(s: str) -> str:
    if "A52_PHASE446H_F0_LIFECYCLE" in s:
        return s

    decl_anchor = '''extern bool a52_p445_active(void);
extern void a52_p445_try_arm(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg, u32 flags);
'''
    decl_repl = '''extern bool a52_p445_active(void);
extern void a52_p446_mark(u32 event,u32 aux0,u32 aux1);
/* A52_PHASE446H_F0_LIFECYCLE */
extern void a52_p445_try_arm(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg, u32 flags);
'''
    s = one(s, decl_anchor, decl_repl, "F0 mark declaration")

    s = one(
        s,
        "\tbool a52_p421_f0 = false;\n",
        "\tbool a52_p421_f0 = false;\n\tbool a52_p446h_f0 = false;\n",
        "F0 local",
    )

    detect_anchor = '''#endif

\ta52_p421_f0 = false; /* Phase444 retires legacy F0 forensics */
'''
    detect_repl = '''#endif

\tif (dsi_ctrl && msg && dsi_ctrl->cell_index == 0 && msg->type == 0x29 &&
\t    msg->tx_len == 3 && msg->tx_buf) {
\t\tconst u8 *p446h = msg->tx_buf;
\t\tif (p446h[0] == 0xF0 && p446h[1] == 0x5A && p446h[2] == 0x5A) {
\t\t\ta52_p446h_f0 = true;
\t\t\ta52_p446_mark(0x1a0U, flags ? *flags : 0U, (u32)msg->flags);
\t\t}
\t}

\ta52_p421_f0 = false; /* Phase444 retires legacy F0 forensics */
'''
    s = one(s, detect_anchor, detect_repl, "F0 detect")

    s = one(
        s,
        "\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n",
        "\tdsi_message_setup_tx_mode(dsi_ctrl, msg->tx_len, flags);\n"
        "\tif (a52_p446h_f0) a52_p446_mark(0x1a1U,*flags,0U);\n",
        "F0 after mode",
    )
    s = one(
        s,
        "\trc = dsi_message_validate_tx_mode(dsi_ctrl, msg->tx_len, flags);\n",
        "\trc = dsi_message_validate_tx_mode(dsi_ctrl, msg->tx_len, flags);\n"
        "\tif (a52_p446h_f0) a52_p446_mark(0x1a2U,*flags,(u32)rc);\n",
        "F0 after validate",
    )
    s = one(
        s,
        "\ta52_p445_try_arm(dsi_ctrl, msg, *flags);\n",
        "\ta52_p445_try_arm(dsi_ctrl, msg, *flags);\n"
        "\tif (a52_p446h_f0) a52_p446_mark(0x1a3U,*flags,(u32)a52_p445_active());\n",
        "F0 arm",
    )

    s = one(
        s,
        "\tdsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);\n"
        "\tif (a52_p445_abort_pending()) { rc = -ETIMEDOUT; goto error; }\n",
        "\tif (a52_p446h_f0) a52_p446_mark(0x1a4U,*flags,(u32)cmd_mem.offset);\n"
        "\tdsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);\n"
        "\tif (a52_p446h_f0) a52_p446_mark(0x1adU,(u32)rc,(u32)a52_p445_active());\n"
        "\tif (a52_p445_abort_pending()) { rc = -ETIMEDOUT; goto error; }\n",
        "F0 kickoff wrapper",
    )

    # The real HW kickoff is in dsi_kickoff_msg_tx(), so use Phase445's global
    # active state there rather than the dsi_message_tx() local boolean.
    old_hw = '''\t\t\t\tif (a52_p445_active() && a52_p445_rung() == 0)
\t\t\t\t\ta52_p445_capture_kickoff_ctx(dsi_ctrl, cmd_mem, hw_flags);
\t\t\t\tdsi_hw_ops.kickoff_command(
\t\t\t\t\t\t&dsi_ctrl->hw,
\t\t\t\t\t\tcmd_mem,
\t\t\t\t\t\thw_flags);
'''
    new_hw = '''\t\t\t\tif (a52_p445_active() && a52_p445_rung() == 0)
\t\t\t\t\ta52_p445_capture_kickoff_ctx(dsi_ctrl, cmd_mem, hw_flags);
\t\t\t\tif (a52_p445_active())
\t\t\t\t\ta52_p446_mark(0x1a5U,(u32)cmd_mem->offset,(u32)cmd_mem->length);
\t\t\t\tdsi_hw_ops.kickoff_command(
\t\t\t\t\t\t&dsi_ctrl->hw,
\t\t\t\t\t\tcmd_mem,
\t\t\t\t\t\thw_flags);
\t\t\t\tif (a52_p445_active())
\t\t\t\t\ta52_p446_mark(0x1a6U,hw_flags,
\t\t\t\t\t\tDSI_R32(&dsi_ctrl->hw,DSI_STATUS));
'''
    s = one(s, old_hw, new_hw, "F0 HW kickoff")

    wait_anchor = '''\tdsi_ctrl = container_of(work, struct dsi_ctrl, dma_cmd_wait);
\tdsi_hw_ops = dsi_ctrl->hw.ops;
\tSDE_EVT32(dsi_ctrl->cell_index, SDE_EVTLOG_FUNC_ENTRY);
'''
    wait_repl = '''\tdsi_ctrl = container_of(work, struct dsi_ctrl, dma_cmd_wait);
\tdsi_hw_ops = dsi_ctrl->hw.ops;
\tif (a52_p445_active())
\t\ta52_p446_mark(0x1a7U,(u32)atomic_read(&dsi_ctrl->dma_irq_trig),
\t\t\tDSI_R32(&dsi_ctrl->hw,DSI_STATUS));
\tSDE_EVT32(dsi_ctrl->cell_index, SDE_EVTLOG_FUNC_ENTRY);
'''
    s = one(s, wait_anchor, wait_repl, "F0 wait enter")

    s = one(
        s,
        '''\tif (atomic_read(&dsi_ctrl->dma_irq_trig)) {
\t\tif (a52_p445_active())
\t\t\ta52_p445_terminal(dsi_ctrl, 1);
''',
        '''\tif (atomic_read(&dsi_ctrl->dma_irq_trig)) {
\t\tif (a52_p445_active())
\t\t\ta52_p446_mark(0x1a8U,1U,(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
\t\tif (a52_p445_active())
\t\t\ta52_p445_terminal(dsi_ctrl, 1);
''',
        "F0 immediate done",
    )

    s = one(
        s,
        '''\tret = wait_for_completion_timeout(
\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,
\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));
\tif (a52_p445_active()) {
\t\ta52_p445_terminal(dsi_ctrl, ret);
''',
        '''\tret = wait_for_completion_timeout(
\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,
\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));
\tif (a52_p445_active()) {
\t\ta52_p446_mark(0x1a9U,(u32)ret,(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
\t\ta52_p445_terminal(dsi_ctrl, ret);
''',
        "F0 first wait result",
    )

    s = one(
        s,
        '''\t\tif (!ret && a52_p445_should_r1()) {
\t\t\ta52_p445_begin_r1(dsi_ctrl);
''',
        '''\t\tif (!ret && a52_p445_should_r1()) {
\t\t\ta52_p446_mark(0x1aaU,0U,DSI_R32(&dsi_ctrl->hw,DSI_STATUS));
\t\t\ta52_p445_begin_r1(dsi_ctrl);
''',
        "F0 R1 begin",
    )

    s = one(
        s,
        '''\t\t\telse
\t\t\t\tret = 0;
\t\t\ta52_p445_terminal(dsi_ctrl, ret);
''',
        '''\t\t\telse
\t\t\t\tret = 0;
\t\t\ta52_p446_mark(0x1abU,(u32)ret,
\t\t\t\t(u32)atomic_read(&dsi_ctrl->dma_irq_trig));
\t\t\ta52_p445_terminal(dsi_ctrl, ret);
''',
        "F0 R1 result",
    )

    s = one(
        s,
        '''error:
\tif (buffer)
''',
        '''error:
\tif (a52_p446h_f0)
\t\ta52_p446_mark(0x1acU,(u32)rc,(u32)a52_p445_active());
\tif (buffer)
''',
        "F0 tx return",
    )
    return s

def check(c: str, d: str, p: str, ctrl: str) -> None:
    required = (
        (c, MARK),
        (c, "static atomic_t p446h_late_safe = ATOMIC_INIT(0);"),
        (c, "!atomic_read(&p446h_late_safe)"),
        (c, "void a52_p446_set_late_safe(u32 enable)"),
        (d, "A52_PHASE446H_DISPLAY_HANDOFF"),
        (d, "a52_p446_mark(0x193U,1U,(u32)display->ctrl_count);"),
        (p, "A52_PHASE446H_PANEL_STEPS"),
        (p, "a52_p446_mark(0x198U,0U,(u32)vdd->samsung_splash_enabled);"),
        (p, "a52_p446_mark(0x19cU,(u32)rc,(u32)panel->panel_initialized);"),
        (ctrl, "A52_PHASE446H_F0_LIFECYCLE"),
        (ctrl, "a52_p446_mark(0x1a0U"),
        (ctrl, "a52_p446_mark(0x1a5U"),
        (ctrl, "a52_p446_mark(0x1a9U"),
        (ctrl, "a52_p446_mark(0x1abU"),
    )
    missing=[tok for text,tok in required if tok not in text]
    if missing:
        die("contract missing: " + ", ".join(missing))

def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    central=a.root/"drivers/a52_display/msm/a52_phase445.c"
    display=a.root/"drivers/a52_display/msm/dsi/dsi_display.c"
    panel=a.root/"drivers/a52_display/msm/dsi/dsi_panel.c"
    ctrl=a.root/"drivers/a52_display/msm/dsi/dsi_ctrl.c"
    for x in (central,display,panel,ctrl):
        if not x.is_file():
            die(f"missing {x}")
    if not a.check_only:
        central.write_text(patch_central(central.read_text(errors="replace")))
        display.write_text(patch_display(display.read_text(errors="replace")))
        panel.write_text(patch_panel(panel.read_text(errors="replace")))
        ctrl.write_text(patch_ctrl(ctrl.read_text(errors="replace")))
    check(
        central.read_text(errors="replace"),
        display.read_text(errors="replace"),
        panel.read_text(errors="replace"),
        ctrl.read_text(errors="replace"),
    )
    print("Phase446h GKI: panel/F0 lifecycle + late-safe recorder PASS")

if __name__=="__main__":
    main()
