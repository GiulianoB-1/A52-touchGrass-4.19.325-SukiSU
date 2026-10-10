#!/usr/bin/env python3
"""Paired AR-IDLE observer for P6 F0-BISECT, read-only except recorder writes.

The actual disable function is sde_encoder_phys_cmd_prepare_commit, NOT
dsi_display_prepare(P1). Record SW enable, HW enable, pending kickoff,
TE pointer status, and encoder-error bit before/after the existing disable.
Each sample is stored 16 times, with CRC. Eight phase-2 live-debug-bus
samples are separated by approximately 1ms in BOTH kernels (adds ~7ms to
commit preparation). INTF timing-engine status/frame count and CTL scheduler
state are read-only. A missing hook is UNKNOWN, never PASS.
"""
import argparse
from pathlib import Path

MARK="A52_F0B_AR_IDLE_16X_V2"
REL=Path("sde/sde_encoder_phys_cmd.c")

def one(s,a,b,label):
    n=s.count(a)
    if n!=1:
        raise RuntimeError("AR-IDLE %s: anchor count=%d (must be 1)"%(label,n))
    return s.replace(a,b,1)

HELPER=r'''
/* A52_F0B_AR_IDLE_16X_V2
 * Does not change the autorefresh/TE state. Snapshot only, 8 events max.
 * Step 1 = software says not enabled, so disable path skipped.
 * Step 2 = software says enabled, before existing disable.
 * Step 3 = after normal autorefresh-disable path, not proof of success.
 */
#include <linux/ktime.h>
#include <linux/atomic.h>
#include <linux/string.h>
#include <linux/delay.h>

extern int __F0BAR_STORE__(u32, u32, const char *, u32, u32,
                           const void *, u32);
struct a52_f0bar_observation {
    u32 magic, version, index, step;
    u32 sw_autorefresh, hw_status, hw_valid, pending_kickoff;
    u32 enable_state, read_ptr, write_ptr, ptr_valid;
    u32 hw_status_inv, pending_inv, step_inv, tag;
    u32 intf_valid, intf_engine_enabled, intf_frame_count, intf_line_count;
    u32 ctl_valid, ctl_scheduler_status;
    u64 time_ns, time_ns_inv;
};
static atomic_t a52_f0bar_count = ATOMIC_INIT(0);
static void a52_f0bar_observe(struct sde_encoder_phys *phys, u32 step)
{
    struct a52_f0bar_observation r[16], sample = {0};
    struct sde_hw_pp_vsync_info info = {0};
    struct intf_status intf_state = {0};
    struct sde_hw_intf *intf;
    struct sde_hw_pingpong *pp;
    int index, i, rc = -ENODEV;

    if (!phys) return;
    index = atomic_inc_return(&a52_f0bar_count);
    if (index > 24) return;
    sample.magic = 0x52414246U; /* FBAR */
    sample.version = 2U;
    sample.index = (u32)index;
    sample.step = step;
    sample.sw_autorefresh = sde_encoder_phys_cmd_is_autorefresh_enabled(phys);
    sample.hw_status = ~0U;
    if (phys->hw_mdptop && phys->hw_mdptop->ops.get_autorefresh_status) {
        sample.hw_status = phys->hw_mdptop->ops.get_autorefresh_status(
            phys->hw_mdptop, phys->intf_idx);
        sample.hw_valid = 1U;
    }
    sample.pending_kickoff = (u32)atomic_read(&phys->pending_kickoff_cnt);
    sample.enable_state = (u32)phys->enable_state;
    intf = phys->hw_intf;
    pp = phys->hw_pp;
    if (intf && intf->ops.get_status) {
        intf->ops.get_status(intf, &intf_state);
        sample.intf_valid = 1U;
        sample.intf_engine_enabled = !!intf_state.is_en;
        sample.intf_frame_count = intf_state.frame_count;
        sample.intf_line_count = intf_state.line_count;
    }
    if (phys->hw_ctl && phys->hw_ctl->ops.get_scheduler_status) {
        sample.ctl_valid = 1U;
        sample.ctl_scheduler_status =
            phys->hw_ctl->ops.get_scheduler_status(phys->hw_ctl);
    }
    if (phys->has_intf_te && intf && intf->ops.get_vsync_info)
        rc = intf->ops.get_vsync_info(intf, &info);
    else if (pp && pp->ops.get_vsync_info)
        rc = pp->ops.get_vsync_info(pp, &info);
    if (!rc) {
        sample.ptr_valid = 1U;
        sample.read_ptr = (u32)info.rd_ptr_line_count;
        sample.write_ptr = (u32)info.wr_ptr_line_count;
    }
    sample.hw_status_inv = ~sample.hw_status;
    sample.pending_inv = ~sample.pending_kickoff;
    sample.step_inv = ~sample.step;
    sample.tag = 0xa52f0b02U;
    sample.time_ns = ktime_get_ns();
    sample.time_ns_inv = ~sample.time_ns;
    for (i=0; i<16; i++) r[i] = sample;
    (void)__F0BAR_STORE__(0x46304200U | step, 0x4630U, "F0BAR",
                          sample.index, step, r, sizeof(r));
    pr_warn("F0BAR #%u phase=%u t=%llu sw=%u dbg_valid=%u dbg=%08x dbg_busy7=%u pend=%u enc=%u ptr_valid=%u read=%u write=%u intf_valid=%u intf_en=%u intf_frame=%u intf_line=%u sched_valid=%u sched=%08x\n",
        sample.index, step, (unsigned long long)sample.time_ns,
        sample.sw_autorefresh, sample.hw_valid, sample.hw_status,
        sample.hw_valid ? !!(sample.hw_status & BIT(7)) : 0U,
        sample.pending_kickoff, sample.enable_state,
        sample.ptr_valid, sample.read_ptr, sample.write_ptr,
        sample.intf_valid, sample.intf_engine_enabled,
        sample.intf_frame_count, sample.intf_line_count,
        sample.ctl_valid, sample.ctl_scheduler_status);
}
'''

