#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one match, found {count}')
    return text.replace(old, new, 1)


def replace_function(text: str, signature: str, replacement: str) -> str:
    search_from = 0
    start = -1
    brace = -1
    while True:
        candidate = text.find(signature, search_from)
        if candidate < 0:
            raise SystemExit(f'function definition not found: {signature}')
        candidate_brace = text.find('{', candidate)
        candidate_semicolon = text.find(';', candidate)
        if candidate_brace >= 0 and (candidate_semicolon < 0 or candidate_brace < candidate_semicolon):
            start = candidate
            brace = candidate_brace
            break
        search_from = candidate + len(signature)
    depth = 0
    end = None
    for index in range(brace, len(text)):
        char = text[index]
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise SystemExit(f'closing brace not found: {signature}')
    return text[:start] + replacement.rstrip() + text[end:]


SUPPLY_HELPER = r'''static void a52_ufs_trace_supply(struct device *dev, const char *name)
{
	struct device_node *supplier;
	struct device_node *walk;
	struct platform_device *pdev = NULL;
	const char *compatible = "<none>";
	const char *status = "<okay-default>";
	const char *regulator_name = "<none>";
	const char *provider_node = "<none>";
	const char *provider_dev = "<none>";
	const char *provider_driver = "<unbound>";
	char property[40];
	u32 phandle = 0;
	int len;

	if (!dev || !dev->of_node || !name)
		return;

	len = snprintf(property, sizeof(property), "%s-supply", name);
	if (len <= 0 || len >= (int)sizeof(property))
		return;

	of_property_read_u32(dev->of_node, property, &phandle);
	supplier = of_parse_phandle(dev->of_node, property, 0);
	if (!supplier) {
		a52_persistent_diag_mark("A52UFSDEP copy=1 SUPPLY name=%s prop=%s phandle=0x%x node=<missing>\n",
					 name, property, phandle);
		a52_persistent_diag_mark("A52UFSDEP copy=2 SUPPLY name=%s prop=%s phandle=0x%x node=<missing>\n",
					 name, property, phandle);
		a52_persistent_diag_mark("A52UFSDEP copy=3 SUPPLY name=%s prop=%s phandle=0x%x node=<missing>\n",
					 name, property, phandle);
		return;
	}

	of_property_read_string(supplier, "compatible", &compatible);
	of_property_read_string(supplier, "status", &status);
	of_property_read_string(supplier, "regulator-name", &regulator_name);

	for (walk = supplier; walk; walk = walk->parent) {
		pdev = of_find_device_by_node(walk);
		if (pdev) {
			provider_node = walk->full_name;
			provider_dev = dev_name(&pdev->dev);
			if (pdev->dev.driver)
				provider_driver = pdev->dev.driver->name;
			break;
		}
	}

	a52_persistent_diag_mark("A52UFSDEP copy=1 SUPPLY name=%s phandle=0x%x node=%s compat=%s status=%s regulator=%s avail=%d\n",
				 name, phandle, supplier->full_name, compatible, status,
				 regulator_name, of_device_is_available(supplier));
	a52_persistent_diag_mark("A52UFSDEP copy=2 SUPPLY name=%s phandle=0x%x node=%s compat=%s status=%s regulator=%s avail=%d\n",
				 name, phandle, supplier->full_name, compatible, status,
				 regulator_name, of_device_is_available(supplier));
	a52_persistent_diag_mark("A52UFSDEP copy=3 SUPPLY name=%s phandle=0x%x node=%s compat=%s status=%s regulator=%s avail=%d\n",
				 name, phandle, supplier->full_name, compatible, status,
				 regulator_name, of_device_is_available(supplier));
	a52_persistent_diag_mark("A52UFSDEP copy=1 PROVIDER name=%s node=%s dev=%s driver=%s\n",
				 name, provider_node, provider_dev, provider_driver);
	a52_persistent_diag_mark("A52UFSDEP copy=2 PROVIDER name=%s node=%s dev=%s driver=%s\n",
				 name, provider_node, provider_dev, provider_driver);
	a52_persistent_diag_mark("A52UFSDEP copy=3 PROVIDER name=%s node=%s dev=%s driver=%s\n",
				 name, provider_node, provider_dev, provider_driver);

	if (pdev)
		put_device(&pdev->dev);
	of_node_put(supplier);
}

'''

