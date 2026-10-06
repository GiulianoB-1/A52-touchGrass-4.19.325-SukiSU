#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK='A52_PHASE446_EARLY_SPLASH_HEARTBEAT_V1'

def one(s,old,new,label):
    n=s.count(old)
    if n!=1: raise SystemExit(f'P446 {label}: expected 1 anchor, found {n}')
    return s.replace(old,new,1)

C=r'''// SPDX-License-Identifier: GPL-2.0-only
/* A52_PHASE446_EARLY_SPLASH_HEARTBEAT_V1
 * Early continuous-splash heartbeat.  Read-only except for persistent recorder RAM.
 */
#include <linux/atomic.h>
#include <linux/crc32.h>
#include <linux/delay.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/ktime.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/uaccess.h>
#include <linux/workqueue.h>
#include <asm/cacheflush.h>

#define P446_RAM_PHYS 0xB1A62000ULL
#define P446_RAM_BYTES 0x00098000U
#define P446_HDR_BYTES 4096U
#define P446_REC_COMMIT 0x4460c0deU
#define P446_HDR_COMMIT 0x446b0071U
#define P446_MAGIC 0x3142483634343650ULL /* P6446HB1-ish LE marker */
#define P446_MDP_PHYS 0x0ae00000ULL
#define P446_DISPCC_PHYS 0x0af00000ULL
#define P446_GCC_PHYS 0x00100000ULL
#define P446_DSI_PHYS 0x0ae94000ULL
#define P446_REFGEN_PHYS 0x088e7000ULL
#define P446_TOP 0x001000U
#define P446_CTL0 0x002000U
#define P446_SSPP10 0x029000U
#define P446_LM0 0x045000U
#define P446_INTF1 0x06b800U
#define P446_PP0 0x071000U
#define P446_CLK_OFF BIT(31)
#define P446_F_SAFE BIT(0)
#define P446_F_PERIODIC BIT(1)
#define P446_F_MARK BIT(2)
#define P446_F_SMMU_VALID BIT(3)
#define P446_F_CB2_REUSE BIT(4)
#define P446_F_DSI_SAFE BIT(5)
#define P446_EVT_PERIODIC 0x100U
#define P446_EVT_INIT 0U
#define P446_EVT_STOP10S 0x101U

extern void a52_ackfr_record(const char *fmt, ...);

struct p446_smmu_cache {
	u32 smr3, s2cr3, cb, sctlr, tcr, fsr;
	u64 ttbr0, pa_ff400000, pa_ff413000;
	u32 chosen_cb, valid;
};

struct p446_rec {
	u64 ns;
	u32 seq, event, flags;
	u32 gcc_ahb, disp_ahb, disp_mdp, gdsc, refgen;
	u32 frame, tear, intf_status, timing_en, line_count, intf_auto;
	u32 mdp_intr, mdp_intr2;
	u32 pp_auto_cfg, pp_auto_status, pp_line;
	u32 dsi_status, dsi_lane, dsi_clk;
	u32 sspp_size, sspp_xy, sspp_addr0, sspp_format, sspp_unpack, sspp_opmode;
	u32 ctl_layer, ctl_ext, ctl_ext2, ctl_ext3, ctl_flush;
	u32 lm_opmode, lm_blend0;
	u32 smr3, s2cr3, cb, sctlr, tcr, fsr;
	u64 ttbr0, pa_ff400000, pa_ff413000;
	u32 cb2_reuse_param, chosen_cb;
	u32 crc32, commit;
} __packed;

struct p446_hdr {
	u64 magic;
	u32 version, rec_size, capacity, count, wraps, cb2_param;
	u64 init_ns, last_ns;
	u32 hdr_crc32, commit;
	u8 reserved[P446_HDR_BYTES - 56];
} __packed;

static void __iomem *p446_ram, *p446_mdp, *p446_dispcc, *p446_gcc, *p446_dsi, *p446_refgen;
static struct p446_hdr *p446_hdr_shadow;
static struct p446_smmu_cache p446_smmu;
static DEFINE_SPINLOCK(p446_lock);
static atomic_t p446_seq = ATOMIC_INIT(0);
static atomic_t p446_fast_done = ATOMIC_INIT(0);
static struct delayed_work p446_work;
static bool p446_cb2_reuse_param;

static u32 p446_crc(const void *p, size_t n) { return crc32_le(~0U,p,n)^~0U; }
static inline u32 rr(void __iomem *b,u32 o){ return b?readl_relaxed((u8 __iomem*)b+o):~0U; }
static void p446_flush(void __iomem *p,size_t n){ wmb(); __flush_dcache_area((void __force*)p,n); dsb(sy); }

static bool p446_safe(u32 *gcc_ahb,u32 *disp_ahb,u32 *disp_mdp,u32 *gdsc)
{
	*gcc_ahb=rr(p446_gcc,0x1700c);
	*disp_ahb=rr(p446_dispcc,0x104c);
	*disp_mdp=rr(p446_dispcc,0x1010);
	*gdsc=rr(p446_dispcc,0x1004);
	return !(*gcc_ahb & P446_CLK_OFF) && !(*disp_ahb & P446_CLK_OFF) && !(*disp_mdp & P446_CLK_OFF);
}

static void p446_header_sync(u32 count,u32 wraps,u64 ns)
{
	struct p446_hdr h;
	memset(&h,0,sizeof(h));
	h.magic=P446_MAGIC; h.version=1; h.rec_size=sizeof(struct p446_rec);
	h.capacity=(P446_RAM_BYTES-P446_HDR_BYTES)/sizeof(struct p446_rec);
	h.count=count; h.wraps=wraps; h.cb2_param=p446_cb2_reuse_param?1:0;
	h.init_ns=p446_hdr_shadow?p446_hdr_shadow->init_ns:0; h.last_ns=ns;
	h.commit=P446_HDR_COMMIT; h.hdr_crc32=p446_crc(&h,offsetof(struct p446_hdr,hdr_crc32));
	if(p446_hdr_shadow) memcpy(p446_hdr_shadow,&h,sizeof(h));
