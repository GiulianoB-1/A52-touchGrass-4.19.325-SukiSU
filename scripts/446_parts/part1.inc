	memcpy_toio(p446_ram,&h,sizeof(h)); p446_flush(p446_ram,sizeof(h));
}

static void p446_emit(u32 event,u32 flags)
{
	struct p446_rec r; unsigned long irqf; u32 seq,cap,slot,count,wraps; bool safe;
	struct p446_smmu_cache sc;
	if(!p446_ram) return;
	memset(&r,0,sizeof(r)); r.ns=ktime_get_boottime_ns(); r.event=event; r.flags=flags;
	safe=p446_safe(&r.gcc_ahb,&r.disp_ahb,&r.disp_mdp,&r.gdsc);
	r.refgen=rr(p446_refgen,0x80);
	if(safe){
		r.flags|=P446_F_SAFE;
		r.frame=rr(p446_mdp,P446_INTF1+0x0ac); r.tear=rr(p446_mdp,P446_INTF1+0x298);
		r.intf_status=rr(p446_mdp,P446_INTF1+0x26c); r.timing_en=rr(p446_mdp,P446_INTF1+0x000);
		r.line_count=rr(p446_mdp,P446_INTF1+0x0b0); r.intf_auto=rr(p446_mdp,P446_INTF1+0x2b4);
		r.mdp_intr=rr(p446_mdp,P446_TOP+0x014); r.mdp_intr2=rr(p446_mdp,P446_TOP+0x00c);
		r.pp_auto_cfg=rr(p446_mdp,P446_PP0+0x030); r.pp_auto_status=rr(p446_mdp,P446_PP0+0x034); r.pp_line=rr(p446_mdp,P446_PP0+0x0b0);
		r.sspp_size=rr(p446_mdp,P446_SSPP10+0x00); r.sspp_xy=rr(p446_mdp,P446_SSPP10+0x08);
		r.sspp_addr0=rr(p446_mdp,P446_SSPP10+0x14); r.sspp_format=rr(p446_mdp,P446_SSPP10+0x30);
		r.sspp_unpack=rr(p446_mdp,P446_SSPP10+0x34); r.sspp_opmode=rr(p446_mdp,P446_SSPP10+0x38);
		r.ctl_layer=rr(p446_mdp,P446_CTL0+0x000); r.ctl_ext=rr(p446_mdp,P446_CTL0+0x040);
		r.ctl_ext2=rr(p446_mdp,P446_CTL0+0x070); r.ctl_ext3=rr(p446_mdp,P446_CTL0+0x0a0); r.ctl_flush=rr(p446_mdp,P446_CTL0+0x018);
		r.lm_opmode=rr(p446_mdp,P446_LM0+0x000); r.lm_blend0=rr(p446_mdp,P446_LM0+0x020);
		if(!(rr(p446_dispcc,0x104c)&P446_CLK_OFF)){
			r.flags|=P446_F_DSI_SAFE; r.dsi_status=rr(p446_dsi,0x0008); r.dsi_lane=rr(p446_dsi,0x00a8); r.dsi_clk=rr(p446_dsi,0x0120);
		}
	}
	spin_lock_irqsave(&p446_lock,irqf); sc=p446_smmu; spin_unlock_irqrestore(&p446_lock,irqf);
	if(sc.valid){ r.flags|=P446_F_SMMU_VALID; r.smr3=sc.smr3; r.s2cr3=sc.s2cr3; r.cb=sc.cb; r.sctlr=sc.sctlr; r.tcr=sc.tcr; r.fsr=sc.fsr; r.ttbr0=sc.ttbr0; r.pa_ff400000=sc.pa_ff400000; r.pa_ff413000=sc.pa_ff413000; r.chosen_cb=sc.chosen_cb; }
	r.cb2_reuse_param=p446_cb2_reuse_param?1:0; if(r.cb2_reuse_param) r.flags|=P446_F_CB2_REUSE;
	seq=(u32)atomic_inc_return(&p446_seq); r.seq=seq; cap=(P446_RAM_BYTES-P446_HDR_BYTES)/sizeof(r); slot=(seq-1)%cap; count=min(seq,cap); wraps=(seq-1)/cap;
	r.commit=P446_REC_COMMIT; r.crc32=p446_crc(&r,offsetof(struct p446_rec,crc32));
	memcpy_toio((u8 __iomem*)p446_ram+P446_HDR_BYTES+slot*sizeof(r),&r,sizeof(r)); p446_flush((u8 __iomem*)p446_ram+P446_HDR_BYTES+slot*sizeof(r),sizeof(r));
	p446_header_sync(count,wraps,r.ns);
	/* Second transport: existing Phase414 debug-partition recorder. */
	a52_ackfr_record("P446 H q=%u e=%u f=%x fr=%x i2=%x ss=%x a=%x fm=%x op=%x",r.seq,r.event,r.flags,r.frame,r.mdp_intr2,r.sspp_size,r.sspp_addr0,r.sspp_format,r.sspp_opmode);
	a52_ackfr_record("P446 C q=%u ly=%x x=%x x2=%x x3=%x fl=%x lm=%x bl=%x pp=%x",r.seq,r.ctl_layer,r.ctl_ext,r.ctl_ext2,r.ctl_ext3,r.ctl_flush,r.lm_opmode,r.lm_blend0,r.pp_auto_cfg);
	a52_ackfr_record("P446 S q=%u sm=%x s2=%x cb=%u sc=%x tc=%x fs=%x tt=%llx",r.seq,r.smr3,r.s2cr3,r.cb,r.sctlr,r.tcr,r.fsr,(unsigned long long)r.ttbr0);
}

void a52_p446_mark(u32 event){ p446_emit(event,P446_F_MARK); }
EXPORT_SYMBOL_GPL(a52_p446_mark);

void a52_p446_smmu_update(u32 event,u32 smr3,u32 s2cr3,u32 cb,u32 sctlr,u32 tcr,u64 ttbr0,u32 fsr,u64 pa0,u64 pa1,u32 chosen)
{
	unsigned long f; spin_lock_irqsave(&p446_lock,f); p446_smmu.smr3=smr3; p446_smmu.s2cr3=s2cr3; p446_smmu.cb=cb; p446_smmu.sctlr=sctlr; p446_smmu.tcr=tcr; p446_smmu.ttbr0=ttbr0; p446_smmu.fsr=fsr; p446_smmu.pa_ff400000=pa0; p446_smmu.pa_ff413000=pa1; p446_smmu.chosen_cb=chosen; p446_smmu.valid=1; spin_unlock_irqrestore(&p446_lock,f); p446_emit(event,P446_F_MARK);
}
EXPORT_SYMBOL_GPL(a52_p446_smmu_update);

void a52_p446_set_cb2_param(bool en){ p446_cb2_reuse_param=en; }
EXPORT_SYMBOL_GPL(a52_p446_set_cb2_param);

static void p446_worker(struct work_struct *w)
{
	u64 ns=ktime_get_boottime_ns(); unsigned long d;
	p446_emit(P446_EVT_PERIODIC,P446_F_PERIODIC);
	if(ns < 10ULL*NSEC_PER_SEC) d=msecs_to_jiffies(10);
	else { if(!atomic_xchg(&p446_fast_done,1)) p446_emit(P446_EVT_STOP10S,P446_F_MARK); d=msecs_to_jiffies(1000); }
