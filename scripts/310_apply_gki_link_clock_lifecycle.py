#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

CLK_REL = Path('drivers/a52_display/msm/dsi/dsi_clk_manager.c')
PHY_REL = Path('drivers/a52_display/msm/dsi/dsi_phy.c')
PLL_REL = Path('drivers/a52_display/pll/dsi_pll_10nm.c')
MARK = 'A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V2'
P309 = 'A52_PHASE309_GKI_CLAMP_RELEASE_LATCH_V1'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'Phase310 {label}: expected exactly one match, found {n}')
    return text.replace(old, new, 1)


def function_span(text: str, name: str) -> tuple[int, int, int]:
    ret = r'(?:int|void|long|unsigned\s+long)'
    pat = re.compile(r'(?m)^(?:static\s+)?' + ret + r'\s+' + re.escape(name) + r'\s*\([^;]*?\)\s*\{')
    matches = list(pat.finditer(text))
    if len(matches) != 1:
        raise SystemExit(f'Phase310 {name}: expected one definition, found {len(matches)}')
    m = matches[0]
    open_brace = text.find('{', m.start(), m.end())
    depth = 0
    i = open_brace
    state = 'code'
    quote = ''
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ''
        if state == 'code':
            if c == '/' and n == '*':
                state = 'block'; i += 2; continue
            if c == '/' and n == '/':
                state = 'line'; i += 2; continue
            if c in ('\"', "'"):
                state = 'string'; quote = c; i += 1; continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return m.start(), open_brace, i + 1
            i += 1
        elif state == 'block':
            if c == '*' and n == '/':
                state = 'code'; i += 2
            else:
                i += 1
        elif state == 'line':
            if c == '\n':
                state = 'code'
            i += 1
        else:
            if c == '\\':
                i += 2
            elif c == quote:
                state = 'code'; i += 1
            else:
                i += 1
    raise SystemExit(f'Phase310 {name}: unterminated body')


def inject_entry(text: str, name: str, code: str) -> str:
    _start, brace, _end = function_span(text, name)
    return text[:brace + 1] + '\n' + code + text[brace + 1:]


def rewrite_returns_rc(text: str, name: str, before: str) -> str:
    start, _brace, end = function_span(text, name)
    body = text[start:end]
    count = body.count('\treturn rc;')
    if count < 1:
        raise SystemExit(f'Phase310 {name}: no return rc anchor')
    body = body.replace('\treturn rc;', before + '\n\treturn rc;')
    return text[:start] + body + text[end:]


def replace_in_function(text: str, name: str, old: str, new: str, label: str) -> str:
    start, _brace, end = function_span(text, name)
    body = text[start:end]
    n = body.count(old)
    if n != 1:
        raise SystemExit(f'Phase310 {name} {label}: expected one match, found {n}')
    body = body.replace(old, new, 1)
    return text[:start] + body + text[end:]


def inject_before_last(text: str, name: str, anchor: str, code: str) -> str:
    start, _brace, end = function_span(text, name)
    body = text[start:end]
    pos = body.rfind(anchor)
    if pos < 0:
        raise SystemExit(f'Phase310 {name}: final anchor missing: {anchor!r}')
    body = body[:pos] + code + body[pos:]
    return text[:start] + body + text[end:]


