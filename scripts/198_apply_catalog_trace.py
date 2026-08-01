#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


OLD_FUNCTION = r'''struct sde_mdss_cfg *sde_hw_catalog_init(struct drm_device *dev, u32 hw_rev)
{
	int rc;
	struct sde_mdss_cfg *sde_cfg;
	struct device_node *np = dev->dev->of_node;

	sde_cfg = kzalloc(sizeof(*sde_cfg), GFP_KERNEL);
	if (!sde_cfg)
		return ERR_PTR(-ENOMEM);

	sde_cfg->hwversion = hw_rev;

	rc = _sde_hardware_pre_caps(sde_cfg, hw_rev);
	if (rc)
		goto end;

	rc = sde_top_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

#if defined(CONFIG_DISPLAY_SAMSUNG)
	{
		/* sde_hw_catalog_init() be called once for dual dsi,
		 * and two vdds share same sde_kms pointer.
		 * get sde_kms from primary vdd, then call ss_callback
		 * for primary and secondary vdd, respectively.
		 */
		struct samsung_display_driver_data *vdd = ss_get_vdd(PRIMARY_DISPLAY_NDX);
		int i;

		if (IS_ERR_OR_NULL(vdd))
			goto done;

		for (i = PRIMARY_DISPLAY_NDX; i <= SECONDARY_DISPLAY_NDX; i++) {
			vdd = ss_get_vdd(i);
			ss_pba_config(vdd, (void *)sde_cfg);
		}
	}
done:
#endif

	rc = sde_perf_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_qos_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_rot_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	/* uidle must be done before sspp and ctl,
	 * so if something goes wrong, we won't
	 * enable it in ctl and sspp.
	 */
	rc = sde_uidle_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_ctl_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_sspp_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_dspp_top_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_dspp_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_ds_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_dsc_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_pp_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	/* mixer parsing should be done after dspp,
	 * ds and pp for mapping setup
	 */
	rc = sde_mixer_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_intf_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_wb_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	/* cdm parsing should be done after intf and wb for mapping setup */
	rc = sde_cdm_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_vbif_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_parse_reg_dma_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_parse_merge_3d_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = sde_qdss_parse_dt(np, sde_cfg);
	if (rc)
		goto end;

	rc = _sde_hardware_post_caps(sde_cfg, hw_rev);
	if (rc)
		goto end;

	return sde_cfg;

end:
	sde_hw_catalog_deinit(sde_cfg);
	return NULL;
}'''


