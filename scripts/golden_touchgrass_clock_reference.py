#!/usr/bin/env python3
from pathlib import Path
import argparse

CLK = Path('techpack/display/msm/dsi/dsi_clk_manager.c')
CTRL = Path('techpack/display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_GOLDEN_TOUCHGRASS_CLOCK_CHAIN_REFERENCE_V2'


def once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected one match, found {n}')
    return text.replace(old, new, 1)


def patch_clk(text: str) -> str:
    if MARK in text:
        return text

    text = once(
        text,
        'struct dsi_clk_client_info {\n',
        '/* ' + MARK + '\n'
        ' * Read-only, bounded instrumentation for the hardware-working\n'
        ' * TouchGrass 4.19.200 DSI clock construction path.\n'
        ' * Log setters/transitions only; do not continuously sample rates.\n'
        ' */\n'
        'static unsigned long tgref_clk_rate(struct clk *clk)\n'
        '{\n'
        '\treturn clk ? clk_get_rate(clk) : 0;\n'
        '}\n\n'
        'static unsigned long tgref_parent_rate(struct clk *clk)\n'
        '{\n'
        '\tstruct clk *parent = clk ? clk_get_parent(clk) : NULL;\n\n'
        '\treturn parent ? clk_get_rate(parent) : 0;\n'
        '}\n\n'
        'static bool tgref_cache_valid[MAX_DSI_CTRL];\n'
        'static struct link_clk_freq tgref_cache_last[MAX_DSI_CTRL];\n'
        'static unsigned int tgref_cache_logs;\n'
        'static unsigned int tgref_setp_logs;\n'
        'static unsigned int tgref_setb_logs;\n'
        'static unsigned int tgref_parent_logs;\n'
        'static unsigned int tgref_src_enable_logs;\n'
        'static unsigned int tgref_src_disable_logs;\n'
        'static unsigned int tgref_rate_skip_logs;\n'
        'static unsigned int tgref_rate_apply_logs;\n'
        'static unsigned int tgref_prepare_logs;\n'
        'static unsigned int tgref_enable_logs;\n'
        'static unsigned int tgref_stop_logs;\n'
        'static unsigned int tgref_mgr_logs;\n'
        'static unsigned int tgref_req_logs;\n'
        'static unsigned int tgref_splash_logs;\n\n'
        'struct dsi_clk_client_info {\n',
        'clock-chain helper and bounded counters')

    text = once(
        text,
        '\tmemcpy(&mngr->link_clks[clk_mngr_index].freq, &freq,\n'
        '\t\tsizeof(struct link_clk_freq));\n\n'
        '\treturn rc;\n',
        '\tmemcpy(&mngr->link_clks[clk_mngr_index].freq, &freq,\n'
        '\t\tsizeof(struct link_clk_freq));\n\n'
        '\tif (tgref_cache_logs < 8 &&\n'
        '\t    (!tgref_cache_valid[clk_mngr_index] ||\n'
        '\t     memcmp(&tgref_cache_last[clk_mngr_index], &freq,\n'
        '\t\t    sizeof(struct link_clk_freq)))) {\n'
        '\t\tpr_info("TGREF CACHE i=%u sp=%u b=%llu p=%llu i=%llu e=%llu\\n",\n'
        '\t\t\tindex, mngr->is_cont_splash_enabled ? 1 : 0,\n'
        '\t\t\t(unsigned long long)freq.byte_clk_rate,\n'
        '\t\t\t(unsigned long long)freq.pix_clk_rate,\n'
        '\t\t\t(unsigned long long)freq.byte_intf_clk_rate,\n'
        '\t\t\t(unsigned long long)freq.esc_clk_rate);\n'
        '\t\tmemcpy(&tgref_cache_last[clk_mngr_index], &freq,\n'
        '\t\t       sizeof(struct link_clk_freq));\n'
        '\t\ttgref_cache_valid[clk_mngr_index] = true;\n'
        '\t\ttgref_cache_logs++;\n'
        '\t}\n\n'
        '\treturn rc;\n',
        'changed cached-frequency trace')

    text = once(
        text,
        '\telse\n\t\tmngr->link_clks[index].freq.pix_clk_rate = pixel_clk;\n\n\treturn rc;\n',
        '\telse\n\t\tmngr->link_clks[index].freq.pix_clk_rate = pixel_clk;\n\n'
        '\tif (tgref_setp_logs < 8) {\n'
        '\t\tpr_info("TGREF SETP i=%u req=%llu rc=%d act=%lu par=%lu\\n",\n'
        '\t\t\tindex, (unsigned long long)pixel_clk, rc,\n'
        '\t\t\ttgref_clk_rate(mngr->link_clks[index].hs_clks.pixel_clk),\n'
        '\t\t\ttgref_parent_rate(mngr->link_clks[index].hs_clks.pixel_clk));\n'
        '\t\ttgref_setp_logs++;\n'
        '\t}\n\n'
        '\treturn rc;\n',
        'explicit pixel setter trace')

    old = '''\tif (mngr->link_clks[index].hs_clks.byte_intf_clk) {\n\t\trc = clk_set_rate(mngr->link_clks[index].hs_clks.byte_intf_clk,\n\t\t\t\t  byte_intf_clk);\n\t\tif (rc)\n\t\t\tDSI_ERR("failed to set clk rate for byte intf clk=%d\\n",\n\t\t\t       rc);\n\t\telse\n\t\t\tmngr->link_clks[index].freq.byte_intf_clk_rate =\n\t\t\t\t\t\t\t\tbyte_intf_clk;\n\t}\n\n\treturn rc;\n}\n'''
    new = '''\tif (mngr->link_clks[index].hs_clks.byte_intf_clk) {\n\t\trc = clk_set_rate(mngr->link_clks[index].hs_clks.byte_intf_clk,\n\t\t\t\t  byte_intf_clk);\n\t\tif (rc)\n\t\t\tDSI_ERR("failed to set clk rate for byte intf clk=%d\\n",\n\t\t\t       rc);\n\t\telse\n\t\t\tmngr->link_clks[index].freq.byte_intf_clk_rate =\n\t\t\t\t\t\t\t\tbyte_intf_clk;\n\t}\n\n\tif (tgref_setb_logs < 8) {\n\t\tpr_info("TGREF SETB i=%u req_b=%llu req_i=%llu rc=%d act_b=%lu par_b=%lu act_i=%lu par_i=%lu\\n",\n\t\t\tindex, (unsigned long long)byte_clk,\n\t\t\t(unsigned long long)byte_intf_clk, rc,\n\t\t\ttgref_clk_rate(mngr->link_clks[index].hs_clks.byte_clk),\n\t\t\ttgref_parent_rate(mngr->link_clks[index].hs_clks.byte_clk),\n\t\t\ttgref_clk_rate(mngr->link_clks[index].hs_clks.byte_intf_clk),\n\t\t\ttgref_parent_rate(mngr->link_clks[index].hs_clks.byte_intf_clk));\n\t\ttgref_setb_logs++;\n\t}\n\n\treturn rc;\n}\n'''
    text = once(text, old, new, 'explicit byte setter trace')

    text = once(
        text,
        '''int dsi_clk_update_parent(struct dsi_clk_link_set *parent,\n\t\t\t  struct dsi_clk_link_set *child)\n{\n\tint rc = 0;\n\n\trc = clk_set_parent(child->byte_clk, parent->byte_clk);\n''',
        '''int dsi_clk_update_parent(struct dsi_clk_link_set *parent,\n\t\t\t  struct dsi_clk_link_set *child)\n{\n\tint rc = 0;\n\tbool tgref_trace = tgref_parent_logs < 8;\n\n\tif (tgref_trace)\n\t\tpr_info("TGREF PARENT PRE cb=%lu cbp=%lu tb=%lu cp=%lu cpp=%lu tp=%lu\\n",\n\t\t\ttgref_clk_rate(child->byte_clk),\n\t\t\ttgref_parent_rate(child->byte_clk),\n\t\t\ttgref_clk_rate(parent->byte_clk),\n\t\t\ttgref_clk_rate(child->pixel_clk),\n\t\t\ttgref_parent_rate(child->pixel_clk),\n\t\t\ttgref_clk_rate(parent->pixel_clk));\n\n\trc = clk_set_parent(child->byte_clk, parent->byte_clk);\n''',
        'parent pre-trace')

    text = once(
        text,
        '''error:\n\treturn rc;\n}\n\n/**\n * dsi_clk_prepare_enable()''',
        '''error:\n\tif (tgref_trace) {\n\t\tpr_info("TGREF PARENT POST rc=%d cb=%lu cbp=%lu cp=%lu cpp=%lu\\n",\n\t\t\trc, tgref_clk_rate(child->byte_clk),\n\t\t\ttgref_parent_rate(child->byte_clk),\n\t\t\ttgref_clk_rate(child->pixel_clk),\n\t\t\ttgref_parent_rate(child->pixel_clk));\n\t\ttgref_parent_logs++;\n\t}\n\treturn rc;\n}\n\n/**\n * dsi_clk_prepare_enable()''',
        'parent post-trace')

    text = once(
        text,
        '''\trc = clk_prepare_enable(clk->pixel_clk);\n\tif (rc) {\n\t\tDSI_ERR("failed to enable pixel src clk %d\\n", rc);\n\t\treturn rc;\n\t}\n\n\treturn 0;\n}\n\n/**\n * dsi_clk_disable_unprepare()''',
        '''\trc = clk_prepare_enable(clk->pixel_clk);\n\tif (rc) {\n\t\tDSI_ERR("failed to enable pixel src clk %d\\n", rc);\n\t\treturn rc;\n\t}\n\n\tif (tgref_src_enable_logs < 8) {\n\t\tpr_info("TGREF SRC_ON b=%lu bp=%lu p=%lu pp=%lu\\n",\n\t\t\ttgref_clk_rate(clk->byte_clk), tgref_parent_rate(clk->byte_clk),\n\t\t\ttgref_clk_rate(clk->pixel_clk), tgref_parent_rate(clk->pixel_clk));\n\t\ttgref_src_enable_logs++;\n\t}\n\n\treturn 0;\n}\n\n/**\n * dsi_clk_disable_unprepare()''',
        'source-clock enable trace')

    text = once(
        text,
        '''void dsi_clk_disable_unprepare(struct dsi_clk_link_set *clk)\n{\n\tclk_disable_unprepare(clk->pixel_clk);\n\tclk_disable_unprepare(clk->byte_clk);\n}\n''',
        '''void dsi_clk_disable_unprepare(struct dsi_clk_link_set *clk)\n{\n\tif (tgref_src_disable_logs < 8) {\n\t\tpr_info("TGREF SRC_OFF b=%lu bp=%lu p=%lu pp=%lu\\n",\n\t\t\ttgref_clk_rate(clk->byte_clk), tgref_parent_rate(clk->byte_clk),\n\t\t\ttgref_clk_rate(clk->pixel_clk), tgref_parent_rate(clk->pixel_clk));\n\t\ttgref_src_disable_logs++;\n\t}\n\tclk_disable_unprepare(clk->pixel_clk);\n\tclk_disable_unprepare(clk->byte_clk);\n}\n''',
        'source-clock disable trace')

    text = once(
        text,
        '''\tif (mngr->is_cont_splash_enabled)\n\t\treturn 0;\n\n\trc = clk_set_rate(link_hs_clks->byte_clk,\n\t\tl_clks->freq.byte_clk_rate);\n\tif (rc) {\n\t\tDSI_ERR("clk_set_rate failed for byte_clk rc = %d\\n", rc);\n\t\tgoto error;\n\t}\n\n\trc = clk_set_rate(link_hs_clks->pixel_clk,\n\t\tl_clks->freq.pix_clk_rate);\n\tif (rc) {\n\t\tDSI_ERR("clk_set_rate failed for pixel_clk rc = %d\\n", rc);\n\t\tgoto error;\n\t}\n\n\t/*\n\t * If byte_intf_clk is present, set rate for that too.\n\t */\n\tif (link_hs_clks->byte_intf_clk) {\n\t\trc = clk_set_rate(link_hs_clks->byte_intf_clk,\n\t\t\t\tl_clks->freq.byte_intf_clk_rate);\n\t\tif (rc) {\n\t\t\tDSI_ERR("set_rate failed for byte_intf_clk rc = %d\\n",\n\t\t\t\trc);\n\t\t\tgoto error;\n\t\t}\n\t}\nerror:\n\treturn rc;\n}\n''',
        '''\tif (mngr->is_cont_splash_enabled) {\n\t\tif (tgref_rate_skip_logs < 4) {\n\t\t\tpr_info("TGREF RATE_SKIP i=%d b=%llu p=%llu i=%llu act_b=%lu act_p=%lu act_i=%lu\\n",\n\t\t\t\tindex, (unsigned long long)l_clks->freq.byte_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.pix_clk_rate,\n\t\t\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate,\n\t\t\t\ttgref_clk_rate(link_hs_clks->byte_clk),\n\t\t\t\ttgref_clk_rate(link_hs_clks->pixel_clk),\n\t\t\t\ttgref_clk_rate(link_hs_clks->byte_intf_clk));\n\t\t\ttgref_rate_skip_logs++;\n\t\t}\n\t\treturn 0;\n\t}\n\n\t{\n\t\tbool tgref_trace = tgref_rate_apply_logs < 4;\n\n\t\tif (tgref_trace)\n\t\t\ttgref_rate_apply_logs++;\n\n\t\trc = clk_set_rate(link_hs_clks->byte_clk,\n\t\t\tl_clks->freq.byte_clk_rate);\n\t\tif (tgref_trace)\n\t\t\tpr_info("TGREF RATE_B i=%d req=%llu rc=%d act=%lu par=%lu\\n",\n\t\t\t\tindex, (unsigned long long)l_clks->freq.byte_clk_rate,\n\t\t\t\trc, tgref_clk_rate(link_hs_clks->byte_clk),\n\t\t\t\ttgref_parent_rate(link_hs_clks->byte_clk));\n\t\tif (rc) {\n\t\t\tDSI_ERR("clk_set_rate failed for byte_clk rc = %d\\n", rc);\n\t\t\tgoto error;\n\t\t}\n\n\t\trc = clk_set_rate(link_hs_clks->pixel_clk,\n\t\t\tl_clks->freq.pix_clk_rate);\n\t\tif (tgref_trace)\n\t\t\tpr_info("TGREF RATE_P i=%d req=%llu rc=%d act=%lu par=%lu\\n",\n\t\t\t\tindex, (unsigned long long)l_clks->freq.pix_clk_rate,\n\t\t\t\trc, tgref_clk_rate(link_hs_clks->pixel_clk),\n\t\t\t\ttgref_parent_rate(link_hs_clks->pixel_clk));\n\t\tif (rc) {\n\t\t\tDSI_ERR("clk_set_rate failed for pixel_clk rc = %d\\n", rc);\n\t\t\tgoto error;\n\t\t}\n\n\t\t/* If byte_intf_clk is present, set rate for that too. */\n\t\tif (link_hs_clks->byte_intf_clk) {\n\t\t\trc = clk_set_rate(link_hs_clks->byte_intf_clk,\n\t\t\t\tl_clks->freq.byte_intf_clk_rate);\n\t\t\tif (tgref_trace)\n\t\t\t\tpr_info("TGREF RATE_I i=%d req=%llu rc=%d act=%lu par=%lu\\n",\n\t\t\t\t\tindex,\n\t\t\t\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate,\n\t\t\t\t\trc, tgref_clk_rate(link_hs_clks->byte_intf_clk),\n\t\t\t\t\ttgref_parent_rate(link_hs_clks->byte_intf_clk));\n\t\t\tif (rc) {\n\t\t\t\tDSI_ERR("set_rate failed for byte_intf_clk rc = %d\\n",\n\t\t\t\t\trc);\n\t\t\t\tgoto error;\n\t\t\t}\n\t\t}\n\t}\nerror:\n\treturn rc;\n}\n''',
        'bounded splash/set-rate chain trace')

    text = once(
        text,
        '''\tif (link_hs_clks->byte_intf_clk) {\n\t\trc = clk_prepare(link_hs_clks->byte_intf_clk);\n\t\tif (rc) {\n\t\t\tDSI_ERR("Failed to prepare dsi byte intf clk, rc=%d\\n",\n\t\t\t\trc);\n\t\t\tgoto byte_intf_clk_err;\n\t\t}\n\t}\n\n\treturn rc;\n''',
        '''\tif (link_hs_clks->byte_intf_clk) {\n\t\trc = clk_prepare(link_hs_clks->byte_intf_clk);\n\t\tif (rc) {\n\t\t\tDSI_ERR("Failed to prepare dsi byte intf clk, rc=%d\\n",\n\t\t\t\trc);\n\t\t\tgoto byte_intf_clk_err;\n\t\t}\n\t}\n\n\tif (tgref_prepare_logs < 4) {\n\t\tpr_info("TGREF PREP b=%lu p=%lu i=%lu\\n",\n\t\t\ttgref_clk_rate(link_hs_clks->byte_clk),\n\t\t\ttgref_clk_rate(link_hs_clks->pixel_clk),\n\t\t\ttgref_clk_rate(link_hs_clks->byte_intf_clk));\n\t\ttgref_prepare_logs++;\n\t}\n\n\treturn rc;\n''',
        'HS prepare trace')

    text = once(
        text,
        '''\tif (link_hs_clks->byte_intf_clk) {\n\t\trc = clk_enable(link_hs_clks->byte_intf_clk);\n\t\tif (rc) {\n\t\t\tDSI_ERR("Failed to enable dsi byte intf clk, rc=%d\\n",\n\t\t\t\trc);\n\t\t\tgoto byte_intf_clk_err;\n\t\t}\n\t}\n\n\treturn rc;\n''',
        '''\tif (link_hs_clks->byte_intf_clk) {\n\t\trc = clk_enable(link_hs_clks->byte_intf_clk);\n\t\tif (rc) {\n\t\t\tDSI_ERR("Failed to enable dsi byte intf clk, rc=%d\\n",\n\t\t\t\trc);\n\t\t\tgoto byte_intf_clk_err;\n\t\t}\n\t}\n\n\tif (tgref_enable_logs < 4) {\n\t\tpr_info("TGREF ENABLE b=%lu p=%lu i=%lu\\n",\n\t\t\ttgref_clk_rate(link_hs_clks->byte_clk),\n\t\t\ttgref_clk_rate(link_hs_clks->pixel_clk),\n\t\t\ttgref_clk_rate(link_hs_clks->byte_intf_clk));\n\t\ttgref_enable_logs++;\n\t}\n\n\treturn rc;\n''',
        'HS enable trace')

    text = once(
        text,
        '''\tl_clks = container_of(link_hs_clks, struct dsi_link_clks, hs_clks);\n\n\tdsi_link_hs_clk_disable(link_hs_clks);\n''',
        '''\tl_clks = container_of(link_hs_clks, struct dsi_link_clks, hs_clks);\n\n\tif (tgref_stop_logs < 8) {\n\t\tpr_info("TGREF HS_STOP b=%lu p=%lu i=%lu\\n",\n\t\t\ttgref_clk_rate(link_hs_clks->byte_clk),\n\t\t\ttgref_clk_rate(link_hs_clks->pixel_clk),\n\t\t\ttgref_clk_rate(link_hs_clks->byte_intf_clk));\n\t\ttgref_stop_logs++;\n\t}\n\n\tdsi_link_hs_clk_disable(link_hs_clks);\n''',
        'HS stop trace')

    text = once(
        text,
        '''\tif (c_clks || l_clks) {\n\t\trc = dsi_update_clk_state(mngr, c_clks, new_core_clk_state,\n''',
        '''\tif ((c_clks || l_clks) && tgref_mgr_logs < 16) {\n\t\tpr_info("TGREF MGR core=%u->%u link=%u->%u sp=%u\\n",\n\t\t\told_c_clk_state, new_core_clk_state,\n\t\t\told_l_clk_state, new_link_clk_state,\n\t\t\tmngr->is_cont_splash_enabled ? 1 : 0);\n\t\ttgref_mgr_logs++;\n\t}\n\n\tif (c_clks || l_clks) {\n\t\trc = dsi_update_clk_state(mngr, c_clks, new_core_clk_state,\n''',
        'manager state transition trace')

    text = once(
        text,
        '''\tif (changed) {\n\t\trc = dsi_recheck_clk_state(mngr);\n''',
        '''\tif (changed && tgref_req_logs < 16) {\n\t\tpr_info("TGREF REQ client=%s clk=%x req=%u cr=%u cs=%u lr=%u ls=%u\\n",\n\t\t\tc->name, clk, state, c->core_refcount, c->core_clk_state,\n\t\t\tc->link_refcount, c->link_clk_state);\n\t\ttgref_req_logs++;\n\t}\n\n\tif (changed) {\n\t\trc = dsi_recheck_clk_state(mngr);\n''',
        'client request transition trace')

    text = once(
        text,
        '''\tmngr = (struct dsi_clk_mngr *)clk_mgr;\n\tmngr->is_cont_splash_enabled = status;\n}\n''',
        '''\tmngr = (struct dsi_clk_mngr *)clk_mgr;\n\tif (mngr->is_cont_splash_enabled != status && tgref_splash_logs < 4) {\n\t\tpr_info("TGREF SPLASH %u->%u\\n",\n\t\t\tmngr->is_cont_splash_enabled ? 1 : 0, status ? 1 : 0);\n\t\ttgref_splash_logs++;\n\t}\n\tmngr->is_cont_splash_enabled = status;\n}\n''',
        'splash transition-only trace')

    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text

    text = once(
        text,
        '#define DSI_CTRL_TX_TO_MS     200\n',
        '#define DSI_CTRL_TX_TO_MS     200\n\n'
        '/* ' + MARK + ': one-shot first F0 5A 5A command boundary. */\n'
        'static bool tgref_f0_armed;\n'
        'static bool tgref_f0_done;\n',
        'controller marker and one-shot state')

    old = '''kickoff:\n\tdsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);\nerror:\n'''
    new = '''kickoff:\n\tif (!tgref_f0_done && !tgref_f0_armed && msg->type == 0x29 &&\n\t    msg->tx_len == 3 && msg->tx_buf &&\n\t    ((const u8 *)msg->tx_buf)[0] == 0xf0 &&\n\t    ((const u8 *)msg->tx_buf)[1] == 0x5a &&\n\t    ((const u8 *)msg->tx_buf)[2] == 0x5a) {\n\t\ttgref_f0_armed = true;\n\t\tpr_info("TGREF CMD PRE f=%x cache_b=%llu cache_p=%llu cache_i=%llu act_b=%lu act_p=%lu act_i=%lu\\n",\n\t\t\t*flags, (unsigned long long)dsi_ctrl->clk_freq.byte_clk_rate,\n\t\t\t(unsigned long long)dsi_ctrl->clk_freq.pix_clk_rate,\n\t\t\t(unsigned long long)dsi_ctrl->clk_freq.byte_intf_clk_rate,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.byte_clk ?\n\t\t\t\tclk_get_rate(dsi_ctrl->clk_info.hs_link_clks.byte_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.pixel_clk ?\n\t\t\t\tclk_get_rate(dsi_ctrl->clk_info.hs_link_clks.pixel_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.byte_intf_clk ?\n\t\t\t\tclk_get_rate(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk) : 0);\n\t}\n\n\tdsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);\n\n\tif (tgref_f0_armed && !tgref_f0_done && msg->type == 0x29 &&\n\t    msg->tx_len == 3 && msg->tx_buf &&\n\t    ((const u8 *)msg->tx_buf)[0] == 0xf0 &&\n\t    ((const u8 *)msg->tx_buf)[1] == 0x5a &&\n\t    ((const u8 *)msg->tx_buf)[2] == 0x5a) {\n\t\tpr_info("TGREF CMD POST act_b=%lu act_p=%lu act_i=%lu\\n",\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.byte_clk ?\n\t\t\t\tclk_get_rate(dsi_ctrl->clk_info.hs_link_clks.byte_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.pixel_clk ?\n\t\t\t\tclk_get_rate(dsi_ctrl->clk_info.hs_link_clks.pixel_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.byte_intf_clk ?\n\t\t\t\tclk_get_rate(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk) : 0);\n\t\ttgref_f0_done = true;\n\t\ttgref_f0_armed = false;\n\t}\nerror:\n'''
    return once(text, old, new, 'one-shot F0 5A 5A command boundary trace')


def validate(clk: str, ctrl: str) -> None:
    for token in [
        MARK, 'TGREF CACHE', 'TGREF SETP', 'TGREF SETB',
        'TGREF PARENT PRE', 'TGREF PARENT POST', 'TGREF SRC_ON',
        'TGREF SRC_OFF', 'TGREF RATE_SKIP', 'TGREF RATE_B',
        'TGREF RATE_P', 'TGREF RATE_I', 'TGREF PREP', 'TGREF ENABLE',
        'TGREF HS_STOP', 'TGREF MGR', 'TGREF REQ', 'TGREF SPLASH'
    ]:
        if token not in clk:
            raise SystemExit('missing clk marker: ' + token)
    for token in [MARK, 'TGREF CMD PRE', 'TGREF CMD POST', 'tgref_f0_done']:
        if token not in ctrl:
            raise SystemExit('missing ctrl marker: ' + token)
    for obsolete in ['TGREF APPLY0', 'TGREF APPLY1']:
        if obsolete in clk:
            raise SystemExit('obsolete noisy marker still present: ' + obsolete)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    clk_path = args.root / CLK
    ctrl_path = args.root / CTRL
    clk = clk_path.read_text()
    ctrl = ctrl_path.read_text()
    if not args.check_only:
        clk = patch_clk(clk)
        ctrl = patch_ctrl(ctrl)
        clk_path.write_text(clk)
        ctrl_path.write_text(ctrl)
    validate(clk, ctrl)
    print('Golden TouchGrass clock-chain reference V2 instrumentation validated')


if __name__ == '__main__':
    main()
