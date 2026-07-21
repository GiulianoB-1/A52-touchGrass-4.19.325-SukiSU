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
            RUN36_STAGE_URL, headers={"User-Agent": "a52-stage94b-run37-safe-wrapper"}
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


def function_span(text: str, anchor: str, label: str) -> tuple[int, int]:
    start = text.find(anchor)
    if start < 0:
        raise SystemExit(f"{label}: function anchor missing")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"{label}: opening brace missing")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"{label}: closing brace missing")


def patch_a52_ufs_fw_devlink_gate(gki: Path, output: Path) -> dict:
    core_path = gki / "drivers/base/core.c"
    dd_path = gki / "drivers/base/dd.c"
    if not core_path.is_file() or not dd_path.is_file():
        raise SystemExit("driver-core sources are missing")

    core = core_path.read_text(encoding="utf-8")
    dd = dd_path.read_text(encoding="utf-8")
    if "A52_UFS_FW_DEVLINK_FORCE_PROBE" in dd or "a52_device_links_force_probe" in core:
        raise SystemExit("safe A52 UFS fw_devlink bridge already present")

    required_core_tokens = (
        "static void fwnode_links_purge_suppliers",
        "static void device_link_drop_managed",
        "device_links_write_lock();",
        "DL_FLAG_MANAGED",
        "DL_STATE_AVAILABLE",
        "DL_STATE_CONSUMER_PROBE",
        "DL_DEV_PROBING",
    )
    missing = [token for token in required_core_tokens if token not in core]
    if missing:
        raise SystemExit("driver-core force-probe prerequisites missing: " + ", ".join(missing))

    _, drop_end = function_span(
        core, "static void device_link_drop_managed", "managed device-link drop helper"
    )
    helper = r'''

/*
 * Samsung's A52 downstream DT can retain inferred fw_devlink suppliers that
 * never become real devices. Prepare only the explicitly selected legacy UFS
 * consumer for a normal driver probe while keeping link state transitions
 * consistent with device_links_force_bind().
 */
void a52_device_links_force_probe(struct device *dev,
                                  unsigned int *kept,
                                  unsigned int *dropped);
void a52_device_links_force_probe(struct device *dev,
                                  unsigned int *kept,
                                  unsigned int *dropped)
{
	struct device_link *link, *ln;
	unsigned int local_kept = 0;
	unsigned int local_dropped = 0;

	if (dev->fwnode)
		fwnode_links_purge_suppliers(dev->fwnode);

	device_links_write_lock();
	list_for_each_entry_safe(link, ln, &dev->links.suppliers, c_node) {
		if (!(link->flags & DL_FLAG_MANAGED))
			continue;

		if (link->status != DL_STATE_AVAILABLE) {
			device_link_drop_managed(link);
			local_dropped++;
			continue;
		}

		WRITE_ONCE(link->status, DL_STATE_CONSUMER_PROBE);
		local_kept++;
	}
	dev->links.status = DL_DEV_PROBING;
	device_links_write_unlock();

	if (kept)
		*kept = local_kept;
	if (dropped)
		*dropped = local_dropped;
}
'''
    core = core[:drop_end] + helper + core[drop_end:]

    declaration_anchor = "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    declaration = declaration_anchor + (
        "extern void a52_device_links_force_probe(struct device *dev,\n"
        "\t\t\t\t\t unsigned int *kept,\n"
        "\t\t\t\t\t unsigned int *dropped);\n"
    )
    dd = replace_once(
        dd,
        declaration_anchor,
        declaration,
        "declare safe A52 device-link force-probe helper",
    )

    helper_match = r'''static bool a52_legacy_ufs_fw_devlink_consumer(const struct device *dev)
{
	const char *name;

	if (!dev)
		return false;
	name = dev_name(dev);
	return name && !strcmp(name, "1d84000.ufshc");
}

'''
    really_probe_anchor = "static int really_probe(struct device *dev, struct device_driver *drv)\n"
    dd = replace_once(
        dd,
        really_probe_anchor,
        helper_match + really_probe_anchor,
        "insert exact A52 UFS consumer selector",
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
\t\tunsigned int kept = 0;
\t\tunsigned int dropped = 0;

\t\t/*
\t\t * Run 35 proved that the real QMP PHY provider is bound, while the
\t\t * legacy UFS host is still rejected before ufs_qcom_probe(). Purge
\t\t * only this consumer's unresolved firmware links, drop unavailable
\t\t * managed device links, and transition available links to
\t\t * CONSUMER_PROBE so device_links_driver_bound/no_driver() remain
\t\t * internally consistent.
\t\t */
\t\ta52_device_links_force_probe(dev, &kept, &dropped);
\t\ta52_persistent_diag_mark("A52_UFS_FW_DEVLINK_FORCE_PROBE copy=1 dev=%s driver=%s reason=%s kept=%u dropped=%u\\n",
\t\t\tdev_name(dev), drv->name, reason, kept, dropped);
\t\ta52_persistent_diag_mark("A52_UFS_FW_DEVLINK_FORCE_PROBE copy=2 dev=%s driver=%s reason=%s kept=%u dropped=%u\\n",
\t\t\tdev_name(dev), drv->name, reason, kept, dropped);
\t\ta52_persistent_diag_mark("A52_UFS_FW_DEVLINK_FORCE_PROBE copy=3 dev=%s driver=%s reason=%s kept=%u dropped=%u\\n",
\t\t\tdev_name(dev), drv->name, reason, kept, dropped);
\t\tret = 0;
\t}
\tif (ret == -EPROBE_DEFER)
\t\tdriver_deferred_probe_add_trigger(dev, local_trigger_count);
\tif (ret)
\t\treturn ret;
'''
    dd = replace_once(dd, old, new, "prepare exact A52 UFS host after stale fw_devlink defer")

    checks = {
        "exact_host_scope": 'strcmp(name, "1d84000.ufshc")' in dd,
        "only_supplier_defer_overridden": (
            "ret == -EPROBE_DEFER && a52_legacy_ufs_fw_devlink_consumer(dev)" in dd
        ),
        "fwnode_suppliers_purged": "fwnode_links_purge_suppliers(dev->fwnode);" in core,
        "unavailable_managed_links_dropped": "device_link_drop_managed(link);" in helper,
        "available_links_transitioned": (
            "WRITE_ONCE(link->status, DL_STATE_CONSUMER_PROBE);" in helper
        ),
        "consumer_state_probing": "dev->links.status = DL_DEV_PROBING;" in helper,
        "three_persistent_markers": all(
            f"A52_UFS_FW_DEVLINK_FORCE_PROBE copy={copy}" in dd for copy in (1, 2, 3)
        ),
        "normal_defer_retained": (
            "if (ret == -EPROBE_DEFER)\n\t\tdriver_deferred_probe_add_trigger" in dd
        ),
        "probe_call_marker_retained": "A52DEV copy=1 CALL" in dd,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("safe A52 UFS fw_devlink bridge audit failed: " + ", ".join(failed))

    core_path.write_text(core, encoding="utf-8")
    dd_path.write_text(dd, encoding="utf-8")
    (output / "patched-drivers-base-core-run37.c").write_text(core, encoding="utf-8")
    (output / "patched-drivers-base-dd-run37.c").write_text(dd, encoding="utf-8")
    return {
        "status": "bridged-safely",
        "reason": (
            "Run 35 bound the flat QMP PHY, but the UFS host still deferred before "
            "the A52DEV CALL and A52UFS PROBE_BEGIN markers."
        ),
        "scope": "device 1d84000.ufshc only; all other consumers retain strict fw_devlink",
        "link_state_policy": (
            "purge unresolved firmware suppliers, drop unavailable managed links, "
            "transition available managed links to CONSUMER_PROBE"
        ),
        "safety": (
            "pinctrl binding, DMA/IOMMU setup, PM-domain activation, and all UFS "
            "clock/PHY/reset/regulator getters remain active and may still defer"
        ),
        "checks": checks,
    }


def merge_report(output: Path, bridge: dict) -> None:
    path = output / "stage-report.json"
    if not path.is_file():
        raise SystemExit("Run 36 stage report is missing")
    report = json.loads(path.read_text(encoding="utf-8"))
    checks = bridge.get("checks", {})
    report["run37_a52_ufs_fw_devlink_safe_bridge"] = bridge
    report.setdefault("checks", {})["run37_a52_ufs_fw_devlink_safe_bridge"] = bool(
        checks and all(checks.values())
    )
    if not report["checks"]["run37_a52_ufs_fw_devlink_safe_bridge"]:
        raise SystemExit("Run 37 safe merged audit failed")
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
            (output / "stage94b-run37-safe-wrapper-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
        except BaseException:
            pass
        raise
