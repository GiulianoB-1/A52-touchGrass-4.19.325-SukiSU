#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from pathlib import Path

RUN37_STAGE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "febdd4fad0f2704b0498a76569031e48d8ee8b4a/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_run37_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    provider = scripts_dir / PROVIDER_SCRIPT
    if not provider.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {provider}")

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing
    )

    with tempfile.TemporaryDirectory(prefix="a52-stage94b-run37-") as tmp:
        tmpdir = Path(tmp)
        previous = tmpdir / "stage94b-run37.py"
        request = urllib.request.Request(
            RUN37_STAGE_URL, headers={"User-Agent": "a52-stage94b-run40-pinctrl-wrapper"}
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            previous.write_bytes(response.read())
        shutil.copy2(provider, tmpdir / PROVIDER_SCRIPT)
        subprocess.run(
            [sys.executable, str(previous), *sys.argv[1:]],
            check=True,
            env=env,
        )


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def triplet(prefix: str, fmt: str, args: str, indent: str = "\t") -> str:
    return "".join(
        f'{indent}a52_persistent_diag_mark("{prefix} copy={copy} {fmt}\\n", {args});\n'
        for copy in (1, 2, 3)
    )


def patch_driver_core(gki: Path, output: Path) -> dict:
    dd_path = gki / "drivers/base/dd.c"
    if not dd_path.is_file():
        raise SystemExit("drivers/base/dd.c is missing")
    dd = dd_path.read_text(encoding="utf-8")

    if "A52_UFS_PINCTRL_DEFER_BYPASS" in dd:
        raise SystemExit("Run 40 driver-core patch already present")
    if "A52_UFS_FW_DEVLINK_FORCE_PROBE" not in dd:
        raise SystemExit("Run 37 safe fw_devlink bridge is missing")

    include_anchor = "#include <linux/module.h>\n"
    if "#include <linux/of.h>\n" not in dd:
        dd = replace_once(
            dd,
            include_anchor,
            include_anchor + "#include <linux/of.h>\n",
            "include OF helpers for Run 40",
        )

    old_selector = r'''static bool a52_legacy_ufs_fw_devlink_consumer(const struct device *dev)
{
	const char *name;

	if (!dev)
		return false;
	name = dev_name(dev);
	return name && !strcmp(name, "1d84000.ufshc");
}

'''
    new_selector = r'''static bool a52_legacy_fw_devlink_consumer(const struct device *dev)
{
	const char *name;

	if (!dev)
		return false;
	name = dev_name(dev);
	return name && (!strcmp(name, "1d84000.ufshc") ||
			!strcmp(name, "f100000.pinctrl"));
}

static bool a52_legacy_ufs_named_reset_pinctrl(const struct device *dev)
{
	if (!dev || !dev->of_node || strcmp(dev_name(dev), "1d84000.ufshc"))
		return false;

	return of_property_match_string(dev->of_node, "pinctrl-names",
					"dev-reset-assert") >= 0 &&
	       of_property_match_string(dev->of_node, "pinctrl-names",
					"dev-reset-deassert") >= 0;
}

static bool a52_run40_preprobe_target(const struct device *dev)
{
	const char *name = dev ? dev_name(dev) : NULL;

	return name && (!strcmp(name, "1d84000.ufshc") ||
			!strcmp(name, "f100000.pinctrl"));
}

'''
    dd = replace_once(dd, old_selector, new_selector, "extend exact Run 40 selectors")

    dd = dd.replace(
        "a52_legacy_ufs_fw_devlink_consumer(dev)",
        "a52_legacy_fw_devlink_consumer(dev)",
    )

    old_storage = '''\treturn name && (strstr(name, "ufs") || strstr(name, "scsi") ||
\t\t\tstrstr(name, "sdhci") || strstr(name, "1d84000") ||
\t\t\tstrstr(name, "1d87000"));
'''
    new_storage = '''\treturn name && (strstr(name, "ufs") || strstr(name, "scsi") ||
\t\t\tstrstr(name, "sdhci") || strstr(name, "1d84000") ||
\t\t\tstrstr(name, "1d87000") || strstr(name, "f100000"));
'''
    dd = replace_once(dd, old_storage, new_storage, "trace Lagoon TLMM platform probe")

    old_pinctrl = '''\t/* If using pinctrl, bind pins now before probing */
\tret = pinctrl_bind_pins(dev);
\tif (ret)
\t\tgoto pinctrl_bind_failed;
'''
    new_pinctrl = '''\t/* If using pinctrl, bind pins now before probing */
\tret = pinctrl_bind_pins(dev);
\tif (a52_run40_preprobe_target(dev)) {
'''
    new_pinctrl += triplet(
        "A52_PREPROBE",
        "stage=pinctrl dev=%s driver=%s ret=%d pins=%s",
        'dev_name(dev), drv->name, ret, dev->pins ? "present" : "none"',
        "\t\t",
    )
    new_pinctrl += '''\t}
\tif (ret == -EPROBE_DEFER && a52_legacy_ufs_named_reset_pinctrl(dev)) {
'''
    new_pinctrl += triplet(
        "A52_UFS_PINCTRL_DEFER_BYPASS",
        "dev=%s driver=%s ret=%d contract=named-reset",
        "dev_name(dev), drv->name, ret",
        "\t\t",
    )
    new_pinctrl += '''\t\t/*
\t\t * The Samsung DT has no generic default/init state. The UFS variant
\t\t * driver owns the two explicit reset states and selects them during
\t\t * device reset. pinctrl_bind_pins() has already cleaned dev->pins on
\t\t * this deferral, so continue without retaining a partial handle.
\t\t */
\t\tret = 0;
\t}
\tif (ret)
\t\tgoto pinctrl_bind_failed;
'''
    dd = replace_once(dd, old_pinctrl, new_pinctrl, "instrument and bridge UFS generic pinctrl defer")

    old_dma = '''\tif (dev->bus->dma_configure) {
\t\tret = dev->bus->dma_configure(dev);
\t\tif (ret)
\t\t\tgoto probe_failed;
\t}
'''
    new_dma = '''\tif (dev->bus->dma_configure) {
\t\tret = dev->bus->dma_configure(dev);
\t\tif (a52_run40_preprobe_target(dev)) {
'''
    new_dma += triplet(
        "A52_PREPROBE", "stage=dma dev=%s driver=%s ret=%d",
        "dev_name(dev), drv->name, ret", "\t\t\t"
    )
    new_dma += '''\t\t}
\t\tif (ret)
\t\t\tgoto probe_failed;
\t} else if (a52_run40_preprobe_target(dev)) {
'''
    new_dma += triplet(
        "A52_PREPROBE", "stage=dma dev=%s driver=%s ret=0 hook=none",
        "dev_name(dev), drv->name", "\t\t"
    )
    new_dma += "\t}\n"
    dd = replace_once(dd, old_dma, new_dma, "instrument DMA setup")

    old_sysfs = '''\tret = driver_sysfs_add(dev);
\tif (ret) {
'''
    new_sysfs = '''\tret = driver_sysfs_add(dev);
\tif (a52_run40_preprobe_target(dev)) {
'''
    new_sysfs += triplet(
        "A52_PREPROBE", "stage=sysfs dev=%s driver=%s ret=%d",
        "dev_name(dev), drv->name, ret", "\t\t"
    )
    new_sysfs += '''\t}
\tif (ret) {
'''
    dd = replace_once(dd, old_sysfs, new_sysfs, "instrument driver sysfs setup")

    old_pm = '''\tif (dev->pm_domain && dev->pm_domain->activate) {
\t\tret = dev->pm_domain->activate(dev);
\t\tif (ret)
\t\t\tgoto probe_failed;
\t}
'''
    new_pm = '''\tif (dev->pm_domain && dev->pm_domain->activate) {
\t\tret = dev->pm_domain->activate(dev);
\t\tif (a52_run40_preprobe_target(dev)) {
'''
    new_pm += triplet(
        "A52_PREPROBE", "stage=pm_activate dev=%s driver=%s ret=%d",
        "dev_name(dev), drv->name, ret", "\t\t\t"
    )
    new_pm += '''\t\t}
\t\tif (ret)
\t\t\tgoto probe_failed;
\t} else if (a52_run40_preprobe_target(dev)) {
'''
    new_pm += triplet(
        "A52_PREPROBE", "stage=pm_activate dev=%s driver=%s ret=0 hook=none",
        "dev_name(dev), drv->name", "\t\t"
    )
    new_pm += "\t}\n"
    dd = replace_once(dd, old_pm, new_pm, "instrument PM-domain activation")

    old_probe = '''\tif (dev->bus->probe) {
\t\tret = dev->bus->probe(dev);
\t\tif (ret)
\t\t\tgoto probe_failed;
\t} else if (drv->probe) {
\t\tret = drv->probe(dev);
\t\tif (ret)
\t\t\tgoto probe_failed;
\t}
'''
    new_probe = '''\tif (dev->bus->probe) {
\t\tif (a52_run40_preprobe_target(dev)) {
'''
    new_probe += triplet(
        "A52_PREPROBE", "stage=bus_probe_begin dev=%s driver=%s",
        "dev_name(dev), drv->name", "\t\t\t"
    )
    new_probe += '''\t\t}
\t\tret = dev->bus->probe(dev);
\t\tif (a52_run40_preprobe_target(dev)) {
'''
    new_probe += triplet(
        "A52_PREPROBE", "stage=bus_probe_end dev=%s driver=%s ret=%d",
        "dev_name(dev), drv->name, ret", "\t\t\t"
    )
    new_probe += '''\t\t}
\t\tif (ret)
\t\t\tgoto probe_failed;
\t} else if (drv->probe) {
\t\tif (a52_run40_preprobe_target(dev)) {
'''
    new_probe += triplet(
        "A52_PREPROBE", "stage=driver_probe_begin dev=%s driver=%s",
        "dev_name(dev), drv->name", "\t\t\t"
    )
    new_probe += '''\t\t}
\t\tret = drv->probe(dev);
\t\tif (a52_run40_preprobe_target(dev)) {
'''
    new_probe += triplet(
        "A52_PREPROBE", "stage=driver_probe_end dev=%s driver=%s ret=%d",
        "dev_name(dev), drv->name, ret", "\t\t\t"
    )
    new_probe += '''\t\t}
\t\tif (ret)
\t\t\tgoto probe_failed;
\t}
'''
    dd = replace_once(dd, old_probe, new_probe, "instrument raw platform probe return")

    checks = {
        "exact_tlmm_and_ufs_fwdevlink_scope": (
            'strcmp(name, "1d84000.ufshc")' in dd and
            'strcmp(name, "f100000.pinctrl")' in dd
        ),
        "named_reset_contract_required": (
            '"dev-reset-assert"' in dd and '"dev-reset-deassert"' in dd
        ),
        "generic_pinctrl_only_bypass": (
            "ret == -EPROBE_DEFER && a52_legacy_ufs_named_reset_pinctrl(dev)" in dd
        ),
        "dma_not_bypassed": "ret = dev->bus->dma_configure(dev);" in dd,
        "pm_not_bypassed": "ret = dev->pm_domain->activate(dev);" in dd,
        "raw_probe_return_recorded": "stage=bus_probe_end" in dd,
        "three_pinctrl_bypass_markers": all(
            f"A52_UFS_PINCTRL_DEFER_BYPASS copy={copy}" in dd for copy in (1, 2, 3)
        ),
        "pinctrl_provider_traced": "f100000" in dd and "pinctrl" in dd,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("Run 40 driver-core audit failed: " + ", ".join(failed))

    dd_path.write_text(dd, encoding="utf-8")
    (output / "patched-drivers-base-dd-run40.c").write_text(dd, encoding="utf-8")
    return {"status": "patched", "checks": checks}


def patch_ufs_reset_diagnostics(gki: Path, output: Path) -> dict:
    qcom_path = gki / "drivers/scsi/ufs/ufs-qcom.c"
    if not qcom_path.is_file():
        raise SystemExit("drivers/scsi/ufs/ufs-qcom.c is missing")
    qcom = qcom_path.read_text(encoding="utf-8")
    if "DEVICE_RESET stage=pinctrl_get" in qcom:
        raise SystemExit("Run 40 UFS reset diagnostics already present")

    old_get = '''\tpinctrl = pinctrl_get(hba->dev);
\tif (IS_ERR(pinctrl))
\t\treturn PTR_ERR(pinctrl);
'''
    new_get = '''\tpinctrl = pinctrl_get(hba->dev);
\tif (IS_ERR(pinctrl)) {
\t\tret = PTR_ERR(pinctrl);
'''
    new_get += triplet(
        "A52UFS", "DEVICE_RESET stage=pinctrl_get ret=%d", "ret", "\t\t"
    )
    new_get += '''\t\treturn ret;
\t}
'''
    qcom = replace_once(qcom, old_get, new_get, "record UFS pinctrl_get failure")

    old_assert = '''\tassert_state = pinctrl_lookup_state(pinctrl, "dev-reset-assert");
\tif (IS_ERR(assert_state)) {
\t\tret = PTR_ERR(assert_state);
\t\tgoto out_put;
\t}
'''
    new_assert = '''\tassert_state = pinctrl_lookup_state(pinctrl, "dev-reset-assert");
\tif (IS_ERR(assert_state)) {
\t\tret = PTR_ERR(assert_state);
'''
    new_assert += triplet(
        "A52UFS", "DEVICE_RESET stage=lookup_assert ret=%d", "ret", "\t\t"
    )
    new_assert += '''\t\tgoto out_put;
\t}
'''
    qcom = replace_once(qcom, old_assert, new_assert, "record assert-state lookup failure")

    old_deassert = '''\tdeassert_state = pinctrl_lookup_state(pinctrl, "dev-reset-deassert");
\tif (IS_ERR(deassert_state)) {
\t\tret = PTR_ERR(deassert_state);
\t\tgoto out_put;
\t}
'''
    new_deassert = '''\tdeassert_state = pinctrl_lookup_state(pinctrl, "dev-reset-deassert");
\tif (IS_ERR(deassert_state)) {
\t\tret = PTR_ERR(deassert_state);
'''
    new_deassert += triplet(
        "A52UFS", "DEVICE_RESET stage=lookup_deassert ret=%d", "ret", "\t\t"
    )
    new_deassert += '''\t\tgoto out_put;
\t}
'''
    qcom = replace_once(qcom, old_deassert, new_deassert, "record deassert-state lookup failure")

    old_select = '''\tret = pinctrl_select_state(pinctrl, assert_state);
\tif (ret)
\t\tgoto out_put;
\tusleep_range(10, 15);
\tret = pinctrl_select_state(pinctrl, deassert_state);
\tif (ret)
\t\tgoto out_put;
'''
    new_select = '''\tret = pinctrl_select_state(pinctrl, assert_state);
\tif (ret) {
'''
    new_select += triplet(
        "A52UFS", "DEVICE_RESET stage=select_assert ret=%d", "ret", "\t\t"
    )
    new_select += '''\t\tgoto out_put;
\t}
\tusleep_range(10, 15);
\tret = pinctrl_select_state(pinctrl, deassert_state);
\tif (ret) {
'''
    new_select += triplet(
        "A52UFS", "DEVICE_RESET stage=select_deassert ret=%d", "ret", "\t\t"
    )
    new_select += '''\t\tgoto out_put;
\t}
'''
    qcom = replace_once(qcom, old_select, new_select, "record reset-state selection failure")

    checks = {
        "pinctrl_get_failure_recorded": "DEVICE_RESET stage=pinctrl_get" in qcom,
        "assert_lookup_failure_recorded": "DEVICE_RESET stage=lookup_assert" in qcom,
        "deassert_lookup_failure_recorded": "DEVICE_RESET stage=lookup_deassert" in qcom,
        "assert_select_failure_recorded": "DEVICE_RESET stage=select_assert" in qcom,
        "deassert_select_failure_recorded": "DEVICE_RESET stage=select_deassert" in qcom,
        "success_marker_retained": "DEVICE_RESET path=pinctrl ret=0" in qcom,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("Run 40 UFS reset audit failed: " + ", ".join(failed))

    qcom_path.write_text(qcom, encoding="utf-8")
    (output / "patched-ufs-qcom-run40.c").write_text(qcom, encoding="utf-8")
    return {"status": "patched", "checks": checks}


def patch_printk_filter(gki: Path, output: Path) -> dict:
    printk_path = gki / "kernel/printk/printk.c"
    if not printk_path.is_file():
        raise SystemExit("kernel/printk/printk.c is missing")
    printk = printk_path.read_text(encoding="utf-8")

    old = '''\t       strstr(line, "qmp") || strstr(line, "phy") ||
\t       strstr(line, "PHY") || strstr(line, "regulator") ||
'''
    new = '''\t       strstr(line, "qmp") || strstr(line, "phy") ||
\t       strstr(line, "PHY") || strstr(line, "pinctrl") ||
\t       strstr(line, "f100000") || strstr(line, "lagoon-pinctrl") ||
\t       strstr(line, "regulator") ||
'''
    printk = replace_once(printk, old, new, "mirror Lagoon pinctrl printk messages")

    checks = {
        "pinctrl_messages_mirrored": 'strstr(line, "pinctrl")' in printk,
        "tlmm_address_mirrored": 'strstr(line, "f100000")' in printk,
        "lagoon_driver_mirrored": 'strstr(line, "lagoon-pinctrl")' in printk,
    }
    printk_path.write_text(printk, encoding="utf-8")
    (output / "patched-printk-run40.c").write_text(printk, encoding="utf-8")
    return {"status": "patched", "checks": checks}


def merge_report(output: Path, sections: dict) -> None:
    path = output / "stage-report.json"
    if not path.is_file():
        raise SystemExit("Run 37 stage report is missing")
    report = json.loads(path.read_text(encoding="utf-8"))
    report["run40_a52_ufs_pinctrl_preprobe_bridge"] = sections
    all_checks = {
        f"{section}.{name}": value
        for section, payload in sections.items()
        for name, value in payload.get("checks", {}).items()
    }
    passed = bool(all_checks and all(all_checks.values()))
    report.setdefault("checks", {})["run40_a52_ufs_pinctrl_preprobe_bridge"] = passed
    if not passed:
        failed = [name for name, value in all_checks.items() if not value]
        raise SystemExit("Run 40 merged audit failed: " + ", ".join(failed))
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_run37_stage()
    sections = {
        "driver_core": patch_driver_core(gki, output),
        "ufs_reset": patch_ufs_reset_diagnostics(gki, output),
        "printk_filter": patch_printk_filter(gki, output),
    }
    merge_report(output, sections)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-run40-pinctrl-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