def patch_clk(text: str) -> str:
    if MARK in text:
        return text
    if 'dsi_clk_update_link_clk_state' not in text or 'dsi_link_hs_clk_set_rate' not in text:
        raise SystemExit('Phase310 DSI clock manager shape not recognized')

    inc = '#include "dsi_clk.h"\n'
    if inc not in text:
        raise SystemExit('Phase310 dsi_clk.h include anchor missing')
    text = text.replace(
        inc,
        inc + '#include <linux/atomic.h>\n#include <linux/clk.h>\n#include <linux/string.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        1,
    )

    anchor = 'struct dsi_clk_mngr {\n'
    state = '''/* A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V2
 * Sticky software-only history for the DSI link-clock path. In addition to
 * lifecycle counters, q0/q1/q2 read the existing clock objects for effective
 * rate, prepare/enable state, and whether the byte/pixel ancestry reaches the
 * expected DSI0 PHY PLL outputs. No clock operation is added or removed.
 */
static atomic_t a52_p310_sr_in;
static atomic_t a52_p310_sr_run;
static atomic_t a52_p310_sr_skip;
static atomic_t a52_p310_sr_ok;
static atomic_t a52_p310_sr_rc = ATOMIC_INIT(-999);
static atomic_t a52_p310_sr_idx = ATOMIC_INIT(-1);
static atomic_t a52_p310_prepare_in;
static atomic_t a52_p310_prepare_ok;
static atomic_t a52_p310_prepare_rc = ATOMIC_INIT(-999);
static atomic_t a52_p310_enable_in;
static atomic_t a52_p310_enable_ok;
static atomic_t a52_p310_enable_rc = ATOMIC_INIT(-999);
static atomic_t a52_p310_disable_in;
static atomic_t a52_p310_unprepare_in;
static atomic_t a52_p310_hs_start;
static atomic_t a52_p310_hs_stop;
static atomic_t a52_p310_lp_start;
static atomic_t a52_p310_lp_stop;
static atomic_t a52_p310_update_in;
static atomic_t a52_p310_update_ok;
static atomic_t a52_p310_update_rc = ATOMIC_INIT(-999);
static atomic_t a52_p310_last_type = ATOMIC_INIT(-1);
static atomic_t a52_p310_last_state = ATOMIC_INIT(-1);
static atomic_t a52_p310_last_enable = ATOMIC_INIT(-1);
static struct dsi_link_hs_clk_info *a52_p310_last_hs;
static struct dsi_link_lp_clk_info *a52_p310_last_lp;

'''
    text = replace_once(text, anchor, state + anchor, 'state helper')

    helper_anchor = 'static int _get_clk_mngr_index(struct dsi_clk_mngr *mngr,\n'
    helper = r'''static int a52_p310_clk_chain_has(struct clk *clk, const char *expected)
{
	int depth;

	if (!clk || !expected)
		return -1;
	for (depth = 0; clk && depth < 4; depth++) {
		const char *name = __clk_get_name(clk);

		if (name && !strcmp(name, expected))
			return 1;
		clk = clk_get_parent(clk);
	}
	return 0;
}

static u32 a52_p310_clk_mask(struct dsi_link_hs_clk_info *hs,
		struct dsi_link_lp_clk_info *lp, bool prepared)
{
	u32 mask = 0;

	if (hs && hs->byte_clk &&
	    (prepared ? __clk_is_prepared(hs->byte_clk) : __clk_is_enabled(hs->byte_clk)))
		mask |= BIT(0);
	if (hs && hs->pixel_clk &&
	    (prepared ? __clk_is_prepared(hs->pixel_clk) : __clk_is_enabled(hs->pixel_clk)))
		mask |= BIT(1);
	if (hs && hs->byte_intf_clk &&
	    (prepared ? __clk_is_prepared(hs->byte_intf_clk) : __clk_is_enabled(hs->byte_intf_clk)))
		mask |= BIT(2);
	if (lp && lp->esc_clk &&
	    (prepared ? __clk_is_prepared(lp->esc_clk) : __clk_is_enabled(lp->esc_clk)))
		mask |= BIT(3);
	return mask;
}

void a52_p310_clk_snapshot(unsigned int index, unsigned int point)
{
	struct dsi_link_hs_clk_info *hs = READ_ONCE(a52_p310_last_hs);
	struct dsi_link_lp_clk_info *lp = READ_ONCE(a52_p310_last_lp);
	unsigned long br = 0, pr = 0, ir = 0, er = 0;
	int bp = -1, pp = -1;
	u32 em, pm;

	if (hs) {
		if (hs->byte_clk) {
			br = clk_get_rate(hs->byte_clk);
			bp = a52_p310_clk_chain_has(hs->byte_clk,
				"dsi0_phy_pll_out_byteclk");
		}
		if (hs->pixel_clk) {
			pr = clk_get_rate(hs->pixel_clk);
			pp = a52_p310_clk_chain_has(hs->pixel_clk,
				"dsi0_phy_pll_out_dsiclk");
		}
		if (hs->byte_intf_clk)
			ir = clk_get_rate(hs->byte_intf_clk);
	}
	if (lp && lp->esc_clk)
		er = clk_get_rate(lp->esc_clk);

	em = a52_p310_clk_mask(hs, lp, false);
	pm = a52_p310_clk_mask(hs, lp, true);

	a52_ackfr_record("P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d",
		point, index,
		atomic_read(&a52_p310_sr_in), atomic_read(&a52_p310_sr_run),
		atomic_read(&a52_p310_sr_skip), atomic_read(&a52_p310_sr_ok),
		atomic_read(&a52_p310_sr_rc), atomic_read(&a52_p310_sr_idx));
	a52_ackfr_record("P276 310H q=%u pr=%d po=%d pc=%d en=%d eo=%d ec=%d hs=%d hp=%d",
		point, atomic_read(&a52_p310_prepare_in),
		atomic_read(&a52_p310_prepare_ok), atomic_read(&a52_p310_prepare_rc),
		atomic_read(&a52_p310_enable_in), atomic_read(&a52_p310_enable_ok),
		atomic_read(&a52_p310_enable_rc), atomic_read(&a52_p310_hs_start),
		atomic_read(&a52_p310_hs_stop));
	a52_ackfr_record("P276 310U q=%u di=%d up=%d ls=%d lp=%d ui=%d uo=%d ur=%d t=%d s=%d e=%d",
		point, atomic_read(&a52_p310_disable_in),
		atomic_read(&a52_p310_unprepare_in), atomic_read(&a52_p310_lp_start),
		atomic_read(&a52_p310_lp_stop), atomic_read(&a52_p310_update_in),
		atomic_read(&a52_p310_update_ok), atomic_read(&a52_p310_update_rc),
		atomic_read(&a52_p310_last_type), atomic_read(&a52_p310_last_state),
		atomic_read(&a52_p310_last_enable));
	a52_ackfr_record("P276 310R q=%u br=%lu pr=%lu ir=%lu er=%lu",
		point, br, pr, ir, er);
	a52_ackfr_record("P276 310E q=%u em=%x pm=%x bp=%d pp=%d",
		point, em, pm, bp, pp);
}

'''
    text = replace_once(text, helper_anchor, helper + helper_anchor, 'clock object snapshot helper')

    text = inject_entry(text, 'dsi_link_hs_clk_set_rate',
        '\tWRITE_ONCE(a52_p310_last_hs, link_hs_clks);\n'
        '\tatomic_inc(&a52_p310_sr_in);\n\tatomic_set(&a52_p310_sr_idx, index);')

    splash_old = '''\tif (mngr->is_cont_splash_enabled)\n\t\treturn 0;\n\n\trc = clk_set_rate(link_hs_clks->byte_clk,\n'''
    splash_new = '''\tif (mngr->is_cont_splash_enabled) {\n\t\tatomic_inc(&a52_p310_sr_skip);\n\t\tif (atomic_read(&a52_p310_sr_skip) == 1)\n\t\t\ta52_ackfr_record("P276 310CE e=1 i=%d rc=0", index);\n\t\treturn 0;\n\t}\n\n\tatomic_inc(&a52_p310_sr_run);\n\trc = clk_set_rate(link_hs_clks->byte_clk,\n'''
    text = replace_once(text, splash_old, splash_new, 'continuous-splash set-rate gate')
    text = rewrite_returns_rc(text, 'dsi_link_hs_clk_set_rate',
        '\tatomic_set(&a52_p310_sr_rc, rc);\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_sr_ok);')

    text = inject_entry(text, 'dsi_link_hs_clk_prepare', '\tatomic_inc(&a52_p310_prepare_in);')
    text = rewrite_returns_rc(text, 'dsi_link_hs_clk_prepare',
        '\tatomic_set(&a52_p310_prepare_rc, rc);\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_prepare_ok);')

    text = inject_entry(text, 'dsi_link_hs_clk_enable', '\tatomic_inc(&a52_p310_enable_in);')
    text = rewrite_returns_rc(text, 'dsi_link_hs_clk_enable',
        '\tatomic_set(&a52_p310_enable_rc, rc);\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_enable_ok);')

    text = inject_entry(text, 'dsi_link_hs_clk_disable', '\tatomic_inc(&a52_p310_disable_in);')
    text = inject_entry(text, 'dsi_link_hs_clk_unprepare', '\tatomic_inc(&a52_p310_unprepare_in);')
    text = inject_entry(text, 'dsi_link_hs_clk_start',
        '\tWRITE_ONCE(a52_p310_last_hs, link_hs_clks);\n\tatomic_inc(&a52_p310_hs_start);')
    text = inject_entry(text, 'dsi_link_hs_clk_stop', '\tatomic_inc(&a52_p310_hs_stop);')
    text = inject_entry(text, 'dsi_link_lp_clk_start',
        '\tWRITE_ONCE(a52_p310_last_lp, link_lp_clks);\n\tatomic_inc(&a52_p310_lp_start);')
    text = inject_entry(text, 'dsi_link_lp_clk_stop', '\tatomic_inc(&a52_p310_lp_stop);')

    text = inject_entry(text, 'dsi_clk_update_link_clk_state',
        '\tatomic_inc(&a52_p310_update_in);\n'
        '\tatomic_set(&a52_p310_last_type, (int)l_type);\n'
        '\tatomic_set(&a52_p310_last_state, (int)l_state);\n'
        '\tatomic_set(&a52_p310_last_enable, enable ? 1 : 0);')
    text = rewrite_returns_rc(text, 'dsi_clk_update_link_clk_state',
        '\tatomic_set(&a52_p310_update_rc, rc);\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_update_ok);')
    return text


