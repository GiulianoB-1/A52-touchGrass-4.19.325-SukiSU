	schedule_delayed_work(&p446_work,d);
}

static int p446_proc_show(struct seq_file *m,void *v){ struct p446_hdr h; if(!p446_ram)return 0; memcpy_fromio(&h,p446_ram,sizeof(h)); seq_printf(m,"P446 magic=%llx rec=%u count=%u wraps=%u cb2=%u last=%llu\n",(unsigned long long)h.magic,h.rec_size,h.count,h.wraps,h.cb2_param,(unsigned long long)h.last_ns); return 0; }
static int p446_proc_open(struct inode *i,struct file *f){ return single_open(f,p446_proc_show,NULL); }
static const struct proc_ops p446_proc_ops={.proc_open=p446_proc_open,.proc_read=seq_read,.proc_lseek=seq_lseek,.proc_release=single_release};
static ssize_t p446_raw_read(struct file *f,char __user *u,size_t n,loff_t *pp){ u8 *b; ssize_t r; if(*pp>=P446_RAM_BYTES)return 0; n=min_t(size_t,n,P446_RAM_BYTES-*pp); b=kmalloc(n,GFP_KERNEL); if(!b)return -ENOMEM; memcpy_fromio(b,(u8 __iomem*)p446_ram+*pp,n); if(copy_to_user(u,b,n))r=-EFAULT; else { *pp+=n; r=n; } kfree(b); return r; }
static const struct proc_ops p446_raw_ops={.proc_read=p446_raw_read,.proc_lseek=default_llseek};

static int __init p446_init(void)
{
	struct p446_hdr h;
	p446_ram=ioremap(P446_RAM_PHYS,P446_RAM_BYTES); p446_mdp=ioremap(P446_MDP_PHYS,0x80000); p446_dispcc=ioremap(P446_DISPCC_PHYS,0x3000); p446_gcc=ioremap(P446_GCC_PHYS,0x18000); p446_dsi=ioremap(P446_DSI_PHYS,0x400); p446_refgen=ioremap(P446_REFGEN_PHYS,0x100);
	if(!p446_ram||!p446_dispcc||!p446_gcc) return -ENOMEM;
	memset_io(p446_ram,0,P446_RAM_BYTES); memset(&h,0,sizeof(h)); h.magic=P446_MAGIC; h.version=1; h.rec_size=sizeof(struct p446_rec); h.capacity=(P446_RAM_BYTES-P446_HDR_BYTES)/sizeof(struct p446_rec); h.init_ns=ktime_get_boottime_ns(); h.commit=P446_HDR_COMMIT; h.hdr_crc32=p446_crc(&h,offsetof(struct p446_hdr,hdr_crc32)); memcpy_toio(p446_ram,&h,sizeof(h)); p446_flush(p446_ram,sizeof(h));
	p446_hdr_shadow=kzalloc(sizeof(*p446_hdr_shadow),GFP_KERNEL); if(p446_hdr_shadow) memcpy(p446_hdr_shadow,&h,sizeof(h));
	proc_create("a52_phase446",0444,NULL,&p446_proc_ops); proc_create("a52_phase446_raw",0444,NULL,&p446_raw_ops);
	INIT_DELAYED_WORK(&p446_work,p446_worker); p446_emit(P446_EVT_INIT,P446_F_MARK); schedule_delayed_work(&p446_work,0); return 0;
}
subsys_initcall(p446_init);
'''

def patch_make(root):
    p=root/'drivers/a52_display/msm/Makefile'; s=p.read_text()
    if MARK not in s:
        s=one(s,'msm_drm-y += sde/sde_crtc.o \\\n',f'# {MARK}\nmsm_drm-y += a52_phase446.o \\\n\nmsm_drm-y += sde/sde_crtc.o \\\n','Makefile')
        p.write_text(s)
    (root/'drivers/a52_display/msm/a52_phase446.c').write_text(C)

def patch_arm(root):
    p=root/'drivers/iommu/arm/arm-smmu/arm-smmu.c'; s=p.read_text()
    if MARK in s: return
    # externs and boot param gate around existing Phase427 treatment
    anchor='struct a52_p427_handoff_state {'
    pre='''/* A52_PHASE446_EARLY_SPLASH_HEARTBEAT_V1 */\nextern void a52_p446_mark(u32 event);\nextern void a52_p446_smmu_update(u32 event,u32 smr3,u32 s2cr3,u32 cb,u32 sctlr,u32 tcr,u64 ttbr0,u32 fsr,u64 pa0,u64 pa1,u32 chosen);\nextern void a52_p446_set_cb2_param(bool en);\nstatic bool a52_p446_cb2_reuse;\nstatic int __init a52_p446_cb2_setup(char *str)\n{\n\tbool v=false; if (!str || kstrtobool(str,&v)) return 0; a52_p446_cb2_reuse=v; a52_p446_set_cb2_param(v); return 1;\n}\n__setup("a52.cb2_reuse=", a52_p446_cb2_setup);\n\n'''
    s=one(s,anchor,pre+anchor,'arm extern/setup')
    old='''\tif (!a52_p427_sid800_dev(dev, &fwspec, &cfg))\n\t\treturn -ENOENT;\n'''
    new='''\tif (!READ_ONCE(a52_p446_cb2_reuse))\n\t\treturn -ENOENT;\n\tif (!a52_p427_sid800_dev(dev, &fwspec, &cfg))\n\t\treturn -ENOENT;\n'''
    s=one(s,old,new,'cb2 gate')
    # helper function before cache boot routes
    anchor='static void a52_p426_cache_boot_routes(struct arm_smmu_device *smmu)\n'
    helper=r'''static void a52_p446_snap_smmu(struct arm_smmu_device *smmu, u32 event, int idx, int cb, struct arm_smmu_domain *dom)
{
	u32 smr=~0U,s2=~0U,sc=~0U,tc=~0U,fs=~0U; u64 tt=~0ULL,pa0=0,pa1=0; struct io_pgtable_ops *ops=NULL;
