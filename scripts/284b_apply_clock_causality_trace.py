#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

CLK = Path('drivers/a52_display/msm/dsi/dsi_clk_manager.c')
DISPLAY = Path('drivers/a52_display/msm/dsi/dsi_display.c')
MARK = 'A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1'


def replace_one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase284 causality: expected one {label} anchor, found {n}')
    return text.replace(old, new, 1)


def patch_display(text: str) -> str:
    if MARK in text:
        return text

    if 'A52_PHASE283_GOLDEN_HANDOFF_TRACE_V1' not in text:
        raise SystemExit('Phase284 causality: Phase283 display handoff prerequisite missing')
    if '#include <linux/a52_ack_secure_flight_recorder.h>' not in text:
        raise SystemExit('Phase284 causality: recorder include missing from reconstructed display source')

    text = replace_one(
        text,
        '#define DSI_CLOCK_BITRATE_RADIX 10\n#define MAX_TE_SOURCE_ID  2\n',
        '#define DSI_CLOCK_BITRATE_RADIX 10\n#define MAX_TE_SOURCE_ID  2\n\n'
        '/* ' + MARK + '\n'
        ' * Bounded, read-only trace of the rate producer. This is the GKI-side\n'
        ' * counterpart to Golden TGREF DERIVE and does not alter clock values.\n'
        ' */\n'
        'static unsigned int a52_p284_origin_logs;\n',
        'display clock-origin counter')

    old = (
        '\t\tSDE_EVT32(i, bit_rate, byte_clk_rate, pclk_rate);\n\n'
        '\t\tctrl->clk_freq.byte_clk_rate = byte_clk_rate;\n'
        '\t\tctrl->clk_freq.byte_intf_clk_rate = byte_intf_clk_rate;\n'
        '\t\tctrl->clk_freq.pix_clk_rate = pclk_rate;\n'
        '\t\trc = dsi_clk_set_link_frequencies(display->dsi_clk_handle,\n'
        '\t\t\tctrl->clk_freq, ctrl->cell_index);\n'
    )
    new = (
        '\t\tSDE_EVT32(i, bit_rate, byte_clk_rate, pclk_rate);\n\n'
        '\t\tif (a52_p284_origin_logs < 8) {\n'
        '\t\t\ta52_ackfr_record("P276 284O0 c=%d y=%u in=%u l=%u b=%u d=%u",\n'
        '\t\t\t\tctrl->cell_index, host_cfg->phy_type, bit_clk_rate,\n'
        '\t\t\t\tnum_of_lanes, bpp, host_cfg->byte_intf_clk_div);\n'
        '\t\t\ta52_ackfr_record("P276 284O1 bit=%llx lane=%llx b=%llx i=%llx p=%llx",\n'
        '\t\t\t\t(unsigned long long)bit_rate,\n'
        '\t\t\t\t(unsigned long long)bit_rate_per_lane,\n'
        '\t\t\t\t(unsigned long long)byte_clk_rate,\n'
        '\t\t\t\t(unsigned long long)byte_intf_clk_rate,\n'
        '\t\t\t\t(unsigned long long)pclk_rate);\n'
        '\t\t\ta52_p284_origin_logs++;\n'
        '\t\t}\n\n'
        '\t\tctrl->clk_freq.byte_clk_rate = byte_clk_rate;\n'
        '\t\tctrl->clk_freq.byte_intf_clk_rate = byte_intf_clk_rate;\n'
        '\t\tctrl->clk_freq.pix_clk_rate = pclk_rate;\n'
        '\t\trc = dsi_clk_set_link_frequencies(display->dsi_clk_handle,\n'
        '\t\t\tctrl->clk_freq, ctrl->cell_index);\n'
    )
    return replace_one(text, old, new, 'display derived-rate')


