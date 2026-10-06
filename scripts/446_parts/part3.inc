	if (!smmu) return;
	if (idx >= 0 && idx < smmu->num_mapping_groups) { smr=arm_smmu_gr0_read(smmu,ARM_SMMU_GR0_SMR(idx)); s2=arm_smmu_gr0_read(smmu,ARM_SMMU_GR0_S2CR(idx)); }
	if (cb >= 0 && cb < smmu->num_context_banks) { sc=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_SCTLR); tc=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_TCR); tt=arm_smmu_cb_readq(smmu,cb,ARM_SMMU_CB_TTBR0); fs=arm_smmu_cb_read(smmu,cb,ARM_SMMU_CB_FSR); }
	if (dom) { ops=dom->pgtbl_ops; if (ops && ops->iova_to_phys) { pa0=ops->iova_to_phys(ops,0xff400000ULL); pa1=ops->iova_to_phys(ops,0xff413000ULL); } }
	a52_p446_smmu_update(event,smr,s2,(u32)cb,sc,tc,tt,fs,pa0,pa1,dom?(u32)dom->cfg.cbndx:(u32)cb);
}

'''
    s=one(s,anchor,helper+anchor,'smmu helper')
    # earliest bootloader CB2 snapshot after boot route cache
    old='''\ta52_p426_take_snapshot(smmu, &a52_p426_boot, true);\n\tsmp_wmb();\n'''
    new='''\ta52_p426_take_snapshot(smmu, &a52_p426_boot, true);\n\ta52_p446_snap_smmu(smmu, 1U, 3, 2, NULL); /* F446-01 bootloader CB2 */\n\tsmp_wmb();\n'''
    s=one(s,old,new,'boot cb2 snapshot')
    # around allocation
    old='''\t} else {\n\t\tret = arm_smmu_alloc_context_bank(smmu_domain, smmu, dev, start);\n\t}\n'''
    new='''\t} else {\n\t\tif (a52_p427_sid800_dev(dev, NULL, NULL)) a52_p446_snap_smmu(smmu, 2U, 3, 2, NULL); /* F446-02 pre alloc */\n\t\tret = arm_smmu_alloc_context_bank(smmu_domain, smmu, dev, start);\n\t\tif (a52_p427_sid800_dev(dev, NULL, NULL) && ret >= 0) a52_p446_snap_smmu(smmu, 3U, 3, ret, NULL); /* F446-03 chosen CB */\n\t}\n'''
    s=one(s,old,new,'alloc markers')
    # after context bank write
    old='''\t\tarm_smmu_init_context_bank(smmu_domain, &pgtbl_cfg);\n\t\tarm_smmu_write_context_bank(smmu, cfg->cbndx);\n'''
    new='''\t\tarm_smmu_init_context_bank(smmu_domain, &pgtbl_cfg);\n\t\tif (a52_p427_sid800_dev(dev, NULL, NULL)) a52_p446_snap_smmu(smmu, 4U, 3, cfg->cbndx, smmu_domain); /* F446-04 before CB write */\n\t\tarm_smmu_write_context_bank(smmu, cfg->cbndx);\n\t\tif (a52_p427_sid800_dev(dev, NULL, NULL)) a52_p446_snap_smmu(smmu, 5U, 3, cfg->cbndx, smmu_domain); /* F446-05 CB programmed M=0 */\n'''
    s=one(s,old,new,'context write markers')
    # S2CR markers specifically 08/09
    old='''\t\ts2cr[idx].type = type;\n\t\ts2cr[idx].privcfg = S2CR_PRIVCFG_DEFAULT;\n\t\ts2cr[idx].cbndx = cbndx;\n\t\tarm_smmu_write_s2cr(smmu, idx);\n'''
    new='''\t\tif (idx == 3) a52_p446_snap_smmu(smmu, 8U, idx, s2cr[idx].cbndx, smmu_domain); /* F446-08 pre S2CR3 */\n\t\ts2cr[idx].type = type;\n\t\ts2cr[idx].privcfg = S2CR_PRIVCFG_DEFAULT;\n\t\ts2cr[idx].cbndx = cbndx;\n\t\tarm_smmu_write_s2cr(smmu, idx);\n\t\tif (idx == 3) a52_p446_snap_smmu(smmu, 9U, idx, cbndx, smmu_domain); /* F446-09 post S2CR3 */\n'''
    s=one(s,old,new,'s2cr 08/09')
    # earlymap enable translation marker
    old='''\t\t} else {\n\t\t\tret = arm_smmu_enable_s1_translations(smmu_domain);\n\t\t\tif (!ret)\n\t\t\t\tsmmu_domain->attributes &= ~BIT(DOMAIN_ATTR_EARLY_MAP);\n\t\t}\n'''
    new='''\t\t} else {\n\t\t\tif (smmu_domain->smmu && smmu_domain->cfg.cbndx < smmu_domain->smmu->num_context_banks) a52_p446_snap_smmu(smmu_domain->smmu, 6U, 3, smmu_domain->cfg.cbndx, smmu_domain); /* F446-06 pre M=1 */\n\t\t\tret = arm_smmu_enable_s1_translations(smmu_domain);\n\t\t\tif (!ret) {\n\t\t\t\tsmmu_domain->attributes &= ~BIT(DOMAIN_ATTR_EARLY_MAP);\n\t\t\t\ta52_p446_snap_smmu(smmu_domain->smmu, 7U, 3, smmu_domain->cfg.cbndx, smmu_domain); /* F446-07 post M=1 */\n\t\t\t}\n\t\t}\n'''
    s=one(s,old,new,'earlymap markers')
    p.write_text(s)

def patch_msm(root):
    p=root/'drivers/a52_display/msm/msm_drv.c'; s=p.read_text()
    if MARK in s: return
    inc='''#include "msm_drv.h"\n'''
    if inc in s: s=one(s,inc,inc+'/* '+MARK+' */\nextern void a52_p446_mark(u32 event);\n', 'msm decl')
    else:
        # put after first include block
        s='/* '+MARK+' */\nextern void a52_p446_mark(u32 event);\n'+s
    s=one(s,'static int msm_drm_init(struct device *dev, struct drm_driver *drv)\n{\n','static int msm_drm_init(struct device *dev, struct drm_driver *drv)\n{\n\ta52_p446_mark(10U); /* F446-10 msm_drm_init enter */\n','drm enter')
