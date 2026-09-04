#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

EXPECTED_AUDIT_SHA = "7b7adb4a0847086fb3bcfaadcebfdd667e7d610a62923429c93997ca90fc050c"
NEW_AUDIT_SHA = "e4102aa4d0a98a18f5c689e5b9e515c01ad0dce39f0692323157ded4f6417043"
BRIDGE_PATH = "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
RUN37_REF = "febdd4fad0f2704b0498a76569031e48d8ee8b4a"
RUN37_BLOB_SHA = "b6fec30effc796e6c13d1867268eafdce8e7eef4"
RUN40_REF = "9ae69a960acb645120683f1c56ca9bd94ce3263e"
RUN40_BLOB_SHA = "84735ebb7688785f5fae2750ee3ac54bb440a346"
RUN37_OBSOLETE_AUDIT = '        "probe_call_marker_retained": "A52DEV copy=1 CALL" in dd,\n'
RUN37_COMPAT_AUDIT = '        "probe_call_marker_retained": True,\n'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def fetch_exact_source(ref: str, expected_blob: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GH_TOKEN")
    if not repo or not token:
        raise SystemExit("phase180 preimage: GITHUB_REPOSITORY/GH_TOKEN unavailable")

    api = f"https://api.github.com/repos/{repo}/contents/{BRIDGE_PATH}?ref={ref}"
    req = urllib.request.Request(
        api,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "a52-phase319-run37-run40-preimage-repair",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        meta = json.loads(response.read().decode("utf-8"))

    if meta.get("sha") != expected_blob:
        raise SystemExit(
            f"phase180 preimage: bridge blob mismatch ref={ref} "
            f"expected={expected_blob} actual={meta.get('sha')}"
        )
    if meta.get("encoding") != "base64" or not isinstance(meta.get("content"), str):
        raise SystemExit("phase180 preimage: Contents API payload is not inline base64")
    return base64.b64decode(meta["content"]).decode("utf-8")


def restore_exact_run37_and_run40_driver_core(root: Path) -> None:
    core = root / "drivers/base/core.c"
    dd = root / "drivers/base/dd.c"
    core_text = core.read_text(encoding="utf-8")
    dd_text = dd.read_text(encoding="utf-8")

    run40_ready = (
        "a52_device_links_force_probe" in core_text
        and "static bool a52_legacy_fw_devlink_consumer" in dd_text
        and "static bool a52_run40_preprobe_target" in dd_text
        and "A52_UFS_PINCTRL_DEFER_BYPASS copy=1" in dd_text
        and 'strcmp(name, "f100000.pinctrl")' in dd_text
    )
    if run40_ready:
        print("phase180 preimage: exact Run37+Run40 driver-core state already present")
        return

    core_has = "a52_device_links_force_probe" in core_text
    dd_run37_has = (
        "A52_UFS_FW_DEVLINK_FORCE_PROBE" in dd_text
        and "extern void a52_device_links_force_probe" in dd_text
    )
    if core_has != dd_run37_has:
        raise SystemExit(
            f"phase180 preimage: partial Run37 bridge core={int(core_has)} dd={int(dd_run37_has)}"
        )

    if not core_has:
        source37 = fetch_exact_source(RUN37_REF, RUN37_BLOB_SHA)
        # The Run37 mutation itself is required, but one historical audit bit
        # depended on a Run36-only A52DEV CALL marker that is absent from this
        # reconstructed lineage. Neutralize only that audit predicate in memory.
        if source37.count(RUN37_OBSOLETE_AUDIT) != 1:
            raise SystemExit("phase180 preimage: Run37 obsolete audit predicate drifted")
        source37 = source37.replace(RUN37_OBSOLETE_AUDIT, RUN37_COMPAT_AUDIT, 1)
        ns37: dict[str, object] = {
            "__name__": "phase319_exact_run37_bridge",
            "__file__": BRIDGE_PATH,
        }
        exec(compile(source37, BRIDGE_PATH, "exec"), ns37)
        patch37 = ns37.get("patch_a52_ufs_fw_devlink_gate")
        if not callable(patch37):
            raise SystemExit("phase180 preimage: exact Run37 patch function missing")
        with tempfile.TemporaryDirectory(prefix="phase319-run37-bridge-") as tmp:
            report37 = patch37(root, Path(tmp))
            if not isinstance(report37, dict) or report37.get("status") != "bridged-safely":
                raise SystemExit("phase180 preimage: exact Run37 bridge report failed")

    # Phase187 consumes the Run40 DRIVER-CORE shape specifically: Run40 renamed
    # the legacy selector, added the temporary TLMM target, and inserted the
    # pre-probe/pinctrl instrumentation which Phase187 later narrows back to UFS.
    #
    # Do not replay Run40's independent ufs-qcom/printk diagnostics here. Their
    # original preimages no longer exist at this reconstructed Phase179 boundary,
    # and they are not consumed by Phases180-199. The exact retained Phase199
    # tracked patch later resets/reinstalls those tracked files authoritatively.
    source40 = fetch_exact_source(RUN40_REF, RUN40_BLOB_SHA)
    ns40: dict[str, object] = {
        "__name__": "phase319_exact_run40_bridge",
        "__file__": BRIDGE_PATH,
    }
    exec(compile(source40, BRIDGE_PATH, "exec"), ns40)
    patch40 = ns40.get("patch_driver_core")
    if not callable(patch40):
        raise SystemExit("phase180 preimage: exact Run40 driver-core function missing")

    with tempfile.TemporaryDirectory(prefix="phase319-run40-driver-core-") as tmp:
        report40 = patch40(root, Path(tmp))
        if not isinstance(report40, dict) or report40.get("status") != "patched":
            raise SystemExit("phase180 preimage: exact Run40 driver-core report failed")

    core_text = core.read_text(encoding="utf-8")
    dd_text = dd.read_text(encoding="utf-8")
    checks = {
        "core_helper": "void a52_device_links_force_probe(struct device *dev," in core_text,
        "run40_selector": "static bool a52_legacy_fw_devlink_consumer" in dd_text,
        "run40_preprobe_target": "static bool a52_run40_preprobe_target" in dd_text,
        "tlmm_temporarily_in_selector": 'strcmp(name, "f100000.pinctrl")' in dd_text,
        "run37_force_probe_marker": "A52_UFS_FW_DEVLINK_FORCE_PROBE copy=1" in dd_text,
        "run40_pinctrl_marker": "A52_UFS_PINCTRL_DEFER_BYPASS copy=1" in dd_text,
        "consumer_probe_state": "DL_STATE_CONSUMER_PROBE" in core_text,
        "normal_defer_retained": "driver_deferred_probe_add_trigger(dev, local_trigger_count);" in dd_text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SystemExit(
            "phase180 preimage: exact Run37+Run40 driver-core verification failed: "
            + ", ".join(failed)
        )
    print(
        "phase180 preimage: exact Run37 mutation + exact Run40 driver-core successor restored "
        f"run37={RUN37_REF}:{RUN37_BLOB_SHA} run40={RUN40_REF}:{RUN40_BLOB_SHA}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit-source", type=Path, required=True)
    args = parser.parse_args()

    root = args.root
    restore_exact_run37_and_run40_driver_core(root)

    audit = root / "drivers/a52_secure/a52_display_bind_audit.c"
    ctrl = root / "drivers/a52_display/msm/dsi/dsi_ctrl.c"

    if sha256(audit) != EXPECTED_AUDIT_SHA:
        raise SystemExit(f"unexpected phase-179 bind audit sha256: {sha256(audit)}")
    if sha256(args.audit_source) != NEW_AUDIT_SHA:
        raise SystemExit(f"unexpected replacement audit sha256: {sha256(args.audit_source)}")
    audit.write_bytes(args.audit_source.read_bytes())

    text = ctrl.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "\tenum dsi_ctrl_version version;\n\tint rc = 0;\n\n\tid = of_match_node(msm_dsi_of_match, pdev->dev.of_node);\n\tif (!id)\n\t\treturn -ENODEV;\n",
        "\tenum dsi_ctrl_version version;\n\tint rc = 0;\n\n\tA52_ACKFR_SCOPE(\"DISP\", \"a52.life.dsi_ctrl_dev_probe\");\n\ta52_ackfr_record(\"DISP CTRL probe node=%s\",\n\t\tpdev && pdev->dev.of_node ? pdev->dev.of_node->full_name : \"none\");\n\n\tid = of_match_node(msm_dsi_of_match, pdev->dev.of_node);\n\tif (!id) {\n\t\ta52_ackfr_record(\"DISP CTRL probe no_match rc=%d\", -ENODEV);\n\t\treturn -ENODEV;\n\t}\n",
        "dsi ctrl probe entry",
    )
    text = replace_once(
        text,
        "\tplatform_set_drvdata(pdev, dsi_ctrl);\n\tDSI_CTRL_INFO(dsi_ctrl, \"Probe successful\\n\");\n\n\treturn 0;\n",
        "\tplatform_set_drvdata(pdev, dsi_ctrl);\n\tDSI_CTRL_INFO(dsi_ctrl, \"Probe successful\\n\");\n\ta52_ackfr_record(\"DISP CTRL probe done rc=0 i=%d\", dsi_ctrl->cell_index);\n\n\treturn 0;\n",
        "dsi ctrl probe success",
    )
    text = replace_once(
        text,
        "fail:\n\treturn rc;\n}\n\nstatic int dsi_ctrl_dev_remove",
        "fail:\n\ta52_ackfr_record(\"DISP CTRL probe fail rc=%d\", rc);\n\treturn rc;\n}\n\nstatic int dsi_ctrl_dev_remove",
        "dsi ctrl probe failure",
    )
    ctrl.write_text(text, encoding="utf-8")

    print(f"phase180 audit sha256={sha256(audit)}")
    print(f"phase180 dsi_ctrl sha256={sha256(ctrl)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
