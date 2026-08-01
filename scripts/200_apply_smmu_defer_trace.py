#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

SMMU = Path('drivers/a52_display/msm/msm_smmu.c')
KMS = Path('drivers/a52_display/msm/sde/sde_kms.c')
REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')

def rep(text, old, new, label):
    n=text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    return text.replace(old,new,1)

def patch_smmu(t):
    if 'SMMU create defer domain=%d' in t:
        return t
    t=rep(t, '#include <linux/module.h>\n', '#include <linux/module.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n', 'recorder include')
    old='''\tif (!client || !client->domain)\n\t\treturn -ENODEV;\n\n\tret = iommu_map(client->domain, dest_address, dest_address,\n\t\t\tsize, prot);\n'''
    new='''\ta52_ackfr_record("SMMU map client=%d domain=%d base=0x%x size=0x%x",\n\t\t!!client, client && client->domain, dest_address, size);\n\tif (!client || !client->domain) {\n\t\ta52_ackfr_record("SMMU map fail rc=%d client=%d domain=%d",\n\t\t\t-ENODEV, !!client, client && client->domain);\n\t\treturn -ENODEV;\n\t}\n\n\tret = iommu_map(client->domain, dest_address, dest_address,\n\t\t\tsize, prot);\n\ta52_ackfr_record("SMMU map exit rc=%d", ret);\n'''
    t=rep(t,old,new,'one-to-one map trace')
    old='''\tchild = of_find_compatible_node(dev->of_node, NULL, compat);\n\tif (!child) {\n\t\tDRM_DEBUG("unable to find compatible node for %s\\n", compat);\n\t\treturn ERR_PTR(-ENODEV);\n\t}\n\n\tpdev = of_platform_device_create(child, NULL, dev);\n\tif (!pdev) {\n\t\tDRM_ERROR("unable to create smmu platform dev for domain %d\\n",\n\t\t\t\tdomain);\n\t\treturn ERR_PTR(-ENODEV);\n\t}\n\n\tsmmu->client = platform_get_drvdata(pdev);\n\n\treturn &pdev->dev;\n'''
    new='''\ta52_ackfr_record("SMMU create enter domain=%d compat=%s", domain, compat);\n\tchild = of_find_compatible_node(dev->of_node, NULL, compat);\n\tif (!child) {\n\t\ta52_ackfr_record("SMMU create no-node domain=%d", domain);\n\t\tDRM_DEBUG("unable to find compatible node for %s\\n", compat);\n\t\treturn ERR_PTR(-ENODEV);\n\t}\n\n\tpdev = of_platform_device_create(child, NULL, dev);\n\tif (!pdev) {\n\t\ta52_ackfr_record("SMMU create no-pdev domain=%d", domain);\n\t\tDRM_ERROR("unable to create smmu platform dev for domain %d\\n",\n\t\t\t\tdomain);\n\t\treturn ERR_PTR(-ENODEV);\n\t}\n\n\tsmmu->client = platform_get_drvdata(pdev);\n\ta52_ackfr_record("SMMU create state domain=%d driver=%d client=%d",\n\t\tdomain, !!pdev->dev.driver, !!smmu->client);\n\tif (!smmu->client) {\n\t\t/* 5.10 may create the child before its IOMMU domain is ready. */\n\t\ta52_ackfr_record("SMMU create defer domain=%d", domain);\n\t\tof_node_clear_flag(child, OF_POPULATED);\n\t\tplatform_device_unregister(pdev);\n\t\treturn ERR_PTR(-EPROBE_DEFER);\n\t}\n\n\ta52_ackfr_record("SMMU create ready domain=%d secure=%d", domain,\n\t\tsmmu->client->secure);\n\treturn &pdev->dev;\n'''
    t=rep(t,old,new,'device create defer')
    old='''\tmatch = of_match_device(msm_smmu_dt_match, &pdev->dev);\n\tif (!match || !match->data) {\n'''
    new='''\tmatch = of_match_device(msm_smmu_dt_match, &pdev->dev);\n\ta52_ackfr_record("SMMU probe enter match=%d driver=%d", !!match,\n\t\t!!pdev->dev.driver);\n\tif (!match || !match->data) {\n'''
    t=rep(t,old,new,'probe enter')
    old='''\tclient->dev = &pdev->dev;\n\tclient->domain = iommu_get_domain_for_dev(client->dev);\n\tif (!client->domain) {\n\t\tdev_err(&pdev->dev, "iommu get domain for dev failed\\n");\n\t\treturn -EINVAL;\n\t}\n'''
    new='''\tclient->dev = &pdev->dev;\n\tclient->domain = iommu_get_domain_for_dev(client->dev);\n\ta52_ackfr_record("SMMU probe domain compat=%s ready=%d",\n\t\tmatch->compatible, !!client->domain);\n\tif (!client->domain) {\n\t\tdev_err(&pdev->dev, "iommu get domain for dev deferred\\n");\n\t\ta52_ackfr_record("SMMU probe defer compat=%s", match->compatible);\n\t\treturn -EPROBE_DEFER;\n\t}\n'''
    t=rep(t,old,new,'probe defer')
    old='''\tplatform_set_drvdata(pdev, client);\n\n\treturn 0;\n'''
    new='''\tplatform_set_drvdata(pdev, client);\n\ta52_ackfr_record("SMMU probe ready compat=%s secure=%d",\n\t\tmatch->compatible, client->secure);\n\n\treturn 0;\n'''
    t=rep(t,old,new,'probe ready')
    old='''\tret = platform_driver_register(&msm_smmu_driver);\n\tif (ret)\n\t\tpr_err("mdss_smmu_register_driver() failed!\\n");\n\n\treturn ret;\n'''
    new='''\ta52_ackfr_record("SMMU driver-register enter");\n\tret = platform_driver_register(&msm_smmu_driver);\n\ta52_ackfr_record("SMMU driver-register exit rc=%d", ret);\n\tif (ret)\n\t\tpr_err("mdss_smmu_register_driver() failed!\\n");\n\n\treturn ret;\n'''
    t=rep(t,old,new,'driver register trace')
    return t