def patch_pll(text: str) -> str:
    if MARK in text:
        return text
    if 'vco_10nm_prepare' not in text or 'dsi_pll_10nm_lock_status' not in text:
        raise SystemExit('Phase310 10nm PLL source shape not recognized')

    inc = '#include "pll_drv.h"\n'
    if inc not in text:
        raise SystemExit('Phase310 PLL include anchor missing')
    text = text.replace(
        inc,
        inc + '#include <linux/atomic.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        1,
    )

    anchor = 'static inline int pll_reg_read(void *context, unsigned int reg,\n'
    helper = r'''/* A52_PHASE310_GKI_LINK_CLOCK_LIFECYCLE_V2
 * Sticky history for the existing 10nm DSI PLL/VCO handoff lifecycle.
 * Recorder calls and atomics only: no PLL write, delay, timeout, or return
 * behavior is changed.
 */
#define A52_P310_PLL_MAX 2
struct a52_p310_pll_hist {
	atomic_t set_rate;
	atomic_t set_skip_on;
	atomic_t set_ok;
	atomic_t prepare;
	atomic_t prepare_skip_handoff;
	atomic_t prepare_ok;
	atomic_t enable;
	atomic_t enable_ok;
	atomic_t enable_rc;
	atomic_t lock_poll;
	atomic_t lock_ok;
	atomic_t lock_rc;
	atomic_t recalc;
	atomic_t handoff_set;
	atomic_t unprepare;
};
static struct a52_p310_pll_hist a52_p310_pll_hist[A52_P310_PLL_MAX];
static struct mdss_pll_resources *a52_p310_pll_last[A52_P310_PLL_MAX];

static struct a52_p310_pll_hist *a52_p310_pll_h(struct mdss_pll_resources *rsc)
{
	if (!rsc || rsc->index >= A52_P310_PLL_MAX)
		return NULL;
	WRITE_ONCE(a52_p310_pll_last[rsc->index], rsc);
	return &a52_p310_pll_hist[rsc->index];
}

static void a52_p310_pll_evt(struct mdss_pll_resources *rsc,
		u32 event, int rc)
{
	if (!rsc || rsc->index >= A52_P310_PLL_MAX)
		return;
	WRITE_ONCE(a52_p310_pll_last[rsc->index], rsc);
	a52_ackfr_record("P276 310PE i=%u e=%u rc=%d", rsc->index, event, rc);
}

void a52_p310_pll_lifecycle_snapshot(unsigned int index, unsigned int point)
{
	struct a52_p310_pll_hist *h;
	struct mdss_pll_resources *rsc;

	if (index >= A52_P310_PLL_MAX)
		return;
	h = &a52_p310_pll_hist[index];
	rsc = READ_ONCE(a52_p310_pll_last[index]);
	a52_ackfr_record("P276 310P q=%u i=%u sr=%d ss=%d so=%d pr=%d ps=%d po=%d en=%d eo=%d",
		point, index, atomic_read(&h->set_rate), atomic_read(&h->set_skip_on),
		atomic_read(&h->set_ok), atomic_read(&h->prepare),
		atomic_read(&h->prepare_skip_handoff), atomic_read(&h->prepare_ok),
		atomic_read(&h->enable), atomic_read(&h->enable_ok));
	a52_ackfr_record("P276 310L q=%u lp=%d lo=%d lr=%d er=%d re=%d hs=%d up=%d on=%d ho=%d",
		point, atomic_read(&h->lock_poll), atomic_read(&h->lock_ok),
		atomic_read(&h->lock_rc), atomic_read(&h->enable_rc),
		atomic_read(&h->recalc), atomic_read(&h->handoff_set),
		atomic_read(&h->unprepare), rsc ? rsc->pll_on : -1,
		rsc ? rsc->handoff_resources : -1);
}

'''
    text = replace_once(text, anchor, helper + anchor, 'PLL lifecycle helper')

    # vco set-rate lifecycle
    old = '''\tif (!rsc) {\n\t\tpr_err("pll resource not found\\n");\n\t\treturn -EINVAL;\n\t}\n\n\tif (rsc->pll_on)\n\t\treturn 0;\n'''
    new = '''\tif (!rsc) {\n\t\tpr_err("pll resource not found\\n");\n\t\treturn -EINVAL;\n\t}\n\n\tif (a52_p310_pll_h(rsc))\n\t\tatomic_inc(&a52_p310_pll_h(rsc)->set_rate);\n\ta52_p310_pll_evt(rsc, 1, 0);\n\n\tif (rsc->pll_on) {\n\t\tatomic_inc(&a52_p310_pll_h(rsc)->set_skip_on);\n\t\ta52_p310_pll_evt(rsc, 2, 0);\n\t\treturn 0;\n\t}\n'''
    text = replace_in_function(text, 'vco_10nm_set_rate', old, new, 'set-rate entry')
    text = inject_before_last(text, 'vco_10nm_set_rate', '\n\treturn 0;\n',
        '\n\tatomic_inc(&a52_p310_pll_h(rsc)->set_ok);\n\ta52_p310_pll_evt(rsc, 3, 0);\n')

    # lock polling is the actual PLL lock criterion used by this provider.
    old = '''\trc = readl_poll_timeout_atomic(pll->pll_base + PLL_COMMON_STATUS_ONE,\n\t\t\t\t       status,\n\t\t\t\t       ((status & BIT(0)) > 0),\n\t\t\t\t       delay_us,\n\t\t\t\t       timeout_us);\n\tif (rc)\n'''
    new = '''\trc = readl_poll_timeout_atomic(pll->pll_base + PLL_COMMON_STATUS_ONE,\n\t\t\t\t       status,\n\t\t\t\t       ((status & BIT(0)) > 0),\n\t\t\t\t       delay_us,\n\t\t\t\t       timeout_us);\n\tif (a52_p310_pll_h(pll)) {\n\t\tatomic_inc(&a52_p310_pll_h(pll)->lock_poll);\n\t\tatomic_set(&a52_p310_pll_h(pll)->lock_rc, rc);\n\t\tif (!rc)\n\t\t\tatomic_inc(&a52_p310_pll_h(pll)->lock_ok);\n\t}\n\ta52_p310_pll_evt(pll, 4, rc);\n\tif (rc)\n'''
    text = replace_in_function(text, 'dsi_pll_10nm_lock_status', old, new, 'lock result')

    # real PLL enable function
    text = inject_entry(text, 'dsi_pll_enable',
        '\tstruct a52_p310_pll_hist *a52h;\n')
    old = '''\tstruct mdss_pll_resources *rsc = vco->priv;\n\n\tdsi_pll_enable_pll_bias(rsc);\n'''
    new = '''\tstruct mdss_pll_resources *rsc = vco->priv;\n\n\ta52h = a52_p310_pll_h(rsc);\n\tif (a52h)\n\t\tatomic_inc(&a52h->enable);\n\ta52_p310_pll_evt(rsc, 5, 0);\n\n\tdsi_pll_enable_pll_bias(rsc);\n'''
    text = replace_in_function(text, 'dsi_pll_enable', old, new, 'enable entry')
    old = '''\tif (rsc->slave)\n\t\tMDSS_PLL_REG_W(rsc->slave->phy_base, PHY_CMN_RBUF_CTRL, 0x01);\n\nerror:\n\treturn rc;\n'''
    new = '''\tif (rsc->slave)\n\t\tMDSS_PLL_REG_W(rsc->slave->phy_base, PHY_CMN_RBUF_CTRL, 0x01);\n\n\tif (a52h)\n\t\tatomic_inc(&a52h->enable_ok);\n\ta52_p310_pll_evt(rsc, 6, 0);\n\nerror:\n\tif (a52h)\n\t\tatomic_set(&a52h->enable_rc, rc);\n\treturn rc;\n'''
    text = replace_in_function(text, 'dsi_pll_enable', old, new, 'enable result')

    # prepare/handoff lifecycle
    old = '''\tif (!pll) {\n\t\tpr_err("dsi pll resources are not available\\n");\n\t\treturn -EINVAL;\n\t}\n\n\t/* Skip vco recalculation for continuous splash use case */\n\tif (pll->handoff_resources)\n\t\treturn 0;\n'''
    new = '''\tif (!pll) {\n\t\tpr_err("dsi pll resources are not available\\n");\n\t\treturn -EINVAL;\n\t}\n\n\tatomic_inc(&a52_p310_pll_h(pll)->prepare);\n\ta52_p310_pll_evt(pll, 7, 0);\n\n\t/* Skip vco recalculation for continuous splash use case */\n\tif (pll->handoff_resources) {\n\t\tatomic_inc(&a52_p310_pll_h(pll)->prepare_skip_handoff);\n\t\ta52_p310_pll_evt(pll, 8, 0);\n\t\treturn 0;\n\t}\n'''
    text = replace_in_function(text, 'vco_10nm_prepare', old, new, 'prepare entry/handoff skip')
    text = inject_before_last(text, 'vco_10nm_prepare', '\n\treturn rc;\n',
        '\n\tif (!rc)\n\t\tatomic_inc(&a52_p310_pll_h(pll)->prepare_ok);\n\ta52_p310_pll_evt(pll, 9, rc);\n')

    # recalc is where a bootloader-locked PLL establishes handoff_resources.
    old = '''\tif (!pll) {\n\t\tpr_err("pll is null\\n");\n\t\treturn 0;\n\t}\n\n\t/*\n'''
    new = '''\tif (!pll) {\n\t\tpr_err("pll is null\\n");\n\t\treturn 0;\n\t}\n\n\tatomic_inc(&a52_p310_pll_h(pll)->recalc);\n\ta52_p310_pll_evt(pll, 10, 0);\n\n\t/*\n'''
    text = replace_in_function(text, 'vco_10nm_recalc_rate', old, new, 'recalc entry')
    old = '''\tif (!dsi_pll_10nm_lock_status(pll))\n\t\tpll->handoff_resources = true;\n'''
    new = '''\tif (!dsi_pll_10nm_lock_status(pll)) {\n\t\tpll->handoff_resources = true;\n\t\tatomic_inc(&a52_p310_pll_h(pll)->handoff_set);\n\t\ta52_p310_pll_evt(pll, 11, 0);\n\t}\n'''
    text = replace_in_function(text, 'vco_10nm_recalc_rate', old, new, 'handoff establishment')

    old = '''\tif (!pll) {\n\t\tpr_err("dsi pll resources not available\\n");\n\t\treturn;\n\t}\n\n\t/*\n'''
    new = '''\tif (!pll) {\n\t\tpr_err("dsi pll resources not available\\n");\n\t\treturn;\n\t}\n\n\tatomic_inc(&a52_p310_pll_h(pll)->unprepare);\n\ta52_p310_pll_evt(pll, 12, 0);\n\n\t/*\n'''
    text = replace_in_function(text, 'vco_10nm_unprepare', old, new, 'unprepare entry')
    return text


