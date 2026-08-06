#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

MARKER = "A52_PHASE234_PHASE230_LAYOUT_FIX_V2"
ANCHOR = "\ndef locate_phase234_kernel_root() -> Path | None:\n"

OVERRIDE = r'''

# A52_PHASE234_PHASE230_LAYOUT_FIX_V2
# Phase 230 inserts bounded KGSL records between driver_match_device() and the
# inherited RSCC records. Match only the two contiguous RSCC record blocks so
# all KGSL and Phase 202 SMMU instrumentation remains byte-for-byte intact.
def patch_phase234_dd_text(text: str, label: str) -> str:
    if "A52_PHASE234_RSCC_MATCH_FILTER_V1" in text:
        return text

    helper_old = """static bool a52_rscc_probe_device(const struct device *dev)
{
\treturn dev && dev->of_node &&
\t\tof_device_is_compatible(dev->of_node, \"qcom,sde-rsc\");
}
"""
    helper_new = helper_old + """
/* A52_PHASE234_RSCC_MATCH_FILTER_V1 */
static bool a52_rscc_probe_driver(const struct device_driver *drv)
{
\treturn drv && drv->name && !strcmp(drv->name, \"sde_rsc\");
}
"""
    text = _replace_exact_once(
        text, helper_old, helper_new, f"{label}: RSCC driver helper v2"
    )

    old_device = """\tif (a52_rscc_probe_device(dev))
\t\ta52_ackfr_record(\"RSCCCORE match path=device-attach dev=%s drv=%s rc=%d\",
\t\t\tdev_name(dev), drv && drv->name ? drv->name : \"-\", ret);
"""
    new_device = """\tif (a52_rscc_probe_device(dev) &&
\t    (ret || a52_rscc_probe_driver(drv)))
\t\ta52_ackfr_record(\"RSCCFOCUS match path=device-attach dev=%s drv=%s rc=%d\",
\t\t\tdev_name(dev), drv && drv->name ? drv->name : \"-\", ret);
"""
    text = _replace_exact_once(
        text, old_device, new_device, f"{label}: device-attach focused block v2"
    )

    old_driver = """\tif (a52_rscc_probe_device(dev))
\t\ta52_ackfr_record(\"RSCCCORE match path=driver-attach dev=%s drv=%s rc=%d\",
\t\t\tdev_name(dev), drv && drv->name ? drv->name : \"-\", ret);
"""
    new_driver = """\tif (a52_rscc_probe_device(dev) &&
\t    (ret || a52_rscc_probe_driver(drv)))
\t\ta52_ackfr_record(\"RSCCFOCUS match path=driver-attach dev=%s drv=%s rc=%d\",
\t\t\tdev_name(dev), drv && drv->name ? drv->name : \"-\", ret);
"""
    text = _replace_exact_once(
        text, old_driver, new_driver, f"{label}: driver-attach focused block v2"
    )
    return text
'''


def main() -> int:
    path = Path(__file__).with_name("227_phase226_retention_wrapper.py")
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("Phase 234 cumulative Phase 230 layout repair already present")
        return 0
    count = text.count(ANCHOR)
    if count != 1:
        raise SystemExit(
            f"expected one Phase 234 override insertion anchor, found {count}"
        )
    text = text.replace(ANCHOR, OVERRIDE + ANCHOR, 1)
    required = (
        MARKER,
        "device-attach focused block v2",
        "driver-attach focused block v2",
        "RSCCFOCUS match path=device-attach",
        "RSCCFOCUS match path=driver-attach",
    )
    for token in required:
        if token not in text:
            raise SystemExit(f"missing repaired Phase 234 token: {token}")
    path.write_text(text, encoding="utf-8")
    print("Phase 234 cumulative Phase 230 RSCC layout repair: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
