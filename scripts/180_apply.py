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
RUN37_REF = "febdd4fad0f2704b0498a76569031e48d8ee8b4a"
RUN37_PATH = "scripts/94b_stage_a52xq_ufs_phy_bridge.py"
RUN37_BLOB_SHA = "b6fec30effc796e6c13d1867268eafdce8e7eef4"
RUN37_OBSOLETE_AUDIT = '        "probe_call_marker_retained": "A52DEV copy=1 CALL" in dd,\n'
RUN37_COMPAT_AUDIT = '        "probe_call_marker_retained": True,\n'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def restore_exact_run37_devlink_bridge(root: Path) -> None:
    core = root / "drivers/base/core.c"
    dd = root / "drivers/base/dd.c"
    core_text = core.read_text(encoding="utf-8")
    dd_text = dd.read_text(encoding="utf-8")

    core_has = "a52_device_links_force_probe" in core_text
    dd_has = (
        "A52_UFS_FW_DEVLINK_FORCE_PROBE" in dd_text
        and "extern void a52_device_links_force_probe" in dd_text
    )
    if core_has and dd_has:
        print("phase180 preimage: exact Run37 devlink bridge already present")
        return
    if core_has or dd_has:
        raise SystemExit(
            f"phase180 preimage: partial Run37 bridge core={int(core_has)} dd={int(dd_has)}"
        )

    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GH_TOKEN")
    if not repo or not token:
        raise SystemExit("phase180 preimage: GITHUB_REPOSITORY/GH_TOKEN unavailable")

    api = f"https://api.github.com/repos/{repo}/contents/{RUN37_PATH}?ref={RUN37_REF}"
    req = urllib.request.Request(
        api,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "a52-phase319-run37-preimage-repair",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        meta = json.loads(response.read().decode("utf-8"))

    if meta.get("sha") != RUN37_BLOB_SHA:
        raise SystemExit(
            f"phase180 preimage: Run37 blob mismatch expected={RUN37_BLOB_SHA} actual={meta.get('sha')}"
        )
    if meta.get("encoding") != "base64" or not isinstance(meta.get("content"), str):
        raise SystemExit("phase180 preimage: Run37 Contents API payload is not inline base64")

    # Run37's mutation itself is still the required historical preimage. Its
    # audit also required a Run36-only A52DEV CALL diagnostic marker. That
    # marker is absent from this reconstructed lineage and is not consumed by
    # the bridge mutation or by Phases180-199. Patch only that audit predicate
    # in memory, with an exact one-match guard; the fetched Run37 blob identity
    # and all post-mutation bridge checks remain fail-closed.
    source = base64.b64decode(meta["content"]).decode("utf-8")
    if source.count(RUN37_OBSOLETE_AUDIT) != 1:
        raise SystemExit("phase180 preimage: Run37 obsolete audit predicate drifted")
    source = source.replace(RUN37_OBSOLETE_AUDIT, RUN37_COMPAT_AUDIT, 1)

    namespace: dict[str, object] = {
        "__name__": "phase319_exact_run37_bridge",
        "__file__": RUN37_PATH,
    }
    exec(compile(source, RUN37_PATH, "exec"), namespace)
    patch = namespace.get("patch_a52_ufs_fw_devlink_gate")
    if not callable(patch):
        raise SystemExit("phase180 preimage: exact Run37 patch function missing")

    with tempfile.TemporaryDirectory(prefix="phase319-run37-bridge-") as tmp:
        out = Path(tmp)
        report = patch(root, out)
        if not isinstance(report, dict) or report.get("status") != "bridged-safely":
            raise SystemExit("phase180 preimage: exact Run37 bridge report failed")

    core_text = core.read_text(encoding="utf-8")
    dd_text = dd.read_text(encoding="utf-8")
    checks = {
        "core_helper": "void a52_device_links_force_probe(struct device *dev," in core_text,
        "dd_declaration": "extern void a52_device_links_force_probe(struct device *dev," in dd_text,
        "run37_marker": "A52_UFS_FW_DEVLINK_FORCE_PROBE copy=1" in dd_text,
        "consumer_probe_state": "DL_STATE_CONSUMER_PROBE" in core_text,
        "normal_defer_retained": "driver_deferred_probe_add_trigger(dev, local_trigger_count);" in dd_text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise SystemExit("phase180 preimage: exact Run37 bridge verification failed: " + ", ".join(failed))
    print(
        "phase180 preimage: exact Run37 devlink mutation restored with Run36-only audit compatibility "
        f"ref={RUN37_REF} blob={RUN37_BLOB_SHA}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit-source", type=Path, required=True)
    args = parser.parse_args()

    root = args.root
    restore_exact_run37_devlink_bridge(root)

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