GET_VREG = r'''static int ufshcd_get_vreg(struct device *dev, struct ufs_vreg *vreg)
{
	int ret = 0;

	if (!vreg)
		goto out;

	a52_ufs_trace_supply(dev, vreg->name);
	a52_persistent_diag_mark("A52UFSDEP copy=1 VREG_GET_BEGIN name=%s\n", vreg->name);
	a52_persistent_diag_mark("A52UFSDEP copy=2 VREG_GET_BEGIN name=%s\n", vreg->name);
	a52_persistent_diag_mark("A52UFSDEP copy=3 VREG_GET_BEGIN name=%s\n", vreg->name);
	vreg->reg = devm_regulator_get(dev, vreg->name);
	if (IS_ERR(vreg->reg)) {
		ret = PTR_ERR(vreg->reg);
		dev_err(dev, "%s: %s get failed, err=%d\n",
				__func__, vreg->name, ret);
	}
	a52_persistent_diag_mark("A52UFSDEP copy=1 VREG_GET_END name=%s ret=%d reg=%p\n",
				 vreg->name, ret, vreg->reg);
	a52_persistent_diag_mark("A52UFSDEP copy=2 VREG_GET_END name=%s ret=%d reg=%p\n",
				 vreg->name, ret, vreg->reg);
	a52_persistent_diag_mark("A52UFSDEP copy=3 VREG_GET_END name=%s ret=%d reg=%p\n",
				 vreg->name, ret, vreg->reg);
out:
	return ret;
}'''

SETUP_VREG = r'''static int ufshcd_setup_vreg(struct ufs_hba *hba, bool on)
{
	int ret = 0;
	struct device *dev = hba->dev;
	struct ufs_vreg_info *info = &hba->vreg_info;

	ret = ufshcd_toggle_vreg(dev, info->vcc, on);
	a52_persistent_diag_mark("A52UFSDEP copy=1 VREG_TOGGLE name=vcc on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=2 VREG_TOGGLE name=vcc on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=3 VREG_TOGGLE name=vcc on=%d ret=%d\n", on, ret);
	if (ret)
		goto out;

	ret = ufshcd_toggle_vreg(dev, info->vccq, on);
	a52_persistent_diag_mark("A52UFSDEP copy=1 VREG_TOGGLE name=vccq on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=2 VREG_TOGGLE name=vccq on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=3 VREG_TOGGLE name=vccq on=%d ret=%d\n", on, ret);
	if (ret)
		goto out;

	ret = ufshcd_toggle_vreg(dev, info->vccq2, on);
	a52_persistent_diag_mark("A52UFSDEP copy=1 VREG_TOGGLE name=vccq2 on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=2 VREG_TOGGLE name=vccq2 on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=3 VREG_TOGGLE name=vccq2 on=%d ret=%d\n", on, ret);

out:
	if (ret) {
		ufshcd_toggle_vreg(dev, info->vccq2, false);
		ufshcd_toggle_vreg(dev, info->vccq, false);
		ufshcd_toggle_vreg(dev, info->vcc, false);
	}
	return ret;
}'''

SETUP_HBA_VREG = r'''static int ufshcd_setup_hba_vreg(struct ufs_hba *hba, bool on)
{
	struct ufs_vreg_info *info = &hba->vreg_info;
	int ret;

	ret = ufshcd_toggle_vreg(hba->dev, info->vdd_hba, on);
	a52_persistent_diag_mark("A52UFSDEP copy=1 VREG_TOGGLE name=vdd-hba on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=2 VREG_TOGGLE name=vdd-hba on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=3 VREG_TOGGLE name=vdd-hba on=%d ret=%d\n", on, ret);
	return ret;
}'''

