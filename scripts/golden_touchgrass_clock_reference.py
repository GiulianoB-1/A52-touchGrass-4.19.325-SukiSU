#!/usr/bin/env python3
from pathlib import Path
import argparse

CLK = Path('techpack/display/msm/dsi/dsi_clk_manager.c')
CTRL = Path('techpack/display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_GOLDEN_TOUCHGRASS_CLOCK_REFERENCE_V1'


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
        ' * Read-only reference instrumentation for the hardware-working\n'
        ' * TouchGrass 4.19.200 display clock path. No clock state is changed.\n'
        ' */\n'
        'static unsigned long tgref_clk_rate(struct clk *clk)\n'
        '{\n'
        '\treturn clk ? clk_get_rate(clk) : 0;\n'
        '}\n\n'
        'struct dsi_clk_client_info {\n',
        'clock reference helper')

    text = once(
        text,
        '\tmemcpy(&mngr->link_clks[clk_mngr_index].freq, &freq,\n'
        '\t\tsizeof(struct link_clk_freq));\n\n'
        '\treturn rc;\n',
        '\tmemcpy(&mngr->link_clks[clk_mngr_index].freq, &freq,\n'
        '\t\tsizeof(struct link_clk_freq));\n\n'
        '\tpr_info("TGREF CACHE i=%u sp=%u req_b=%llu req_p=%llu req_i=%llu req_e=%llu act_b=%lu act_p=%lu act_i=%lu act_e=%lu\\n",\n'
        '\t\tindex, mngr->is_cont_splash_enabled ? 1 : 0,\n'
        '\t\t(unsigned long long)freq.byte_clk_rate,\n'
        '\t\t(unsigned long long)freq.pix_clk_rate,\n'
        '\t\t(unsigned long long)freq.byte_intf_clk_rate,\n'
        '\t\t(unsigned long long)freq.esc_clk_rate,\n'
        '\t\ttgref_clk_rate(mngr->link_clks[clk_mngr_index].hs_clks.byte_clk),\n'
        '\t\ttgref_clk_rate(mngr->link_clks[clk_mngr_index].hs_clks.pixel_clk),\n'
        '\t\ttgref_clk_rate(mngr->link_clks[clk_mngr_index].hs_clks.byte_intf_clk),\n'
        '\t\ttgref_clk_rate(mngr->link_clks[clk_mngr_index].lp_clks.esc_clk));\n\n'
        '\treturn rc;\n',
        'cache-rate trace')

    text = once(
        text,
        '\telse\n\t\tmngr->link_clks[index].freq.pix_clk_rate = pixel_clk;\n\n\treturn rc;\n',
        '\telse\n\t\tmngr->link_clks[index].freq.pix_clk_rate = pixel_clk;\n\n'
        '\tpr_info("TGREF SETP i=%u req=%llu rc=%d act=%lu\\n", index,\n'
        '\t\t(unsigned long long)pixel_clk, rc,\n'
        '\t\ttgref_clk_rate(mngr->link_clks[index].hs_clks.pixel_clk));\n\n'
        '\treturn rc;\n',
        'pixel setter trace')

    old = '''\tif (mngr->link_clks[index].hs_clks.byte_intf_clk) {\n\t\trc = clk_set_rate(mngr->link_clks[index].hs_clks.byte_intf_clk,\n\t\t\t\t  byte_intf_clk);\n\t\tif (rc)\n\t\t\tDSI_ERR("failed to set clk rate for byte intf clk=%d\\n",\n\t\t\t       rc);\n\t\telse\n\t\t\tmngr->link_clks[index].freq.byte_intf_clk_rate =\n\t\t\t\t\t\t\t\tbyte_intf_clk;\n\t}\n\n\treturn rc;\n}\n'''
    new = '''\tif (mngr->link_clks[index].hs_clks.byte_intf_clk) {\n\t\trc = clk_set_rate(mngr->link_clks[index].hs_clks.byte_intf_clk,\n\t\t\t\t  byte_intf_clk);\n\t\tif (rc)\n\t\t\tDSI_ERR("failed to set clk rate for byte intf clk=%d\\n",\n\t\t\t       rc);\n\t\telse\n\t\t\tmngr->link_clks[index].freq.byte_intf_clk_rate =\n\t\t\t\t\t\t\t\tbyte_intf_clk;\n\t}\n\n\tpr_info("TGREF SETB i=%u req_b=%llu req_i=%llu rc=%d act_b=%lu act_i=%lu\\n",\n\t\tindex, (unsigned long long)byte_clk,\n\t\t(unsigned long long)byte_intf_clk, rc,\n\t\ttgref_clk_rate(mngr->link_clks[index].hs_clks.byte_clk),\n\t\ttgref_clk_rate(mngr->link_clks[index].hs_clks.byte_intf_clk));\n\n\treturn rc;\n}\n'''
    text = once(text, old, new, 'byte setter trace')

    text = once(
        text,
        '\tif (mngr->is_cont_splash_enabled)\n\t\treturn 0;\n\n\trc = clk_set_rate(link_hs_clks->byte_clk,\n',
        '\tpr_info("TGREF APPLY0 i=%d sp=%u req_b=%llu req_p=%llu req_i=%llu act_b=%lu act_p=%lu act_i=%lu\\n",\n'
        '\t\tindex, mngr->is_cont_splash_enabled ? 1 : 0,\n'
        '\t\t(unsigned long long)l_clks->freq.byte_clk_rate,\n'
        '\t\t(unsigned long long)l_clks->freq.pix_clk_rate,\n'
        '\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate,\n'
        '\t\ttgref_clk_rate(link_hs_clks->byte_clk),\n'
        '\t\ttgref_clk_rate(link_hs_clks->pixel_clk),\n'
        '\t\ttgref_clk_rate(link_hs_clks->byte_intf_clk));\n\n'
        '\tif (mngr->is_cont_splash_enabled) {\n'
        '\t\tpr_info("TGREF SKIP i=%d reason=cont_splash act_b=%lu act_p=%lu act_i=%lu\\n",\n'
        '\t\t\tindex, tgref_clk_rate(link_hs_clks->byte_clk),\n'
        '\t\t\ttgref_clk_rate(link_hs_clks->pixel_clk),\n'
        '\t\t\ttgref_clk_rate(link_hs_clks->byte_intf_clk));\n'
        '\t\treturn 0;\n'
        '\t}\n\n\trc = clk_set_rate(link_hs_clks->byte_clk,\n',
        'continuous-splash apply trace')

    text = once(
        text,
        '\tif (link_hs_clks->byte_intf_clk) {\n\t\trc = clk_set_rate(link_hs_clks->byte_intf_clk,\n\t\t\t\tl_clks->freq.byte_intf_clk_rate);\n\t\tif (rc) {\n\t\t\tDSI_ERR("set_rate failed for byte_intf_clk rc = %d\\n",\n\t\t\t\trc);\n\t\t\tgoto error;\n\t\t}\n\t}\nerror:\n\treturn rc;\n}\n\nstatic int dsi_link_hs_clk_prepare',
        '\tif (link_hs_clks->byte_intf_clk) {\n\t\trc = clk_set_rate(link_hs_clks->byte_intf_clk,\n\t\t\t\tl_clks->freq.byte_intf_clk_rate);\n\t\tif (rc) {\n\t\t\tDSI_ERR("set_rate failed for byte_intf_clk rc = %d\\n",\n\t\t\t\trc);\n\t\t\tgoto error;\n\t\t}\n\t}\nerror:\n'
        '\tpr_info("TGREF APPLY1 i=%d rc=%d act_b=%lu act_p=%lu act_i=%lu\\n",\n'
        '\t\tindex, rc, tgref_clk_rate(link_hs_clks->byte_clk),\n'
        '\t\ttgref_clk_rate(link_hs_clks->pixel_clk),\n'
        '\t\ttgref_clk_rate(link_hs_clks->byte_intf_clk));\n'
        '\treturn rc;\n}\n\nstatic int dsi_link_hs_clk_prepare',
        'post-apply trace')

    text = once(
        text,
        '\tmngr = (struct dsi_clk_mngr *)clk_mgr;\n\tmngr->is_cont_splash_enabled = status;\n}\n',
        '\tmngr = (struct dsi_clk_mngr *)clk_mgr;\n'
        '\tpr_info("TGREF SPLASH old=%u new=%u b=%lu p=%lu i=%lu e=%lu\\n",\n'
        '\t\tmngr->is_cont_splash_enabled ? 1 : 0, status ? 1 : 0,\n'
        '\t\ttgref_clk_rate(mngr->link_clks[0].hs_clks.byte_clk),\n'
        '\t\ttgref_clk_rate(mngr->link_clks[0].hs_clks.pixel_clk),\n'
        '\t\ttgref_clk_rate(mngr->link_clks[0].hs_clks.byte_intf_clk),\n'
        '\t\ttgref_clk_rate(mngr->link_clks[0].lp_clks.esc_clk));\n'
        '\tmngr->is_cont_splash_enabled = status;\n}\n',
        'splash state trace')

    return text