def patch_kms(t):
    if 'KMSMMU required-domain fail domain=%d rc=%d' in t:
        return t
    old='''\t\tif (IS_ERR(mmu)) {\n\t\t\tret = PTR_ERR(mmu);\n\t\t\tSDE_DEBUG("failed to init iommu id %d: rc:%d\\n",\n\t\t\t\t\t\t\t\ti, ret);\n\t\t\tcontinue;\n\t\t}\n'''
    new='''\t\tif (IS_ERR(mmu)) {\n\t\t\tret = PTR_ERR(mmu);\n\t\t\tSDE_DEBUG("failed to init iommu id %d: rc:%d\\n",\n\t\t\t\t\t\t\t\ti, ret);\n\t\t\tif ((i == MSM_SMMU_DOMAIN_UNSECURE) &&\n\t\t\t\t\tsde_kms->splash_data.num_splash_regions) {\n\t\t\t\ta52_ackfr_record("KMSMMU required-domain fail domain=%d rc=%d",\n\t\t\t\t\ti, ret);\n\t\t\t\tgoto fail;\n\t\t\t}\n\t\t\tcontinue;\n\t\t}\n'''
    return rep(t,old,new,'required unsecure domain')

def patch_rec(t):
    if '!strncmp(message, "SMMU ", 5)' in t:
        return t
    old='''\t       !strncmp(message, "CAT ", 4) ||\n\t       !strncmp(message, "A52GDSC ", 8);\n'''
    new='''\t       !strncmp(message, "CAT ", 4) ||\n\t       !strncmp(message, "SMMU ", 5) ||\n\t       !strncmp(message, "A52GDSC ", 8);\n'''
    return rep(t,old,new,'SMMU retention')

def run(root):
    for rel,fn in ((SMMU,patch_smmu),(KMS,patch_kms),(REC,patch_rec)):
        p=root/rel
        text=p.read_text()
        p.write_text(fn(text))
    print('phase200 SMMU deferred-probe trace applied')