def patch_phy(text: str) -> str:
    if MARK in text:
        return text
    if P309 not in text:
        raise SystemExit('Phase310 requires inherited Phase309 observer')

    old = 'extern void a52_p308_pll_snapshot(unsigned int index, unsigned int point);\n\n'
    new = (old +
           'extern void a52_p310_clk_snapshot(unsigned int index, unsigned int point);\n'
           'extern void a52_p310_pll_lifecycle_snapshot(unsigned int index, unsigned int point);\n\n')
    text = replace_once(text, old, new, 'Phase310 snapshot declarations')

    old_call = '''\ta52_p309_clamp_snapshot(index, point);\n\ta52_p308_pll_snapshot(index, point);\n}\n'''
    new_call = '''\ta52_p309_clamp_snapshot(index, point);\n\ta52_p310_clk_snapshot(index, point);\n\ta52_p310_pll_lifecycle_snapshot(index, point);\n\ta52_p308_pll_snapshot(index, point);\n}\n'''
    return replace_once(text, old_call, new_call, 'exact-F0 consolidated lifecycle snapshot')


def validate(clk: str, phy: str, pll: str) -> None:
    alltxt = clk + phy + pll
    required = [
        MARK,
        'P276 310C q=%u i=%u sr=%d run=%d sk=%d ok=%d rc=%d si=%d',
        'P276 310H q=%u pr=%d po=%d pc=%d en=%d eo=%d ec=%d hs=%d hp=%d',
        'P276 310U q=%u di=%d up=%d ls=%d lp=%d ui=%d uo=%d ur=%d t=%d s=%d e=%d',
        'P276 310R q=%u br=%lu pr=%lu ir=%lu er=%lu',
        'P276 310E q=%u em=%x pm=%x bp=%d pp=%d',
        'P276 310PE i=%u e=%u rc=%d',
        'P276 310P q=%u i=%u sr=%d ss=%d so=%d pr=%d ps=%d po=%d en=%d eo=%d',
        'P276 310L q=%u lp=%d lo=%d lr=%d er=%d re=%d hs=%d up=%d on=%d ho=%d',
        'atomic_inc(&a52_p310_sr_skip);',
        'atomic_inc(&a52_p310_sr_run);',
        'atomic_inc(&a52_p310_prepare_in);',
        'atomic_inc(&a52_p310_enable_in);',
        'atomic_inc(&a52_p310_hs_start);',
        'atomic_inc(&a52_p310_update_in);',
        'dsi0_phy_pll_out_byteclk',
        'dsi0_phy_pll_out_dsiclk',
        'a52_p310_pll_lifecycle_snapshot(index, point);',
        'a52_p310_clk_snapshot(index, point);',
        P309,
    ]
    for token in required:
        if token not in alltxt:
            raise SystemExit('Phase310 required token missing: ' + token)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()

    clk_path = args.root / CLK_REL
    phy_path = args.root / PHY_REL
    pll_path = args.root / PLL_REL
    for p in (clk_path, phy_path, pll_path):
        if not p.is_file():
            raise SystemExit('Phase310 source missing: ' + str(p))

    if not args.check_only:
        clk_path.write_text(patch_clk(clk_path.read_text()))
        pll_path.write_text(patch_pll(pll_path.read_text()))
        phy_path.write_text(patch_phy(phy_path.read_text()))

    validate(clk_path.read_text(), phy_path.read_text(), pll_path.read_text())
    print('Phase310 GKI consolidated DSI link-clock + 10nm PLL lifecycle observer: PASS')


if __name__ == '__main__':
    main()