def patch_ctrl(text: str) -> str:
    if MARK in text:
        return text

    text = once(
        text,
        '#define DSI_CTRL_TX_TO_MS     200\n',
        '#define DSI_CTRL_TX_TO_MS     200\n\n'
        '/* ' + MARK + ': command-boundary control measurement only. */\n',
        'controller marker')

    old = '''kickoff:\n\tdsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);\nerror:\n'''
    new = '''kickoff:\n\tif (msg->type == 0x29 && msg->tx_len == 3 && msg->tx_buf &&\n\t\t((const u8 *)msg->tx_buf)[0] == 0xf0 &&\n\t\t((const u8 *)msg->tx_buf)[1] == 0x5a &&\n\t\t((const u8 *)msg->tx_buf)[2] == 0x5a)\n\t\tpr_info("TGREF CMD PRE f=%x cache_b=%llu cache_p=%llu cache_i=%llu cache_e=%llu act_b=%lu act_p=%lu act_i=%lu act_e=%lu\\n",\n\t\t\t*flags, (unsigned long long)dsi_ctrl->clk_freq.byte_clk_rate,\n\t\t\t(unsigned long long)dsi_ctrl->clk_freq.pix_clk_rate,\n\t\t\t(unsigned long long)dsi_ctrl->clk_freq.byte_intf_clk_rate,\n\t\t\t(unsigned long long)dsi_ctrl->clk_freq.esc_clk_rate,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.byte_clk ? clk_get_rate(dsi_ctrl->clk_info.hs_link_clks.byte_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.pixel_clk ? clk_get_rate(dsi_ctrl->clk_info.hs_link_clks.pixel_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.byte_intf_clk ? clk_get_rate(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.lp_link_clks.esc_clk ? clk_get_rate(dsi_ctrl->clk_info.lp_link_clks.esc_clk) : 0);\n\tdsi_kickoff_msg_tx(dsi_ctrl, msg, &cmd, &cmd_mem, *flags);\n\tif (msg->type == 0x29 && msg->tx_len == 3 && msg->tx_buf &&\n\t\t((const u8 *)msg->tx_buf)[0] == 0xf0 &&\n\t\t((const u8 *)msg->tx_buf)[1] == 0x5a &&\n\t\t((const u8 *)msg->tx_buf)[2] == 0x5a)\n\t\tpr_info("TGREF CMD POST act_b=%lu act_p=%lu act_i=%lu act_e=%lu\\n",\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.byte_clk ? clk_get_rate(dsi_ctrl->clk_info.hs_link_clks.byte_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.pixel_clk ? clk_get_rate(dsi_ctrl->clk_info.hs_link_clks.pixel_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.hs_link_clks.byte_intf_clk ? clk_get_rate(dsi_ctrl->clk_info.hs_link_clks.byte_intf_clk) : 0,\n\t\t\tdsi_ctrl->clk_info.lp_link_clks.esc_clk ? clk_get_rate(dsi_ctrl->clk_info.lp_link_clks.esc_clk) : 0);\nerror:\n'''
    return once(text, old, new, 'F0 5A 5A command boundary trace')


def validate(clk: str, ctrl: str) -> None:
    for token in [MARK, 'TGREF CACHE', 'TGREF SETP', 'TGREF SETB', 'TGREF APPLY0',
                  'TGREF SKIP', 'TGREF APPLY1', 'TGREF SPLASH']:
        if token not in clk:
            raise SystemExit('missing clk marker: ' + token)
    for token in [MARK, 'TGREF CMD PRE', 'TGREF CMD POST', 'msg->type == 0x29']:
        if token not in ctrl:
            raise SystemExit('missing ctrl marker: ' + token)


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
    print('Golden TouchGrass clock-reference instrumentation validated')


if __name__ == '__main__':
    main()
