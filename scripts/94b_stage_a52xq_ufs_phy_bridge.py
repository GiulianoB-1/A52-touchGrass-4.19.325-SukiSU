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

RUN36_STAGE_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "674b9942580e8253ce4d111c4f7471186e7d1a38/"
    "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
)
PROVIDER_SCRIPT = "95_stage_a52xq_rpmh_provider_bridge.py"


def parse_paths() -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()
    return args.gki.resolve(), args.output.resolve()


def replay_run36_stage() -> None:
    scripts_dir = Path(__file__).resolve().parent
    provider = scripts_dir / PROVIDER_SCRIPT
    if not provider.is_file():
        raise SystemExit(f"RPMh provider staging script is missing: {provider}")

    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(scripts_dir) if not existing else str(scripts_dir) + os.pathsep + existing
    )

    with tempfile.TemporaryDirectory(prefix="a52-stage94b-run36-") as tmp:
        tmpdir = Path(tmp)
        previous = tmpdir / "stage94b-run36.py"
        request = urllib.request.Request(
            RUN36_STAGE_URL, headers={"User-Agent": "a52-stage94b-run37-wrapper"}
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


def patch_a52_ufs_fw_devlink_gate(gki: Path, output: Path) -> dict:
    path = gki / "drivers/base/dd.c"
    if not path.is_file():
        raise SystemExit("patched driver-core source is missing")

    text = path.read_text(encoding="utf-8")
    if "A52_UFS_FW_DEVLINK_BYPASS" in text:
        raise SystemExit("A52 UFS fw_devlink bridge already present")

    helper = r'''static bool a52_legacy_ufs_fw_devlink_consumer(const struct device *dev)
{
	const char *name;

	if (!dev)
		return false;
	name = dev_name(dev);
	return name && !strcmp(name, "1d84000.ufshc");
}

'''
    anchor = "static int really_probe(struct device *dev, struct device_driver *drv)\n"
    text = replace_once(
        text,
        anchor,
        helper + anchor,
        "insert A52 UFS fw_devlink helper",
    )

    old = '''\tret = device_links_check_suppliers(dev);
\tif (ret == -EPROBE_DEFER)
\t\tdriver_deferred_probe_add_trigger(dev, local_trigger_count);
\tif (ret)
\t\treturn ret;
'''
    new = '''\tret = device_links_check_suppliers(dev);
\tif (ret == -EPROBE_DEFER && a52_legacy_ufs_fw_devlink_consumer(dev)) {
\t\tconst char *reason = dev->p && dev->p->deferred_probe_reason ?
\t\t\tdev->p->deferred_probe_reason : "<none>";

\t\t/*
\t\t * Samsung's legacy A52 DT can leave inferred fw_devlink suppliers
\t\t * unresolved even after the real PHY provider binds. Strict fw_devlink
\t\t * then blocks the host before ufs_qcom_probe() can validate resources.
\t\t * Continue only for this exact host. pinctrl_bind_pins(), dma_configure(),
\t\t * and the UFS driver's clock/PHY/reset getters still enforce every real
\t\t * hardware dependency and can return -EPROBE_DEFER normally.
\t\t */
\t\ta52_persistent_diag_mark("A52_UFS_FW_DEVLINK_BYPASS copy=1 dev=%s driver=%s reason=%s\\n",
\t\t\tdev_name(dev), drv->name, reason);
\t\ta52_persistent_diag_mark("A52_UFS_FW_DEVLINK_BYPASS copy=2 dev=%s driver=%s reason=%s\\n",
\t\t\tdev_name(dev), drv->name, reason);
\t\ta52_persistent_diag_mark("A52_UFS_FW_DEVLINK_BYPASS copy=3 dev=%s driver=%s reason=%s\\n",
\t\t\tdev_name(dev), drv->name, reason);
\t\tret = 0;
\t}
\tif (ret == -EPROBE_DEFER)
\t\tdriver_deferred_probe_add_trigger(dev, local_trigger_count);
\tif (ret)
\t\treturn ret;
'''
    text = replace_once(text, old, new, "bypass stale A52 UFS inferred supplier gate")

    checks = {
        "exact_host_scope": 'strcmp(name, "1d84000.ufshc")' in text,
        "only_defer_bypassed": (
            "ret == -EPROBE_DEFER && a52_legacy_ufs_fw_devlink_consumer(dev)" in text
        ),
        "reason_recorded": "deferred_probe_reason" in new,
        "three_persistent_markers": all(
            f"A52_UFS_FW_DEVLINK_BYPASS copy={copy}" in text for copy in (1, 2, 3)
        ),
        "normal_defer_retained": (
            "if (ret == -EPROBE_DEFER)\n\t\tdriver_deferred_probe_add_trigger" in text
        ),
        "probe_call_marker_retained": "A52DEV copy=1 CALL" in text,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("A52 UFS fw_devlink bridge audit failed: " + ", ".join(failed))

    path.write_text(text, encoding="utf-8")
    (output / "patched-drivers-base-dd-run37.c").write_text(text, encoding="utf-8")
    return {
        "status": "bridged",
        "reason": (
            "Run 35 created and bound the flat QMP PHY, but repeated host retries "
            "still returned -EPROBE_DEFER before any A52UFS PROBE_BEGIN marker. "
            "Therefore device_links_check_suppliers(), not ufs_qcom_probe(), is the gate."
        ),
        "scope": "device 1d84000.ufshc only; all other consumers retain strict fw_devlink",
        "safety": (
            "pinctrl_bind_pins, DMA configuration, and all driver-level resource getters "
            "remain active and may still defer the host"
        ),
        "checks": checks,
    }


def merge_report(output: Path, bridge: dict) -> None:
    path = output / "stage-report.json"
    if not path.is_file():
        raise SystemExit("Run 36 stage report is missing")
    report = json.loads(path.read_text(encoding="utf-8"))
    checks = bridge.get("checks", {})
    report["run37_a52_ufs_fw_devlink_bridge"] = bridge
    report.setdefault("checks", {})["run37_a52_ufs_fw_devlink_bridge"] = bool(
        checks and all(checks.values())
    )
    if not report["checks"]["run37_a52_ufs_fw_devlink_bridge"]:
        raise SystemExit("Run 37 merged audit failed")
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    gki, output = parse_paths()
    output.mkdir(parents=True, exist_ok=True)
    replay_run36_stage()
    bridge = patch_a52_ufs_fw_devlink_gate(gki, output)
    merge_report(output, bridge)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException:
        try:
            _, output = parse_paths()
            output.mkdir(parents=True, exist_ok=True)
            (output / "stage94b-run37-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
