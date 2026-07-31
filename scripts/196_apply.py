#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path) -> None:
    text = path.read_text()

    text = replace_once(text,
'''\tfor (i = 0; i < MSM_SMMU_DOMAIN_MAX; i++) {
\t\tstruct msm_gem_address_space *aspace;

\t\tmmu = msm_smmu_new(sde_kms->dev->dev, i);
\t\tif (IS_ERR(mmu)) {
''',
'''\ta52_ackfr_record("KMSMMU enter domains=%d regions=%u", MSM_SMMU_DOMAIN_MAX,
\t\tsde_kms->splash_data.num_splash_regions);
\tfor (i = 0; i < MSM_SMMU_DOMAIN_MAX; i++) {
\t\tstruct msm_gem_address_space *aspace;

\t\ta52_ackfr_record("KMSMMU new enter domain=%d", i);
\t\tmmu = msm_smmu_new(sde_kms->dev->dev, i);
\t\ta52_ackfr_record("KMSMMU new exit domain=%d rc=%ld", i,
\t\t\tIS_ERR(mmu) ? PTR_ERR(mmu) : 0L);
\t\tif (IS_ERR(mmu)) {
''', 'mmu new')

    text = replace_once(text,
'''\t\taspace = msm_gem_smmu_address_space_create(sde_kms->dev,
\t\t\tmmu, "sde");
\t\tif (IS_ERR(aspace)) {
''',
'''\t\ta52_ackfr_record("KMSMMU aspace enter domain=%d", i);
\t\taspace = msm_gem_smmu_address_space_create(sde_kms->dev,
\t\t\tmmu, "sde");
\t\ta52_ackfr_record("KMSMMU aspace exit domain=%d rc=%ld", i,
\t\t\tIS_ERR(aspace) ? PTR_ERR(aspace) : 0L);
\t\tif (IS_ERR(aspace)) {
''', 'mmu aspace')

    text = replace_once(text,
'''\t\t\tret = _sde_kms_map_all_splash_regions(sde_kms);
\t\t\tif (ret) {
''',
'''\t\t\ta52_ackfr_record("KMSMMU splash-map enter domain=%d", i);
\t\t\tret = _sde_kms_map_all_splash_regions(sde_kms);
\t\t\ta52_ackfr_record("KMSMMU splash-map exit domain=%d rc=%d", i, ret);
\t\t\tif (ret) {
''', 'mmu splash map')

    text = replace_once(text,
'''\t\tret = mmu->funcs->set_attribute(mmu, DOMAIN_ATTR_EARLY_MAP,
\t\t\t\t &early_map);
\t\tif (ret) {
''',
'''\t\ta52_ackfr_record("KMSMMU early-map enter domain=%d", i);
\t\tret = mmu->funcs->set_attribute(mmu, DOMAIN_ATTR_EARLY_MAP,
\t\t\t\t &early_map);
\t\ta52_ackfr_record("KMSMMU early-map exit domain=%d rc=%d", i, ret);
\t\tif (ret) {
''', 'mmu early map')

    text = replace_once(text,
'''\tsde_kms->base.aspace = sde_kms->aspace[0];

\treturn 0;

early_map_fail:
''',
'''\tsde_kms->base.aspace = sde_kms->aspace[0];
\ta52_ackfr_record("KMSMMU success base-null=%d", !sde_kms->base.aspace);

\treturn 0;

early_map_fail:
\ta52_ackfr_record("KMSMMU fail stage=early-map rc=%d", ret);
''', 'mmu result')

    text = replace_once(text,
'''fail:
\t_sde_kms_mmu_destroy(sde_kms);

\treturn ret;
}
''',
'''fail:
\ta52_ackfr_record("KMSMMU fail stage=destroy rc=%d", ret);
\t_sde_kms_mmu_destroy(sde_kms);

\treturn ret;
}
''', 'mmu fail')

    text = replace_once(text,
'''\t_sde_kms_core_hw_rev_init(sde_kms);

\tpr_info("sde hardware revision:0x%x\\n", sde_kms->core_rev);

\tsde_kms->catalog = sde_hw_catalog_init(dev, sde_kms->core_rev);
''',
'''\ta52_ackfr_record("KMSBLK core-rev enter");
\t_sde_kms_core_hw_rev_init(sde_kms);
\ta52_ackfr_record("KMSBLK core-rev exit rev=0x%x", sde_kms->core_rev);

\tpr_info("sde hardware revision:0x%x\\n", sde_kms->core_rev);

\ta52_ackfr_record("KMSBLK catalog enter rev=0x%x", sde_kms->core_rev);
\tsde_kms->catalog = sde_hw_catalog_init(dev, sde_kms->core_rev);
\ta52_ackfr_record("KMSBLK catalog exit rc=%ld null=%d",
\t\tIS_ERR(sde_kms->catalog) ? PTR_ERR(sde_kms->catalog) : 0L,
\t\t!sde_kms->catalog);
''', 'catalog')

    text = replace_once(text,
'''\trc = _sde_kms_hw_init_power_helper(dev, sde_kms);
\tif (rc) {
''',
'''\ta52_ackfr_record("KMSBLK power-helper enter");
\trc = _sde_kms_hw_init_power_helper(dev, sde_kms);
\ta52_ackfr_record("KMSBLK power-helper exit rc=%d genpd=%d", rc,
\t\tsde_kms->genpd_init);
\tif (rc) {
''', 'power helper')

    text = replace_once(text,
'''\trc = _sde_kms_mmu_init(sde_kms);
\tif (rc) {
''',
'''\ta52_ackfr_record("KMSBLK mmu enter");
\trc = _sde_kms_mmu_init(sde_kms);
\ta52_ackfr_record("KMSBLK mmu exit rc=%d base-null=%d", rc,
\t\t!sde_kms->base.aspace);
\tif (rc) {
''', 'block mmu')

    text = replace_once(text,
'''\trc = sde_reg_dma_init(sde_kms->reg_dma, sde_kms->catalog,
\t\t\tsde_kms->dev);
\tif (rc) {
''',
'''\ta52_ackfr_record("KMSBLK reg-dma enter");
\trc = sde_reg_dma_init(sde_kms->reg_dma, sde_kms->catalog,
\t\t\tsde_kms->dev);
\ta52_ackfr_record("KMSBLK reg-dma exit rc=%d", rc);
\tif (rc) {
''', 'reg dma')

    text = replace_once(text,
'''\trc = sde_rm_init(rm, sde_kms->catalog, sde_kms->mmio,
\t\t\tsde_kms->dev);
\tif (rc) {
''',
'''\ta52_ackfr_record("KMSBLK rm enter");
\trc = sde_rm_init(rm, sde_kms->catalog, sde_kms->mmio,
\t\t\tsde_kms->dev);
\ta52_ackfr_record("KMSBLK rm exit rc=%d", rc);
\tif (rc) {
''', 'rm')

    text = replace_once(text,
'''\tsde_kms->hw_intr = sde_hw_intr_init(sde_kms->mmio, sde_kms->catalog);
\tif (IS_ERR_OR_NULL(sde_kms->hw_intr)) {
''',
'''\ta52_ackfr_record("KMSBLK intr enter");
\tsde_kms->hw_intr = sde_hw_intr_init(sde_kms->mmio, sde_kms->catalog);
\ta52_ackfr_record("KMSBLK intr exit rc=%ld null=%d",
\t\tIS_ERR(sde_kms->hw_intr) ? PTR_ERR(sde_kms->hw_intr) : 0L,
\t\t!sde_kms->hw_intr);
\tif (IS_ERR_OR_NULL(sde_kms->hw_intr)) {
''', 'intr')

    text = replace_once(text,
'''\t\tret = sde_rm_cont_splash_res_init(priv, &sde_kms->rm,
\t\t\t\t&sde_kms->splash_data, sde_kms->catalog);

\t\tfor (i = 0; i < display_count; i++) {
''',
'''\t\ta52_ackfr_record("KMSBLK splash-res enter displays=%d", display_count);
\t\tret = sde_rm_cont_splash_res_init(priv, &sde_kms->rm,
\t\t\t\t&sde_kms->splash_data, sde_kms->catalog);
\t\ta52_ackfr_record("KMSBLK splash-res exit rc=%d", ret);

\t\tfor (i = 0; i < display_count; i++) {
''', 'splash resource')

    text = replace_once(text,
'''\t\t\tdisplay = &sde_kms->splash_data.splash_display[i];
\t\t\t/*
''',
'''\t\t\tdisplay = &sde_kms->splash_data.splash_display[i];
\t\t\ta52_ackfr_record("KMSBLK splash-display i=%d enabled=%d ret=%d",
\t\t\t\ti, display->cont_splash_enabled, ret);
\t\t\t/*
''', 'splash display')

    text = replace_once(text,
'''\tsde_kms->hw_mdp = sde_rm_get_mdp(&sde_kms->rm);
\tif (IS_ERR_OR_NULL(sde_kms->hw_mdp)) {
''',
'''\ta52_ackfr_record("KMSBLK mdp enter");
\tsde_kms->hw_mdp = sde_rm_get_mdp(&sde_kms->rm);
\ta52_ackfr_record("KMSBLK mdp exit rc=%ld null=%d",
\t\tIS_ERR(sde_kms->hw_mdp) ? PTR_ERR(sde_kms->hw_mdp) : 0L,
\t\t!sde_kms->hw_mdp);
\tif (IS_ERR_OR_NULL(sde_kms->hw_mdp)) {
''', 'mdp')

    text = replace_once(text,
'''\t\tsde_kms->hw_vbif[i] = sde_hw_vbif_init(vbif_idx,
\t\t\t\tsde_kms->vbif[vbif_idx], sde_kms->catalog);
\t\tif (IS_ERR_OR_NULL(sde_kms->hw_vbif[vbif_idx])) {
''',
'''\t\ta52_ackfr_record("KMSBLK vbif enter i=%d id=%u", i, vbif_idx);
\t\tsde_kms->hw_vbif[i] = sde_hw_vbif_init(vbif_idx,
\t\t\t\tsde_kms->vbif[vbif_idx], sde_kms->catalog);
\t\ta52_ackfr_record("KMSBLK vbif exit i=%d id=%u rc=%ld null=%d", i,
\t\t\tvbif_idx, IS_ERR(sde_kms->hw_vbif[vbif_idx]) ?
\t\t\tPTR_ERR(sde_kms->hw_vbif[vbif_idx]) : 0L,
\t\t\t!sde_kms->hw_vbif[vbif_idx]);
\t\tif (IS_ERR_OR_NULL(sde_kms->hw_vbif[vbif_idx])) {
''', 'vbif')

    text = replace_once(text,
'''\tsde_kms->hw_sid = sde_hw_sid_init(sde_kms->sid,
\t\t\t\tsde_kms->sid_len, sde_kms->catalog);
\tif (IS_ERR(sde_kms->hw_sid)) {
''',
'''\ta52_ackfr_record("KMSBLK sid enter");
\tsde_kms->hw_sid = sde_hw_sid_init(sde_kms->sid,
\t\t\t\tsde_kms->sid_len, sde_kms->catalog);
\ta52_ackfr_record("KMSBLK sid exit rc=%ld null=%d",
\t\tIS_ERR(sde_kms->hw_sid) ? PTR_ERR(sde_kms->hw_sid) : 0L,
\t\t!sde_kms->hw_sid);
\tif (IS_ERR(sde_kms->hw_sid)) {
''', 'sid')

    text = replace_once(text,
'''\trc = sde_core_perf_init(&sde_kms->perf, dev, sde_kms->catalog,
\t\t\t&priv->phandle, "core_clk");
\tif (rc) {
''',
'''\ta52_ackfr_record("KMSBLK perf enter");
\trc = sde_core_perf_init(&sde_kms->perf, dev, sde_kms->catalog,
\t\t\t&priv->phandle, "core_clk");
\ta52_ackfr_record("KMSBLK perf exit rc=%d", rc);
\tif (rc) {
''', 'perf')

    text = replace_once(text,
'''\trc = _sde_kms_drm_obj_init(sde_kms);
\tif (rc) {
''',
'''\ta52_ackfr_record("KMSBLK drm-obj enter");
\trc = _sde_kms_drm_obj_init(sde_kms);
\ta52_ackfr_record("KMSBLK drm-obj exit rc=%d crtc=%d enc=%d conn=%d plane=%d",
\t\trc, priv->num_crtcs, priv->num_encoders,
\t\tpriv->num_connectors, priv->num_planes);
\tif (rc) {
''', 'drm object')

    text = replace_once(text,
'''\tret = sde_core_irq_domain_add(sde_kms);
\tif (ret)
''',
'''\ta52_ackfr_record("KMSOBJ irq-domain enter");
\tret = sde_core_irq_domain_add(sde_kms);
\ta52_ackfr_record("KMSOBJ irq-domain exit rc=%d", ret);
\tif (ret)
''', 'object irq')

    text = replace_once(text,
'''\tif (!_sde_kms_get_displays(sde_kms))
\t\t(void)_sde_kms_setup_displays(dev, priv, sde_kms);

\tmax_crtc_count = min(catalog->mixer_count, priv->num_encoders);
''',
'''\t{
\t\tint display_rc = _sde_kms_get_displays(sde_kms);

\t\ta52_ackfr_record("KMSOBJ get-displays rc=%d", display_rc);
\t\tif (!display_rc) {
\t\t\tint setup_rc = _sde_kms_setup_displays(dev, priv, sde_kms);

\t\t\ta52_ackfr_record("KMSOBJ setup-displays rc=%d enc=%d conn=%d",
\t\t\t\tsetup_rc, priv->num_encoders, priv->num_connectors);
\t\t}
\t}

\tmax_crtc_count = min(catalog->mixer_count, priv->num_encoders);
\ta52_ackfr_record("KMSOBJ counts mixers=%u sspp=%u max-crtc=%d enc=%d conn=%d",
\t\tcatalog->mixer_count, catalog->sspp_count, max_crtc_count,
\t\tpriv->num_encoders, priv->num_connectors);
''', 'object displays')

    text = replace_once(text,
'''\t\tplane = sde_plane_init(dev, catalog->sspp[i].id, primary,
\t\t\t\t(1UL << max_crtc_count) - 1, 0);
\t\tif (IS_ERR(plane)) {
''',
'''\t\ta52_ackfr_record("KMSOBJ plane enter i=%d id=%u primary=%d", i,
\t\t\tcatalog->sspp[i].id, primary);
\t\tplane = sde_plane_init(dev, catalog->sspp[i].id, primary,
\t\t\t\t(1UL << max_crtc_count) - 1, 0);
\t\ta52_ackfr_record("KMSOBJ plane exit i=%d rc=%ld", i,
\t\t\tIS_ERR(plane) ? PTR_ERR(plane) : 0L);
\t\tif (IS_ERR(plane)) {
''', 'object plane')

    text = replace_once(text,
'''\t\tcrtc = sde_crtc_init(dev, primary_planes[i]);
\t\tif (IS_ERR(crtc)) {
''',
'''\t\ta52_ackfr_record("KMSOBJ crtc enter i=%d", i);
\t\tcrtc = sde_crtc_init(dev, primary_planes[i]);
\t\ta52_ackfr_record("KMSOBJ crtc exit i=%d rc=%ld", i,
\t\t\tIS_ERR(crtc) ? PTR_ERR(crtc) : 0L);
\t\tif (IS_ERR(crtc)) {
''', 'object crtc')

    path.write_text(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    args = ap.parse_args()
    path = Path(args.root) / 'drivers/a52_display/msm/sde/sde_kms.c'
    patch(path)
    print('phase196 KMS hardware-block trace applied')

if __name__ == '__main__':
    main()