SETUP_CLOCKS = r'''static int ufshcd_setup_clocks(struct ufs_hba *hba, bool on)
{
	int ret = 0;
	struct ufs_clk_info *clki;
	struct list_head *head = &hba->clk_list_head;
	unsigned long flags;
	ktime_t start = ktime_get();
	bool clk_state_changed = false;

	if (list_empty(head))
		goto out;

	ret = ufshcd_vops_setup_clocks(hba, on, PRE_CHANGE);
	a52_persistent_diag_mark("A52UFSDEP copy=1 CLK_VOPS phase=pre on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=2 CLK_VOPS phase=pre on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=3 CLK_VOPS phase=pre on=%d ret=%d\n", on, ret);
	if (ret)
		return ret;

	list_for_each_entry(clki, head, list) {
		if (!IS_ERR_OR_NULL(clki->clk)) {
			if (ufshcd_is_link_active(hba) &&
			    clki->keep_link_active)
				continue;

			clk_state_changed = on ^ clki->enabled;
			if (on && !clki->enabled) {
				ret = clk_prepare_enable(clki->clk);
				if (ret)
					dev_err(hba->dev, "%s: %s prepare enable failed, %d\n",
						__func__, clki->name, ret);
			} else if (!on && clki->enabled) {
				clk_disable_unprepare(clki->clk);
			}
			a52_persistent_diag_mark("A52UFSDEP copy=1 CLK_TOGGLE name=%s on=%d ret=%d\n",
					 clki->name, on, ret);
			a52_persistent_diag_mark("A52UFSDEP copy=2 CLK_TOGGLE name=%s on=%d ret=%d\n",
					 clki->name, on, ret);
			a52_persistent_diag_mark("A52UFSDEP copy=3 CLK_TOGGLE name=%s on=%d ret=%d\n",
					 clki->name, on, ret);
			if (ret)
				goto out;
			clki->enabled = on;
			dev_dbg(hba->dev, "%s: clk: %s %sabled\n", __func__,
					clki->name, on ? "en" : "dis");
		}
	}

	ret = ufshcd_vops_setup_clocks(hba, on, POST_CHANGE);
	a52_persistent_diag_mark("A52UFSDEP copy=1 CLK_VOPS phase=post on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=2 CLK_VOPS phase=post on=%d ret=%d\n", on, ret);
	a52_persistent_diag_mark("A52UFSDEP copy=3 CLK_VOPS phase=post on=%d ret=%d\n", on, ret);
	if (ret)
		return ret;

out:
	if (ret) {
		list_for_each_entry(clki, head, list) {
			if (!IS_ERR_OR_NULL(clki->clk) && clki->enabled)
				clk_disable_unprepare(clki->clk);
		}
	} else if (!ret && on) {
		spin_lock_irqsave(hba->host->host_lock, flags);
		hba->clk_gating.state = CLKS_ON;
		trace_ufshcd_clk_gating(dev_name(hba->dev),
				hba->clk_gating.state);
		spin_unlock_irqrestore(hba->host->host_lock, flags);
	}

	if (clk_state_changed)
		trace_ufshcd_profile_clk_gating(dev_name(hba->dev),
			(on ? "on" : "off"),
			ktime_to_us(ktime_sub(ktime_get(), start)), ret);
	return ret;
}'''