NEW_FUNCTION = r'''struct sde_mdss_cfg *sde_hw_catalog_init(struct drm_device *dev, u32 hw_rev)
{
	int rc;
	struct sde_mdss_cfg *sde_cfg;
	struct device_node *np = dev->dev->of_node;

	a52_ackfr_record("CAT enter rev=0x%x np-null=%d", hw_rev, !np);
	a52_ackfr_record("CAT alloc enter bytes=%zu", sizeof(*sde_cfg));
	sde_cfg = kzalloc(sizeof(*sde_cfg), GFP_KERNEL);
	a52_ackfr_record("CAT alloc exit null=%d", !sde_cfg);
	if (!sde_cfg)
		return ERR_PTR(-ENOMEM);

	sde_cfg->hwversion = hw_rev;

	a52_ackfr_record("CAT pre-caps enter");
	rc = _sde_hardware_pre_caps(sde_cfg, hw_rev);
	a52_ackfr_record("CAT pre-caps exit rc=%d", rc);
	if (rc)
		goto end;

	a52_ackfr_record("CAT top enter");
	rc = sde_top_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT top exit rc=%d mdp=%u", rc, sde_cfg->mdp_count);
	if (rc)
		goto end;

#if defined(CONFIG_DISPLAY_SAMSUNG)
	{
		/* sde_hw_catalog_init() be called once for dual dsi,
		 * and two vdds share same sde_kms pointer.
		 * get sde_kms from primary vdd, then call ss_callback
		 * for primary and secondary vdd, respectively.
		 */
		struct samsung_display_driver_data *vdd;
		int i;

		a52_ackfr_record("CAT samsung primary enter");
		vdd = ss_get_vdd(PRIMARY_DISPLAY_NDX);
		a52_ackfr_record("CAT samsung primary exit null=%d err=%ld",
			!vdd, IS_ERR(vdd) ? PTR_ERR(vdd) : 0L);
		if (IS_ERR_OR_NULL(vdd))
			goto done;

		for (i = PRIMARY_DISPLAY_NDX; i <= SECONDARY_DISPLAY_NDX; i++) {
			a52_ackfr_record("CAT samsung pba enter i=%d", i);
			vdd = ss_get_vdd(i);
			a52_ackfr_record("CAT samsung vdd i=%d null=%d err=%ld", i,
				!vdd, IS_ERR(vdd) ? PTR_ERR(vdd) : 0L);
			ss_pba_config(vdd, (void *)sde_cfg);
			a52_ackfr_record("CAT samsung pba exit i=%d", i);
		}
	}
done:
	a52_ackfr_record("CAT samsung done");
#endif

	a52_ackfr_record("CAT perf enter");
	rc = sde_perf_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT perf exit rc=%d", rc);
	if (rc)
		goto end;

	a52_ackfr_record("CAT qos enter");
	rc = sde_qos_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT qos exit rc=%d", rc);
	if (rc)
		goto end;

	a52_ackfr_record("CAT rot enter");
	rc = sde_rot_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT rot exit rc=%d", rc);
	if (rc)
		goto end;

	/* uidle must be done before sspp and ctl,
	 * so if something goes wrong, we won't
	 * enable it in ctl and sspp.
	 */
	a52_ackfr_record("CAT uidle enter");
	rc = sde_uidle_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT uidle exit rc=%d", rc);
	if (rc)
		goto end;

	a52_ackfr_record("CAT ctl enter");
	rc = sde_ctl_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT ctl exit rc=%d count=%u", rc, sde_cfg->ctl_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT sspp enter");
	rc = sde_sspp_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT sspp exit rc=%d count=%u", rc, sde_cfg->sspp_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT dspp-top enter");
	rc = sde_dspp_top_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT dspp-top exit rc=%d", rc);
	if (rc)
		goto end;

	a52_ackfr_record("CAT dspp enter");
	rc = sde_dspp_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT dspp exit rc=%d count=%u", rc, sde_cfg->dspp_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT ds enter");
	rc = sde_ds_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT ds exit rc=%d count=%u", rc, sde_cfg->ds_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT dsc enter");
	rc = sde_dsc_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT dsc exit rc=%d count=%u", rc, sde_cfg->dsc_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT pp enter");
	rc = sde_pp_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT pp exit rc=%d count=%u", rc, sde_cfg->pingpong_count);
	if (rc)
		goto end;

	/* mixer parsing should be done after dspp,
	 * ds and pp for mapping setup
	 */
	a52_ackfr_record("CAT mixer enter");
	rc = sde_mixer_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT mixer exit rc=%d count=%u", rc, sde_cfg->mixer_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT intf enter");
	rc = sde_intf_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT intf exit rc=%d count=%u", rc, sde_cfg->intf_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT wb enter");
	rc = sde_wb_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT wb exit rc=%d count=%u", rc, sde_cfg->wb_count);
	if (rc)
		goto end;

	/* cdm parsing should be done after intf and wb for mapping setup */
	a52_ackfr_record("CAT cdm enter");
	rc = sde_cdm_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT cdm exit rc=%d count=%u", rc, sde_cfg->cdm_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT vbif enter");
	rc = sde_vbif_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT vbif exit rc=%d count=%u", rc, sde_cfg->vbif_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT reg-dma enter");
	rc = sde_parse_reg_dma_dt(np, sde_cfg);
	a52_ackfr_record("CAT reg-dma exit rc=%d count=%u", rc,
		sde_cfg->reg_dma_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT merge3d enter");
	rc = sde_parse_merge_3d_dt(np, sde_cfg);
	a52_ackfr_record("CAT merge3d exit rc=%d count=%u", rc,
		sde_cfg->merge_3d_count);
	if (rc)
		goto end;

	a52_ackfr_record("CAT qdss enter");
	rc = sde_qdss_parse_dt(np, sde_cfg);
	a52_ackfr_record("CAT qdss exit rc=%d", rc);
	if (rc)
		goto end;

	a52_ackfr_record("CAT post-caps enter");
	rc = _sde_hardware_post_caps(sde_cfg, hw_rev);
	a52_ackfr_record("CAT post-caps exit rc=%d", rc);
	if (rc)
		goto end;

	a52_ackfr_record("CAT success ctl=%u sspp=%u mixer=%u intf=%u wb=%u",
		sde_cfg->ctl_count, sde_cfg->sspp_count, sde_cfg->mixer_count,
		sde_cfg->intf_count, sde_cfg->wb_count);
	return sde_cfg;

end:
	a52_ackfr_record("CAT fail rc=%d", rc);
	sde_hw_catalog_deinit(sde_cfg);
	return NULL;
}'''


def patch_file(path: Path) -> None:
    text = path.read_text()
    text = replace_once(
        text,
        '#include <linux/pm_qos.h>\n',
        '#include <linux/pm_qos.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n',
        'recorder include',
    )
    text = replace_once(text, OLD_FUNCTION, NEW_FUNCTION, 'catalog init function')
    path.write_text(text)


def self_test() -> None:
    sample = '#include <linux/pm_qos.h>\n\n' + OLD_FUNCTION + '\n'
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'sde_hw_catalog.c'
        path.write_text(sample)
        patch_file(path)
        out = path.read_text()
        assert '#include <linux/a52_ack_secure_flight_recorder.h>' in out
        assert 'CAT enter rev=0x%x np-null=%d' in out
        assert 'CAT samsung pba exit i=%d' in out
        assert 'CAT post-caps exit rc=%d' in out
        assert 'CAT success ctl=%u sspp=%u mixer=%u intf=%u wb=%u' in out
        assert 'CAT fail rc=%d' in out
        assert OLD_FUNCTION not in out
    print('phase198 catalog trace patcher self-test: PASS')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    if args.root is None:
        parser.error('--root is required unless --self-test is used')

    target = args.root / 'drivers/a52_display/msm/sde/sde_hw_catalog.c'
    patch_file(target)
    print(f'phase198 catalog trace applied: {target}')


if __name__ == '__main__':
    main()