def patch_clk(text: str) -> str:
    if MARK in text:
        return text

    text = replace_one(
        text,
        '#include "dsi_clk.h"\n',
        '#include "dsi_clk.h"\n'
        '#include <linux/clk.h>\n'
        '#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'clock-manager recorder include')

    helper_anchor = '''struct dsi_core_clks {\n'''
    helper = '''/* A52_PHASE284_CLOCK_CAUSALITY_TRACE_V1
 * Bounded, read-only trace of how DSI link rates move from the cached request
 * into clock-framework parents and leaf clocks. No parent/rate/enable operation
 * is added, removed, retried or reordered.
 */
static unsigned int a52_p284_cache_logs;
static unsigned int a52_p284_setp_logs;
static unsigned int a52_p284_setb_logs;
static unsigned int a52_p284_parent_logs;
static unsigned int a52_p284_apply_logs;

static unsigned long a52_p284_clk_rate(struct clk *clk)
{
\treturn clk ? clk_get_rate(clk) : 0;
}

static unsigned long a52_p284_parent_rate(struct clk *clk)
{
\tstruct clk *parent = clk ? clk_get_parent(clk) : NULL;

\treturn parent ? clk_get_rate(parent) : 0;
}

struct dsi_core_clks {
'''
    text = replace_one(text, helper_anchor, helper, 'clock-manager helper')

    old = '''\tmemcpy(&mngr->link_clks[clk_mngr_index].freq, &freq,
\t\tsizeof(struct link_clk_freq));

\treturn rc;
}
'''
    new = '''\tmemcpy(&mngr->link_clks[clk_mngr_index].freq, &freq,
\t\tsizeof(struct link_clk_freq));

\tif (a52_p284_cache_logs < 8) {
\t\ta52_ackfr_record("P276 284M0 c=%u m=%d b=%llx p=%llx i=%llx e=%llx",
\t\t\tindex, clk_mngr_index,
\t\t\t(unsigned long long)freq.byte_clk_rate,
\t\t\t(unsigned long long)freq.pix_clk_rate,
\t\t\t(unsigned long long)freq.byte_intf_clk_rate,
\t\t\t(unsigned long long)freq.esc_clk_rate);
\t\ta52_p284_cache_logs++;
\t}

\treturn rc;
}
'''
    text = replace_one(text, old, new, 'cached-frequency propagation')

    old = '''\trc = clk_set_rate(mngr->link_clks[index].hs_clks.pixel_clk, pixel_clk);
\tif (rc)
\t\tDSI_ERR("failed to set clk rate for pixel clk, rc=%d\\n", rc);
\telse
\t\tmngr->link_clks[index].freq.pix_clk_rate = pixel_clk;

\treturn rc;
}
'''
    new = '''\trc = clk_set_rate(mngr->link_clks[index].hs_clks.pixel_clk, pixel_clk);
\tif (rc)
\t\tDSI_ERR("failed to set clk rate for pixel clk, rc=%d\\n", rc);
\telse
\t\tmngr->link_clks[index].freq.pix_clk_rate = pixel_clk;

\tif (a52_p284_setp_logs < 8) {
\t\ta52_ackfr_record("P276 284M1 c=%u req=%llx rc=%d a=%lx p=%lx",
\t\t\tindex, (unsigned long long)pixel_clk, rc,
\t\t\ta52_p284_clk_rate(mngr->link_clks[index].hs_clks.pixel_clk),
\t\t\ta52_p284_parent_rate(mngr->link_clks[index].hs_clks.pixel_clk));
\t\ta52_p284_setp_logs++;
\t}

\treturn rc;
}
'''
    text = replace_one(text, old, new, 'explicit pixel setter')

    old = '''\tif (mngr->link_clks[index].hs_clks.byte_intf_clk) {
\t\trc = clk_set_rate(mngr->link_clks[index].hs_clks.byte_intf_clk,
\t\t\t\t  byte_intf_clk);
\t\tif (rc)
\t\t\tDSI_ERR("failed to set clk rate for byte intf clk=%d\\n",
\t\t\t       rc);
\t\telse
\t\t\tmngr->link_clks[index].freq.byte_intf_clk_rate =
\t\t\t\t\t\t\t\tbyte_intf_clk;
\t}

\treturn rc;
}
'''
    new = '''\tif (mngr->link_clks[index].hs_clks.byte_intf_clk) {
\t\trc = clk_set_rate(mngr->link_clks[index].hs_clks.byte_intf_clk,
\t\t\t\t  byte_intf_clk);
\t\tif (rc)
\t\t\tDSI_ERR("failed to set clk rate for byte intf clk=%d\\n",
\t\t\t       rc);
\t\telse
\t\t\tmngr->link_clks[index].freq.byte_intf_clk_rate =
\t\t\t\t\t\t\t\tbyte_intf_clk;
\t}

\tif (a52_p284_setb_logs < 8) {
\t\ta52_ackfr_record("P276 284M2 c=%u rb=%llx ri=%llx rc=%d ab=%lx pb=%lx ai=%lx",
\t\t\tindex, (unsigned long long)byte_clk,
\t\t\t(unsigned long long)byte_intf_clk, rc,
\t\t\ta52_p284_clk_rate(mngr->link_clks[index].hs_clks.byte_clk),
\t\t\ta52_p284_parent_rate(mngr->link_clks[index].hs_clks.byte_clk),
\t\t\ta52_p284_clk_rate(mngr->link_clks[index].hs_clks.byte_intf_clk));
\t\ta52_p284_setb_logs++;
\t}

\treturn rc;
}
'''
    text = replace_one(text, old, new, 'explicit byte setter')

    old = '''int dsi_clk_update_parent(struct dsi_clk_link_set *parent,
\t\t\t  struct dsi_clk_link_set *child)
{
\tint rc = 0;

\trc = clk_set_parent(child->byte_clk, parent->byte_clk);
'''
    new = '''int dsi_clk_update_parent(struct dsi_clk_link_set *parent,
\t\t\t  struct dsi_clk_link_set *child)
{
\tint rc = 0;
\tbool a52_trace = a52_p284_parent_logs < 8;

\tif (a52_trace)
\t\ta52_ackfr_record("P276 284M3 cb=%lx bp=%lx tb=%lx cp=%lx pp=%lx tp=%lx",
\t\t\ta52_p284_clk_rate(child->byte_clk),
\t\t\ta52_p284_parent_rate(child->byte_clk),
\t\t\ta52_p284_clk_rate(parent->byte_clk),
\t\t\ta52_p284_clk_rate(child->pixel_clk),
\t\t\ta52_p284_parent_rate(child->pixel_clk),
\t\t\ta52_p284_clk_rate(parent->pixel_clk));

\trc = clk_set_parent(child->byte_clk, parent->byte_clk);
'''
    text = replace_one(text, old, new, 'clock-parent pre-state')

    old = '''error:
\treturn rc;
}

/**
 * dsi_clk_prepare_enable()'''
    new = '''error:
\tif (a52_trace) {
\t\ta52_ackfr_record("P276 284M4 rc=%d cb=%lx bp=%lx cp=%lx pp=%lx",
\t\t\trc, a52_p284_clk_rate(child->byte_clk),
\t\t\ta52_p284_parent_rate(child->byte_clk),
\t\t\ta52_p284_clk_rate(child->pixel_clk),
\t\t\ta52_p284_parent_rate(child->pixel_clk));
\t\ta52_p284_parent_logs++;
\t}
\treturn rc;
}

/**
 * dsi_clk_prepare_enable()'''
    text = replace_one(text, old, new, 'clock-parent post-state')

    old = '''\tif (mngr->is_cont_splash_enabled)
\t\treturn 0;

\trc = clk_set_rate(link_hs_clks->byte_clk,
\t\tl_clks->freq.byte_clk_rate);
\tif (rc) {
\t\tDSI_ERR("clk_set_rate failed for byte_clk rc = %d\\n", rc);
\t\tgoto error;
\t}

\trc = clk_set_rate(link_hs_clks->pixel_clk,
\t\tl_clks->freq.pix_clk_rate);
\tif (rc) {
\t\tDSI_ERR("clk_set_rate failed for pixel_clk rc = %d\\n", rc);
\t\tgoto error;
\t}

\t/*
\t * If byte_intf_clk is present, set rate for that too.
\t */
\tif (link_hs_clks->byte_intf_clk) {
\t\trc = clk_set_rate(link_hs_clks->byte_intf_clk,
\t\t\t\tl_clks->freq.byte_intf_clk_rate);
\t\tif (rc) {
\t\t\tDSI_ERR("set_rate failed for byte_intf_clk rc = %d\\n",
\t\t\t\trc);
\t\t\tgoto error;
\t\t}
\t}
error:
\treturn rc;
}
'''
    new = '''\tif (mngr->is_cont_splash_enabled) {
\t\tif (a52_p284_apply_logs < 12) {
\t\t\ta52_ackfr_record("P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx",
\t\t\t\tindex,
\t\t\t\t(unsigned long long)l_clks->freq.byte_clk_rate,
\t\t\t\t(unsigned long long)l_clks->freq.pix_clk_rate,
\t\t\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate);
\t\t\ta52_p284_apply_logs++;
\t\t}
\t\treturn 0;
\t}

\trc = clk_set_rate(link_hs_clks->byte_clk,
\t\tl_clks->freq.byte_clk_rate);
\tif (a52_p284_apply_logs < 12) {
\t\ta52_ackfr_record("P276 284M6 c=%d req=%llx rc=%d a=%lx p=%lx",
\t\t\tindex, (unsigned long long)l_clks->freq.byte_clk_rate, rc,
\t\t\ta52_p284_clk_rate(link_hs_clks->byte_clk),
\t\t\ta52_p284_parent_rate(link_hs_clks->byte_clk));
\t\ta52_p284_apply_logs++;
\t}
\tif (rc) {
\t\tDSI_ERR("clk_set_rate failed for byte_clk rc = %d\\n", rc);
\t\tgoto error;
\t}

\trc = clk_set_rate(link_hs_clks->pixel_clk,
\t\tl_clks->freq.pix_clk_rate);
\tif (a52_p284_apply_logs < 12) {
\t\ta52_ackfr_record("P276 284M7 c=%d req=%llx rc=%d a=%lx p=%lx",
\t\t\tindex, (unsigned long long)l_clks->freq.pix_clk_rate, rc,
\t\t\ta52_p284_clk_rate(link_hs_clks->pixel_clk),
\t\t\ta52_p284_parent_rate(link_hs_clks->pixel_clk));
\t\ta52_p284_apply_logs++;
\t}
\tif (rc) {
\t\tDSI_ERR("clk_set_rate failed for pixel_clk rc = %d\\n", rc);
\t\tgoto error;
\t}

\t/*
\t * If byte_intf_clk is present, set rate for that too.
\t */
\tif (link_hs_clks->byte_intf_clk) {
\t\trc = clk_set_rate(link_hs_clks->byte_intf_clk,
\t\t\t\tl_clks->freq.byte_intf_clk_rate);
\t\tif (a52_p284_apply_logs < 12) {
\t\t\ta52_ackfr_record("P276 284M8 c=%d req=%llx rc=%d a=%lx p=%lx",
\t\t\t\tindex,
\t\t\t\t(unsigned long long)l_clks->freq.byte_intf_clk_rate, rc,
\t\t\t\ta52_p284_clk_rate(link_hs_clks->byte_intf_clk),
\t\t\t\ta52_p284_parent_rate(link_hs_clks->byte_intf_clk));
\t\t\ta52_p284_apply_logs++;
\t\t}
\t\tif (rc) {
\t\t\tDSI_ERR("set_rate failed for byte_intf_clk rc = %d\\n",
\t\t\t\trc);
\t\t\tgoto error;
\t\t}
\t}
error:
\treturn rc;
}
'''
    text = replace_one(text, old, new, 'link HS rate application')
    return text


def validate(root: Path) -> None:
    clk = (root / CLK).read_text()
    display = (root / DISPLAY).read_text()
    for token in [
        MARK,
        'P276 284M0 c=%u m=%d b=%llx p=%llx i=%llx e=%llx',
        'P276 284M1 c=%u req=%llx rc=%d a=%lx p=%lx',
        'P276 284M2 c=%u rb=%llx ri=%llx rc=%d ab=%lx pb=%lx ai=%lx',
        'P276 284M3 cb=%lx bp=%lx tb=%lx cp=%lx pp=%lx tp=%lx',
        'P276 284M4 rc=%d cb=%lx bp=%lx cp=%lx pp=%lx',
        'P276 284M5 c=%d sp=1 b=%llx p=%llx i=%llx',
        'P276 284M6 c=%d req=%llx rc=%d a=%lx p=%lx',
        'P276 284M7 c=%d req=%llx rc=%d a=%lx p=%lx',
        'P276 284M8 c=%d req=%llx rc=%d a=%lx p=%lx',
    ]:
        if token not in clk:
            raise SystemExit('Phase284 causality clock check missing: ' + token)
    for token in [
        MARK,
        'P276 284O0 c=%d y=%u in=%u l=%u b=%u d=%u',
        'P276 284O1 bit=%llx lane=%llx b=%llx i=%llx p=%llx',
        'bit_rate_per_lane',
        'host_cfg->byte_intf_clk_div',
    ]:
        if token not in display:
            raise SystemExit('Phase284 causality display check missing: ' + token)
    print('Phase284 clock causality trace: PASS')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True, type=Path)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    if not args.check_only:
        clk_path = args.root / CLK
        display_path = args.root / DISPLAY
        clk_path.write_text(patch_clk(clk_path.read_text()))
        display_path.write_text(patch_display(display_path.read_text()))

    validate(args.root)


if __name__ == '__main__':
    main()
