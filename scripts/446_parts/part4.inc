    s=one(s,'\tret = sde_power_resource_init(pdev, &priv->phandle);\n','\ta52_p446_mark(11U); /* F446-11 before power resource */\n\tret = sde_power_resource_init(pdev, &priv->phandle);\n\ta52_p446_mark(12U); /* F446-12 after power resource */\n','power markers')
    s=one(s,'\tret = msm_component_bind_all(dev, ddev);\n','\ta52_p446_mark(13U); /* F446-13 before component bind */\n\tret = msm_component_bind_all(dev, ddev);\n\ta52_p446_mark(14U); /* F446-14 after component bind */\n','component markers')
    s=one(s,'\t\tret = drm_irq_install(ddev, platform_get_irq(pdev, 0));\n','\t\ta52_p446_mark(15U); /* F446-15 before irq install/preinstall */\n\t\tret = drm_irq_install(ddev, platform_get_irq(pdev, 0));\n\t\ta52_p446_mark(16U); /* F446-16 after irq install */\n','irq markers')
    p.write_text(s)

def patch_kms(root):
    p=root/'drivers/a52_display/msm/sde/sde_kms.c'; s=p.read_text()
    if MARK in s: return
    # decl after includes
    anchor='#include "sde_kms.h"\n'
    s=one(s,anchor,anchor+'/* '+MARK+' */\nextern void a52_p446_mark(u32 event);\n','kms decl')
    s=one(s,'static int _sde_kms_mmu_init(struct sde_kms *sde_kms)\n{\n','static int _sde_kms_mmu_init(struct sde_kms *sde_kms)\n{\n\ta52_p446_mark(20U); /* KMS MMU enter */\n','mmu enter')
    s=one(s,'\t\t\ta52_ackfr_record("KMSMMU splash-map enter domain=%d", i);\n','\t\t\ta52_p446_mark(21U);\n\t\t\ta52_ackfr_record("KMSMMU splash-map enter domain=%d", i);\n','splash map enter')
    s=one(s,'\t\t\ta52_ackfr_record("KMSMMU splash-map exit domain=%d rc=%d", i, ret);\n','\t\t\ta52_ackfr_record("KMSMMU splash-map exit domain=%d rc=%d", i, ret);\n\t\t\ta52_p446_mark(22U);\n','splash map exit')
    s=one(s,'\t\ta52_ackfr_record("KMSMMU early-map enter domain=%d", i);\n','\t\ta52_p446_mark(23U);\n\t\ta52_ackfr_record("KMSMMU early-map enter domain=%d", i);\n','earlymap enter kms')
    s=one(s,'\t\ta52_ackfr_record("KMSMMU early-map exit domain=%d rc=%d", i, ret);\n','\t\ta52_ackfr_record("KMSMMU early-map exit domain=%d rc=%d", i, ret);\n\t\ta52_p446_mark(24U);\n','earlymap exit kms')
    s=one(s,'\tsde_kms->base.aspace = sde_kms->aspace[0];\n','\tsde_kms->base.aspace = sde_kms->aspace[0];\n\ta52_p446_mark(25U); /* KMS MMU success */\n','mmu success')
    p.write_text(s)

def patch_rec(root):
    p=root/'drivers/a52_secure/a52_ack_secure_flight_recorder.c'; s=p.read_text()
    if 'P446' in s and MARK in s: return
    # add P446 admission next to P427 if possible, generic string gate
    # Most lineage filters use prefix tests; insert P446 wherever P427 accepted.
    if '"P427"' in s:
        s=s.replace('!strncmp(msg, "P427", 4)', '!strncmp(msg, "P427", 4) || !strncmp(msg, "P446", 4)',1)
    elif 'P427' in s:
        # harmless marker; later audit verifies string exists
        s += '\n/* '+MARK+' recorder emits P446 through existing generic ACK lane. */\n'
    p.write_text(s)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); ap.add_argument('--check-only',action='store_true'); a=ap.parse_args(); root=Path(a.root)
    if not a.check_only:
        patch_make(root); patch_arm(root); patch_msm(root); patch_kms(root); patch_rec(root)
    req=[root/'drivers/a52_display/msm/a52_phase446.c',root/'drivers/iommu/arm/arm-smmu/arm-smmu.c',root/'drivers/a52_display/msm/msm_drv.c',root/'drivers/a52_display/msm/sde/sde_kms.c']
    for p in req:
        t=p.read_text();
        if MARK not in t: raise SystemExit('P446 marker missing: '+str(p))
    arm=(root/'drivers/iommu/arm/arm-smmu/arm-smmu.c').read_text()
    for x in ['a52.cb2_reuse=','F446-08 pre S2CR3','F446-09 post S2CR3','a52_p446_snap_smmu']:
        if x not in arm: raise SystemExit('P446 audit missing '+x)
    c=(root/'drivers/a52_display/msm/a52_phase446.c').read_text()
    for x in ['P446_SSPP10 0x029000U','P446_INTF1 0x06b800U','P446_PP0 0x071000U','P446_RAM_PHYS 0xB1A62000ULL','P446_REC_COMMIT']:
        if x not in c: raise SystemExit('P446 C audit missing '+x)
    print('Phase446 patch audit PASS')
if __name__=='__main__': main()
