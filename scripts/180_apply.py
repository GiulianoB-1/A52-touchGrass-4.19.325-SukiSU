#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

EXPECTED_AUDIT_SHA = "7b7adb4a0847086fb3bcfaadcebfdd667e7d610a62923429c93997ca90fc050c"
NEW_AUDIT_SHA = "d903ec559bb1f5483f5b063cb421a4779416295734e417dbc1dabeaeeeb2c3f1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit-source", type=Path, required=True)
    args = parser.parse_args()

    root = args.root
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