INIT_CLOCKS = r'''static int ufshcd_init_clocks(struct ufs_hba *hba)
{
	int ret = 0;
	struct ufs_clk_info *clki;
	struct device *dev = hba->dev;
	struct list_head *head = &hba->clk_list_head;

	if (list_empty(head))
		goto out;

	list_for_each_entry(clki, head, list) {
		if (!clki->name)
			continue;

		a52_persistent_diag_mark("A52UFSDEP copy=1 CLK_GET_BEGIN name=%s\n", clki->name);
		a52_persistent_diag_mark("A52UFSDEP copy=2 CLK_GET_BEGIN name=%s\n", clki->name);
		a52_persistent_diag_mark("A52UFSDEP copy=3 CLK_GET_BEGIN name=%s\n", clki->name);
		clki->clk = devm_clk_get(dev, clki->name);
		if (IS_ERR(clki->clk)) {
			ret = PTR_ERR(clki->clk);
			dev_err(dev, "%s: %s clk get failed, %d\n",
					__func__, clki->name, ret);
		}
		a52_persistent_diag_mark("A52UFSDEP copy=1 CLK_GET_END name=%s ret=%d clk=%p\n",
					 clki->name, ret, clki->clk);
		a52_persistent_diag_mark("A52UFSDEP copy=2 CLK_GET_END name=%s ret=%d clk=%p\n",
					 clki->name, ret, clki->clk);
		a52_persistent_diag_mark("A52UFSDEP copy=3 CLK_GET_END name=%s ret=%d clk=%p\n",
					 clki->name, ret, clki->clk);
		if (ret)
			goto out;

		if (!strcmp(clki->name, "ref_clk"))
			ufshcd_parse_dev_ref_clk_freq(hba, clki->clk);

		if (clki->max_freq) {
			ret = clk_set_rate(clki->clk, clki->max_freq);
			a52_persistent_diag_mark("A52UFSDEP copy=1 CLK_RATE name=%s hz=%d ret=%d\n",
						 clki->name, clki->max_freq, ret);
			a52_persistent_diag_mark("A52UFSDEP copy=2 CLK_RATE name=%s hz=%d ret=%d\n",
						 clki->name, clki->max_freq, ret);
			a52_persistent_diag_mark("A52UFSDEP copy=3 CLK_RATE name=%s hz=%d ret=%d\n",
						 clki->name, clki->max_freq, ret);
			if (ret) {
				dev_err(hba->dev, "%s: %s clk set rate(%dHz) failed, %d\n",
						__func__, clki->name,
						clki->max_freq, ret);
				goto out;
			}
			clki->curr_freq = clki->max_freq;
		}
		dev_dbg(dev, "%s: clk: %s, rate: %lu\n", __func__,
				clki->name, clk_get_rate(clki->clk));
	}
out:
	return ret;
}'''