def modify(s,store):
    if MARK in s: return s
    target="static void sde_encoder_phys_cmd_prepare_commit(\n"
    helper=HELPER.replace("__F0BAR_STORE__",store)
    s=one(s,target,helper+target,"function")
    early="""\tif (!sde_encoder_phys_cmd_is_autorefresh_enabled(phys_enc))
\t\treturn;

\tsde_encoder_phys_cmd_connect_te(phys_enc, false);
"""
    repl="""\tif (!sde_encoder_phys_cmd_is_autorefresh_enabled(phys_enc)) {
\t\ta52_f0bar_observe(phys_enc, 1); /* no SW disable path */
\t\treturn;
\t}
\t/* Eight phase-2 samples capture debug-bus bit7 across a frame.
\t * Read-only (apart from RAM recorder writes), paired GKI/TG;
\t * this intentionally adds ~7ms before the unchanged disable path.
\t */
\t{
\t\tint f0bar_i;
\t\tfor (f0bar_i = 0; f0bar_i < 8; f0bar_i++) {
\t\t\ta52_f0bar_observe(phys_enc, 2);
\t\t\tif (f0bar_i != 7)
\t\t\t\tusleep_range(950, 1050);
\t\t}
\t}

\tsde_encoder_phys_cmd_connect_te(phys_enc, false);
"""
    s=one(s,early,repl,"before")
    tail="""\t_sde_encoder_autorefresh_disable_seq2(phys_enc);
\tsde_encoder_phys_cmd_connect_te(phys_enc, true);

\tSDE_DEBUG_CMDENC(cmd_enc, "autorefresh disabled successfully\\n");
"""
    end="""\t_sde_encoder_autorefresh_disable_seq2(phys_enc);
\tsde_encoder_phys_cmd_connect_te(phys_enc, true);
\ta52_f0bar_observe(phys_enc, 3); /* AFTER disable: check HW, do not assume idle */

\tSDE_DEBUG_CMDENC(cmd_enc, "autorefresh disabled successfully\\n");
"""
    s=one(s,tail,end,"after")
    return s

def validate(root,store):
    s=(root/REL).read_text()
    for needle in (MARK,store+"(u32,",'"F0BAR"', "a52_f0bar_observe(phys_enc, 1)",
            "a52_f0bar_observe(phys_enc, 2)",
            "a52_f0bar_observe(phys_enc, 3)",
            "get_autorefresh_status", "pending_kickoff_cnt", "get_scheduler_status", "get_status", "usleep_range(950, 1050)", "intf_engine_enabled",
            "ptr_valid", "hw_status & BIT(7)", "for (i=0; i<16; i++)"):
        if needle not in s:raise RuntimeError("AR-IDLE audit missing: "+needle)
    print("AR-IDLE v2 source audit PASS: GKI/TG 8x read-only status per 1ms + INTF/CTL")
    print("AR-IDLE cannot guarantee MDP fetching stopped; P1 is DSI prepare entry.")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--mode",choices=["gki","tg"],required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    rel=Path("drivers/a52_display/msm")/REL if a.mode=="gki" else Path("techpack/display/msm")/REL
    store="a52_p445_store_section" if a.mode=="gki" else "a52_p444_store_section"
    root=a.root
    if not a.check_only:
        path=root/rel
        path.write_text(modify(path.read_text(),store))
    validate(root/("drivers/a52_display/msm" if a.mode=="gki" else "techpack/display/msm"),store)
if __name__=="__main__":
    main()