def self_test():
    smmu = '#include <linux/module.h>\nstatic int msm_smmu_one_to_one_map(struct msm_mmu *mmu, uint32_t iova,\n\t\tuint32_t dest_address, uint32_t size, int prot)\n{\n\tstruct msm_smmu *smmu = to_msm_smmu(mmu);\n\tstruct msm_smmu_client *client = msm_smmu_to_client(smmu);\n\tint ret = 0;\n\n\tif (!client || !client->domain)\n\t\treturn -ENODEV;\n\n\tret = iommu_map(client->domain, dest_address, dest_address,\n\t\t\tsize, prot);\n\tif (ret)\n\t\tpr_err("smmu map failed\\n");\n\n\treturn ret;\n}\n\nstatic int msm_smmu_map(struct msm_mmu *mmu, uint64_t iova,\n\t\tstruct sg_table *sgt, unsigned int len, int prot)\n{\n\tstruct msm_smmu *smmu = to_msm_smmu(mmu);\n\tstruct msm_smmu_client *client = msm_smmu_to_client(smmu);\nstatic struct device *msm_smmu_device_create(struct device *dev,\n\t\tenum msm_mmu_domain_type domain,\n\t\tstruct msm_smmu *smmu)\n{\n\tstruct device_node *child;\n\tstruct platform_device *pdev;\n\tint i;\n\tconst char *compat = NULL;\n\n\tfor (i = 0; i < ARRAY_SIZE(msm_smmu_dt_match); i++) {\n\t\tif (msm_smmu_dt_match[i].data == &msm_smmu_domains[domain]) {\n\t\t\tcompat = msm_smmu_dt_match[i].compatible;\n\t\t\tbreak;\n\t\t}\n\t}\n\n\tif (!compat) {\n\t\tDRM_DEBUG("unable to find matching domain for %d\\n", domain);\n\t\treturn ERR_PTR(-ENOENT);\n\t}\n\tDRM_DEBUG("found domain %d compat: %s\\n", domain, compat);\n\n\tchild = of_find_compatible_node(dev->of_node, NULL, compat);\n\tif (!child) {\n\t\tDRM_DEBUG("unable to find compatible node for %s\\n", compat);\n\t\treturn ERR_PTR(-ENODEV);\n\t}\n\n\tpdev = of_platform_device_create(child, NULL, dev);\n\tif (!pdev) {\n\t\tDRM_ERROR("unable to create smmu platform dev for domain %d\\n",\n\t\t\t\tdomain);\n\t\treturn ERR_PTR(-ENODEV);\n\t}\n\n\tsmmu->client = platform_get_drvdata(pdev);\n\n\treturn &pdev->dev;\n}\n\nstatic int msm_smmu_probe(struct platform_device *pdev)\n{\n\tconst struct of_device_id *match;\n\tstruct msm_smmu_client *client;\n\tconst struct msm_smmu_domain *domain;\n\n\tmatch = of_match_device(msm_smmu_dt_match, &pdev->dev);\n\tif (!match || !match->data) {\n\t\tdev_err(&pdev->dev, "probe failed as match data is invalid\\n");\n\t\treturn -EINVAL;\n\t}\n\n\tdomain = match->data;\n\tif (!domain) {\n\t\tdev_err(&pdev->dev, "no matching device found\\n");\n\t\treturn -EINVAL;\n\t}\n\n\tDRM_INFO("probing device %s\\n", match->compatible);\n\n\tclient = devm_kzalloc(&pdev->dev, sizeof(*client), GFP_KERNEL);\n\tif (!client)\n\t\treturn -ENOMEM;\n\n\tclient->dev = &pdev->dev;\n\tclient->domain = iommu_get_domain_for_dev(client->dev);\n\tif (!client->domain) {\n\t\tdev_err(&pdev->dev, "iommu get domain for dev failed\\n");\n\t\treturn -EINVAL;\n\t}\n\tclient->secure = domain->secure;\n\tclient->domain_attached = true;\n\n\tif (!client->dev->dma_parms)\n\t\tclient->dev->dma_parms = devm_kzalloc(client->dev,\n\t\t\t\tsizeof(*client->dev->dma_parms), GFP_KERNEL);\n\tdma_set_max_seg_size(client->dev, ~0U);\n\tdma_set_seg_boundary(client->dev, ~0UL);\n\n\tiommu_set_fault_handler(client->domain,\n\t\t\tmsm_smmu_fault_handler, (void *)client);\n\n\tDRM_INFO("Created domain %s, secure=%d\\n",\n\t\t\tdomain->label, domain->secure);\n\n\tplatform_set_drvdata(pdev, client);\n\n\treturn 0;\n}\nint __init msm_smmu_driver_init(void)\n{\n\tint ret;\n\n\tret = platform_driver_register(&msm_smmu_driver);\n\tif (ret)\n\t\tpr_err("mdss_smmu_register_driver() failed!\\n");\n\n\treturn ret;\n}\n\nvoid __exit msm_smmu_driver_cleanup(void)\n'
    kms = '\t\tif (IS_ERR(mmu)) {\n\t\t\tret = PTR_ERR(mmu);\n\t\t\tSDE_DEBUG("failed to init iommu id %d: rc:%d\\n",\n\t\t\t\t\t\t\t\ti, ret);\n\t\t\tcontinue;\n\t\t}\n'
    rec = '\t       !strncmp(message, "CAT ", 4) ||\n\t       !strncmp(message, "A52GDSC ", 8);\n'
    smmu = patch_smmu(smmu)
    kms = patch_kms(kms)
    rec = patch_rec(rec)
    for marker in ('SMMU create defer domain=%d','return -EPROBE_DEFER;','SMMU probe domain compat=%s ready=%d','SMMU driver-register exit rc=%d'):
        assert marker in smmu, marker
    assert 'KMSMMU required-domain fail domain=%d rc=%d' in kms
    assert '!strncmp(message, "SMMU ", 5)' in rec
    print('phase200 patcher self-test: PASS')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path); ap.add_argument('--self-test',action='store_true'); a=ap.parse_args()
    if a.self_test: self_test(); return
    if not a.root: ap.error('--root required')
    run(a.root)
if __name__=='__main__': main()