HBA_INIT = r'''static int ufshcd_hba_init(struct ufs_hba *hba)
{
	int err;

	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_BEGIN dev=%s\n", dev_name(hba->dev));
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_BEGIN dev=%s\n", dev_name(hba->dev));
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_BEGIN dev=%s\n", dev_name(hba->dev));

	err = ufshcd_init_hba_vreg(hba);
	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_STAGE name=init_hba_vreg ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_STAGE name=init_hba_vreg ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_STAGE name=init_hba_vreg ret=%d\n", err);
	if (err)
		goto out;

	err = ufshcd_setup_hba_vreg(hba, true);
	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_STAGE name=setup_hba_vreg ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_STAGE name=setup_hba_vreg ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_STAGE name=setup_hba_vreg ret=%d\n", err);
	if (err)
		goto out;

	err = ufshcd_init_clocks(hba);
	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_STAGE name=init_clocks ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_STAGE name=init_clocks ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_STAGE name=init_clocks ret=%d\n", err);
	if (err)
		goto out_disable_hba_vreg;

	err = ufshcd_setup_clocks(hba, true);
	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_STAGE name=setup_clocks ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_STAGE name=setup_clocks ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_STAGE name=setup_clocks ret=%d\n", err);
	if (err)
		goto out_disable_hba_vreg;

	err = ufshcd_init_vreg(hba);
	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_STAGE name=init_device_vreg ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_STAGE name=init_device_vreg ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_STAGE name=init_device_vreg ret=%d\n", err);
	if (err)
		goto out_disable_clks;

	err = ufshcd_setup_vreg(hba, true);
	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_STAGE name=setup_device_vreg ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_STAGE name=setup_device_vreg ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_STAGE name=setup_device_vreg ret=%d\n", err);
	if (err)
		goto out_disable_clks;

	err = ufshcd_variant_hba_init(hba);
	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_STAGE name=variant_hba_init ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_STAGE name=variant_hba_init ret=%d\n", err);
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_STAGE name=variant_hba_init ret=%d\n", err);
	if (err)
		goto out_disable_vreg;

	ufs_debugfs_hba_init(hba);
	hba->is_powered = true;
	goto out;

out_disable_vreg:
	ufshcd_setup_vreg(hba, false);
out_disable_clks:
	ufshcd_setup_clocks(hba, false);
out_disable_hba_vreg:
	ufshcd_setup_hba_vreg(hba, false);
out:
	a52_persistent_diag_mark("A52UFSDEP copy=1 HBA_END ret=%d powered=%d\n", err, hba->is_powered);
	a52_persistent_diag_mark("A52UFSDEP copy=2 HBA_END ret=%d powered=%d\n", err, hba->is_powered);
	a52_persistent_diag_mark("A52UFSDEP copy=3 HBA_END ret=%d powered=%d\n", err, hba->is_powered);
	return err;
}'''


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Instrument the complete A52 UFS power/clock dependency chain.'
    )
    parser.add_argument('--gki', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = gki / 'drivers/scsi/ufs/ufshcd.c'
    if not path.is_file():
        raise SystemExit(f'missing source: {path}')

    text = path.read_text(encoding='utf-8')
    if 'A52UFSDEP copy=1 HBA_STAGE' in text:
        raise SystemExit('Workflow 98 dependency audit is already present')
    if 'extern void a52_persistent_diag_mark' not in text:
        raise SystemExit('Workflow 97 persistent marker declaration is missing')

    if '#include <linux/of_platform.h>' not in text:
        text = replace_once(
            text,
            '#include <linux/of.h>\n',
            '#include <linux/of.h>\n#include <linux/of_platform.h>\n',
            'of_platform include',
        )

    text = replace_function(text, 'static int ufshcd_setup_vreg(', SETUP_VREG)
    text = replace_function(text, 'static int ufshcd_setup_hba_vreg(', SETUP_HBA_VREG)
    text = text.replace(
        'static int ufshcd_get_vreg(struct device *dev, struct ufs_vreg *vreg)',
        SUPPLY_HELPER + 'static int ufshcd_get_vreg(struct device *dev, struct ufs_vreg *vreg)',
        1,
    )
    text = replace_function(text, 'static int ufshcd_get_vreg(', GET_VREG)
    text = replace_function(text, 'static int ufshcd_setup_clocks(', SETUP_CLOCKS)
    text = replace_function(text, 'static int ufshcd_init_clocks(', INIT_CLOCKS)
    text = replace_function(text, 'static int ufshcd_hba_init(', HBA_INIT)

    required = {
        'supply_resolver': 'A52UFSDEP copy=1 SUPPLY' in text,
        'provider_ancestry': 'A52UFSDEP copy=1 PROVIDER' in text,
        'vreg_name_return': 'A52UFSDEP copy=1 VREG_GET_END' in text,
        'per_rail_enable': 'A52UFSDEP copy=1 VREG_TOGGLE name=vccq2' in text,
        'per_clock_get': 'A52UFSDEP copy=1 CLK_GET_END' in text,
        'per_clock_enable': 'A52UFSDEP copy=1 CLK_TOGGLE' in text,
        'hba_stage_init_hba': 'name=init_hba_vreg' in text,
        'hba_stage_device_rails': 'name=init_device_vreg' in text,
        'hba_stage_variant': 'name=variant_hba_init' in text,
        'ice_safe_preserved': 'A52_UFS_ICE_SAFE_BRINGUP' in (
            gki / 'drivers/scsi/ufs/ufs-qcom-ice.c'
        ).read_text(encoding='utf-8'),
    }
    failed = [name for name, passed in required.items() if not passed]
    if failed:
        raise SystemExit('dependency audit staging failed: ' + ', '.join(failed))

    path.write_text(text, encoding='utf-8')
    (output / 'patched-ufshcd.c').write_text(text, encoding='utf-8')
    (output / 'stage-report.json').write_text(
        json.dumps(
            {
                'status': 'staged',
                'hardware_validated': False,
                'purpose': (
                    'Identify every pre-variant UFS dependency in one boot without '
                    'bypassing any additional power rail'
                ),
                'instrumented_sequence': [
                    'init_hba_vreg',
                    'setup_hba_vreg',
                    'init_clocks',
                    'setup_clocks',
                    'init_device_vreg',
                    'setup_device_vreg',
                    'variant_hba_init',
                ],
                'supply_properties': [
                    'vdd-hba-supply',
                    'vcc-supply',
                    'vccq-supply',
                    'vccq2-supply',
                ],
                'checks': required,
            },
            indent=2,
            sort_keys=True,
        )
        + '\n',
        encoding='utf-8',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
