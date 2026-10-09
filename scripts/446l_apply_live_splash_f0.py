#!/usr/bin/env python3
"""Phase446l GKI: one-shot, live-splash first-F0 direct DMA discriminator.

Requires Phase446j applied first. No Samsung layer; no PHY/PLL reset; no
autorefresh disable. If the MDP isn't alive or cannot reach an idle window,
record a PRECONDITION result rather than claiming the hardware failed.
"""
import argparse
from pathlib import Path

MARK = "A52_PHASE446L_LIVE_SPLASH_F0_V1"
BEGIN = "int dsi_display_cont_splash_config(void *dsi_display)"
END = "clks_disabled:"


def one(s, old, new, label):
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: required exactly one occurrence, found {n}")
    return s.replace(old, new, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    target = a.root / "drivers/a52_display/msm/dsi/dsi_display.c"
    s = target.read_text()
    if not a.check_only and MARK not in s:
        s = one(s, "#include <linux/workqueue.h>",
                "#include <linux/workqueue.h>\n#include <linux/io.h>", "io include")

        helper = r'''/* A52_PHASE446L_LIVE_SPLASH_F0_V1
 * 7 s after cont-splash activation: verify live MDP frame count then try
 * exactly one F0 using the normal controller DMA path. We do NOT alter
 * autorefresh, host state, PHY, PLL, SMMU or normal handoff sequencing.
 */
extern int a52_p445_store_section(u32, u32, const char *, u32, u32,
                                  const void *, u32);
extern void a52_p446_mark(u32, u32, u32);
extern int a52_p446l_probe_begin(void);
extern void a52_p446l_probe_end(void);
extern void a52_p446l_probe_state(u32 *, u32 *, u32 *);
extern void a52_p446j_misc_event(struct dsi_ctrl_hw *, u32, u32, u32);
struct p446l_snap {
    u32 frame, line, out_line, int_count, autorefresh, intf_status, ctl_status;
    u32 dsi_status, dsi_lane, dsi_clk;
} __packed;
struct p446l_result {
    u32 magic, version, stage, splash, frame0, frame1, frame2;
    u32 pwr, host, ctrl, cmd, cmd_ref, iova, dma_pre, dma_post;
    u32 gate_pre, armed_pre, seen_pre, gate_post, armed_post, seen_post;
    u32 cmd_ref_post, cmd_state_post, frame_after_restore;
    s32 clk_on, alloc, engine_on, idle, probe_begin, transfer, engine_off, clk_off;
    struct p446l_snap pre, post, next;
    u64 start_ns, end_ns;
} __packed;
static struct dsi_display *p446l_display;
static void p446l_worker(struct work_struct *work);
static DECLARE_DELAYED_WORK(p446l_work, p446l_worker);
static atomic_t p446l_once = ATOMIC_INIT(0);
static void p446l_snap_take(struct p446l_snap *p, struct dsi_ctrl *c,
                            void __iomem *intf, void __iomem *pp,
                            void __iomem *ctl)
{
    p->frame = readl_relaxed(intf + 0x8ac);
    p->intf_status = readl_relaxed(intf + 0x800);
    p->line = readl_relaxed(pp + 0x02c);
    p->out_line = readl_relaxed(pp + 0x028);
    p->int_count = readl_relaxed(pp + 0x014);
    p->autorefresh = readl_relaxed(pp + 0x030);
    p->ctl_status = readl_relaxed(ctl + 0x064);
    p->dsi_status = readl_relaxed(c->hw.base + DSI_STATUS);
    p->dsi_lane = readl_relaxed(c->hw.base + DSI_LANE_STATUS);
    p->dsi_clk = readl_relaxed(c->hw.base + DSI_CLK_STATUS);
}

static void p446l_worker(struct work_struct *work)
{
    struct dsi_display *d = READ_ONCE(p446l_display);
    struct dsi_ctrl *c = NULL;
    struct mipi_dsi_msg msg = {0};
    struct p446l_result v = {0};
    void __iomem *frame = NULL, *pp = NULL, *ctl = NULL;
    const u8 f0[3] = { 0xF0, 0x5A, 0x5A };
    u32 flags = DSI_CTRL_CMD_FETCH_MEMORY;
    int rc = -EAGAIN;
    bool voted = false, eng = false, probe = false;

    (void)work;
    if (!d || atomic_cmpxchg(&p446l_once, 0, 1)) return;
    v.magic = 0x446c0001U; v.version = 1;
    v.clk_on = v.alloc = v.engine_on = v.idle = v.probe_begin =
        v.transfer = v.engine_off = v.clk_off = -EAGAIN;
    v.start_ns = ktime_get_boottime_ns();
    v.stage = 1;
    mutex_lock(&d->display_lock);
    if (!d->is_cont_splash_enabled || !d->panel || !d->ctrl_count)
        goto done;
    c = d->ctrl[d->cmd_master_idx].ctrl;
    if (!c || !c->hw.base || c->cell_index != 0)
        goto done;
    v.splash = 1;
    v.pwr = c->current_state.power_state;
    v.host = c->current_state.host_initialized;
    v.ctrl = c->current_state.controller_state;
    v.cmd = c->current_state.cmd_engine_state;
    v.cmd_ref = d->cmd_engine_refcount;
    v.iova = c->cmd_buffer_iova;
    a52_p446_mark(0x1e0U, v.pwr, (v.host << 16) | (v.ctrl << 8) | v.cmd);

    /* A continuously advancing INTF1 counter validates a live MDP splash. */
    frame = ioremap(0x0ae6b000ULL, 0x1000);
    pp = ioremap(0x0ae71000ULL, 0x1000);
    ctl = ioremap(0x0ae02000ULL, 0x1000);
    v.stage = 2;
    if (!frame || !pp || !ctl) goto done;
    v.frame0 = readl_relaxed(frame + 0x8ac);
    msleep(27);
    v.frame1 = readl_relaxed(frame + 0x8ac);
    a52_p446_mark(0x1e1U, v.frame0, v.frame1);
    if (!d->is_cont_splash_enabled || v.frame0 == v.frame1)
        goto done;

    /* Mirror only the standard host-transfer votes/state transitions. */
    v.stage = 3;
    rc = dsi_display_clk_ctrl(d->dsi_clk_handle, DSI_ALL_CLKS, DSI_CLK_ON);
    v.clk_on = rc;
    if (rc) goto done;
    voted = true;

    v.stage = 4;
    if (!d->tx_cmd_buf)
        rc = dsi_host_alloc_cmd_tx_buffer(d);
    else
        rc = 0;
    v.alloc = rc;
    if (rc) goto done;

    v.stage = 5;
    rc = dsi_display_cmd_engine_enable(d);
    v.engine_on = rc;
    if (rc) goto done;
    eng = true;

    /* No autorefresh or MDP register writes. Abort unless PP has gone idle. */
    v.stage = 6;
    rc = dsi_ctrl_wait_for_cmd_mode_mdp_idle(c);
    v.idle = rc;
    if (rc) goto done;
    if (!d->is_cont_splash_enabled) goto done;

    msg.channel = 0;
    msg.type = MIPI_DSI_GENERIC_LONG_WRITE; /* 0x29, F0 5A 5A */
    msg.flags = MIPI_DSI_MSG_LASTCOMMAND;
    msg.tx_len = sizeof(f0);
    msg.tx_buf = f0;

    v.stage = 7;
    p446l_snap_take(&v.pre, c, frame, pp, ctl);
    v.dma_pre = v.pre.dsi_status;
    a52_p446l_probe_state(&v.gate_pre, &v.armed_pre, &v.seen_pre);
    if (v.gate_pre || v.armed_pre || v.seen_pre) {
        v.probe_begin = -EBUSY;
        goto done;
    }
    rc = a52_p446l_probe_begin();
    v.probe_begin = rc;
    if (rc) goto done;
    probe = true;
    a52_p446j_misc_event(&c->hw, 0x4a40U, v.pre.frame, v.pre.dsi_status);
    a52_p446_mark(0x1e2U, v.pre.frame, v.pre.dsi_status);
    v.stage = 8;
    rc = dsi_ctrl_cmd_transfer(c, &msg, &flags);
    v.transfer = rc;
    p446l_snap_take(&v.post, c, frame, pp, ctl);
    v.dma_post = v.post.dsi_status;
    a52_p446j_misc_event(&c->hw, 0x4a41U, v.post.frame, v.post.dsi_status);
    a52_p446l_probe_end();
    probe = false;
    a52_p446l_probe_state(&v.gate_post, &v.armed_post, &v.seen_post);
    v.frame2 = v.post.frame;
    v.stage = 9;
    a52_p446_mark(0x1e3U, (u32)rc, v.post.dsi_status);
    /* Observe the next logo refresh; leave MDP/PP untouched. */
    msleep(27);
    p446l_snap_take(&v.next, c, frame, pp, ctl);

done:
    if (probe) a52_p446l_probe_end();
    if (eng) v.engine_off = dsi_display_cmd_engine_disable(d);
    if (voted)
        v.clk_off = dsi_display_clk_ctrl(d->dsi_clk_handle,
                                        DSI_ALL_CLKS, DSI_CLK_OFF);
    if (c && v.splash) {
        v.cmd_ref_post = d->cmd_engine_refcount;
        v.cmd_state_post = c->current_state.cmd_engine_state;
    }
    if (frame) {
        v.frame_after_restore = readl_relaxed(frame + 0x8ac);
        iounmap(frame);
    }
    if (pp) iounmap(pp);
    if (ctl) iounmap(ctl);
    v.end_ns = ktime_get_boottime_ns();
    a52_p445_store_section(0x446c0001U, 15U, "L_EARLY_F0",
                           v.stage, (u32)v.transfer, &v, sizeof(v));
    a52_ackfr_record("P446L stage=%u live=%u frame=%u>%u idle=%d transfer=%d eng=%d",
                      v.stage, v.splash, v.frame0, v.frame1, v.idle,
                      v.transfer, v.engine_on);
    pr_err("P446L EARLY stage=%u live=%u frames=%u,%u,%u clk=%d alloc=%d cmd=%d idle=%d DMA=%d off=%d/%d\n",
           v.stage, v.splash, v.frame0, v.frame1, v.frame2,
           v.clk_on, v.alloc, v.engine_on, v.idle, v.transfer,
           v.engine_off, v.clk_off);
    mutex_unlock(&d->display_lock);
}

'''
        s = one(s, BEGIN, helper + BEGIN, "worker injection")
        st = s.index(BEGIN)
        en = s.index(END, st)
        section = s[st:en]
        anchor = "\treturn rc;\n\n"
        if section.count(anchor) != 1:
            raise RuntimeError("unexpected cont-splash success return count")
        section = section.replace(anchor,
              '''    /* One-shot BEFORE normal handoff. Natural F0 is not delayed. */
    if (!rc && display->is_cont_splash_enabled &&
        atomic_read(&p446l_once) == 0) {
        WRITE_ONCE(p446l_display, display);
        schedule_delayed_work(&p446l_work, msecs_to_jiffies(7000));
    }
\treturn rc;\n\n''', 1)
        s = s[:st] + section + s[en:]
        target.write_text(s)

        # Guard Phase445 ladder and Phase446i one-shot ONLY for this
        # controller-level diagnostic, leaving the natural F0 untouched.
        central = a.root / "drivers/a52_display/msm/a52_phase445.c"
        b = central.read_text()
        if "A52_PHASE446L_PROBE_GUARD_V1" not in b:
            b = one(b,
                "static atomic_t p446i_seen = ATOMIC_INIT(0);",
                "static atomic_t p446i_seen = ATOMIC_INIT(0);\\n"
                "static atomic_t p446l_probe_active = ATOMIC_INIT(0);",
                "p445 guard atomic")
            b = one(b,
                "void a52_p445_try_arm(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg, u32 flags)\\n"
                "{\\n    const u8 *p;",
                "void a52_p445_try_arm(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg, u32 flags)\\n"
                "{\\n    const u8 *p;\\n    if (atomic_read(&p446l_probe_active)) return;",
                "early F0 arm guard")
            b = one(b,
                "void a52_p446i_sw_entry(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg, u32 flags)\\n"
                "{\\n    struct p446i_sw *m=&p446i_sw;",
                "void a52_p446i_sw_entry(struct dsi_ctrl *ctrl, const struct mipi_dsi_msg *msg, u32 flags)\\n"
                "{\\n    struct p446i_sw *m=&p446i_sw;\\n"
                "    if (atomic_read(&p446l_probe_active)) return;",
                "natural one-shot guard")
            block = '''/* A52_PHASE446L_PROBE_GUARD_V1
 * Probe-only bypass: never arm Phase445 or consume Phase446i natural F0.
 */
int a52_p446l_probe_begin(void)
{
    if (atomic_read(&p445_gate) != P445_GATE_OFF ||
        atomic_read(&p445_state) != 0 ||
        atomic_read(&p446i_seen) != 0)
        return -EBUSY;
    if (atomic_cmpxchg(&p446l_probe_active, 0, 1))
        return -EBUSY;
    return 0;
}
EXPORT_SYMBOL_GPL(a52_p446l_probe_begin);
void a52_p446l_probe_end(void)
{
    atomic_set(&p446l_probe_active, 0);
}
EXPORT_SYMBOL_GPL(a52_p446l_probe_end);
void a52_p446l_probe_state(u32 *gate, u32 *armed, u32 *seen)
{
    if (gate) *gate = (u32)atomic_read(&p445_gate);
    if (armed) *armed = (u32)atomic_read(&p445_state);
    if (seen) *seen = (u32)atomic_read(&p446i_seen);
}
EXPORT_SYMBOL_GPL(a52_p446l_probe_state);

'''
            b = one(b, "bool a52_p445_active(void)\\n{",
                    block + "bool a52_p445_active(void)\\n{",
                    "probe helper insertion")
            central.write_text(b)

    text = target.read_text()
    for check in (MARK, 'L_EARLY_F0', 'p446l_worker', 'msecs_to_jiffies(7000)',
                  'dsi_ctrl_cmd_transfer(c, &msg, &flags)'):
        if check not in text:
            raise RuntimeError('missing '+check)
    if text.count(MARK) != 1:
        raise RuntimeError('duplicate marker')
    central_text = (a.root / "drivers/a52_display/msm/a52_phase445.c").read_text()
    for token in ("A52_PHASE446L_PROBE_GUARD_V1",
                  "if (atomic_read(&p446l_probe_active)) return;",
                  "a52_p446l_probe_begin", "a52_p446l_probe_end"):
        if token not in central_text:
            raise RuntimeError("missing guard: " + token)
    if text.count('schedule_delayed_work(&p446l_work') != 1:
        raise RuntimeError('duplicate schedule')
    if 'msleep(35)' in text:
        raise RuntimeError('Phase446k delay must NOT be present')
    print('Phase446l prehandoff gated direct F0: PASS')


if __name__ == '__main__':
    main()
