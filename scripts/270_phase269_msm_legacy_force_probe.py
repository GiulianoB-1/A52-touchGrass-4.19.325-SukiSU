#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

TARGET = Path("drivers/gpu/drm/drm_connector.c")
MARKER = "A52_PHASE270_MSM_LEGACY_FORCE_PROBE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


def apply(root: Path) -> None:
    p = root / TARGET
    if not p.is_file():
        raise RuntimeError(f"missing {TARGET}")
    text = p.read_text(encoding="utf-8")
    if MARKER in text:
        print(MARKER)
        print("Phase270 already applied")
        return

    # Exact Android 5.10 drm_mode_getconnector() shape.  Phase269 hardware
    # proved that the vendor Composer sees connected DSI + one possible encoder,
    # but count_modes=0 / encoder_id=0.  Android 5.10 gates the forced probe on
    # DRM master; TouchGrass 4.19 did not.  Restore only that legacy behavior for
    # the exec-latched Composer TGID on this downstream msm_drm device.
    text = one(
        text,
        "#include <linux/export.h>\n",
        "#include <linux/export.h>\n#include <linux/sched.h>\n#include <linux/string.h>\n#include <linux/a52_ack_secure_flight_recorder.h>\n\nextern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);\n",
        "includes / composer identity declaration",
    )

    text = one(
        text,
        "\tbool is_current_master;\n",
        "\tbool is_current_master;\n\tbool a52_legacy_msm_composer_probe;\n",
        "getconnector local state",
    )

    text = one(
        text,
        "\tis_current_master = drm_is_current_master(file_priv);\n\n\tmutex_lock(&dev->mode_config.mutex);\n",
        "\tis_current_master = drm_is_current_master(file_priv);\n\t/* " + MARKER + "\n"
        "\t * Qualcomm's Android-11-era Composer expects the legacy 4.19\n"
        "\t * GETCONNECTOR behavior where count_modes=0 forces fill_modes().\n"
        "\t * Newer DRM core limits that probe to current DRM master.  Scope\n"
        "\t * the compatibility exception to the exact exec-latched Composer\n"
        "\t * TGID and the downstream msm_drm driver only.\n"
        "\t */\n"
        "\ta52_legacy_msm_composer_probe =\n"
        "\t\ta52_ackfr_phase269_is_composer_tgid(current->tgid) &&\n"
        "\t\tdev->driver && dev->driver->name &&\n"
        "\t\t!strcmp(dev->driver->name, \"msm_drm\");\n\n"
        "\tmutex_lock(&dev->mode_config.mutex);\n",
        "master-gate compatibility state",
    )

    old = '''\tif (out_resp->count_modes == 0) {\n\t\tif (is_current_master)\n\t\t\tconnector->funcs->fill_modes(connector,\n\t\t\t\t\t\t     dev->mode_config.max_width,\n\t\t\t\t\t\t     dev->mode_config.max_height);\n\t\telse\n'''
    new = '''\tif (out_resp->count_modes == 0) {\n\t\tif (is_current_master || a52_legacy_msm_composer_probe) {\n\t\t\tif (a52_legacy_msm_composer_probe && !is_current_master)\n\t\t\t\ta52_ackfr_record("P269 FPROBE id=%u master=0 drv=%.15s",\n\t\t\t\t\tconnector->base.id, dev->driver->name);\n\t\t\tconnector->funcs->fill_modes(connector,\n\t\t\t\t\t\t     dev->mode_config.max_width,\n\t\t\t\t\t\t     dev->mode_config.max_height);\n\t\t} else\n'''
    text = one(text, old, new, "forced-probe master gate")

    p.write_text(text, encoding="utf-8")
    print(MARKER)
    print("Phase270: msm_drm Composer legacy forced mode probe enabled")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    args = ap.parse_args()
    apply(args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
