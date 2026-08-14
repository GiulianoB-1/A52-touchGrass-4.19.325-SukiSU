#!/usr/bin/env python3
"""Phase265 prep: stage the narrow Golden A52 SSC + Samsung sensor-class contract.

This patch is intentionally independent from the large audio techpack.  The
healthy A52 vendor image writes /sys/kernel/boot_slpi/boot and runs sscrpcd,
while the Golden kernel builds CONFIG_SENSORS_SSC and CONFIG_SENSORS.

Do not add this to the active compile workflow until Phase264 FastRPC/PDR is
compile-green.  The script is committed early so the exact SSC closure is
recorded while Phase264 compatibility work proceeds.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52_PHASE265_SSC_SENSOR_PARITY_V1"
FILES = ("sensors_ssc.c", "sensors_core.c")
CONFIGS = ("SENSORS_SSC", "SENSORS")


def locate(argv: list[str]) -> Path:
    if argv and argv[0] != "--self-test":
        return Path(argv[0]).resolve()
    return Path("gki/common").resolve()


def workspace(root: Path) -> Path:
    return root.parent.parent


def set_config(path: Path, symbol: str) -> None:
    text = path.read_text(encoding="utf-8")
    enabled = f"CONFIG_{symbol}=y"
    disabled = f"# CONFIG_{symbol} is not set"
    if enabled in text.splitlines():
        return
    if disabled in text:
        text = text.replace(disabled, enabled, 1)
    else:
        text = text.rstrip() + "\n" + enabled + "\n"
    path.write_text(text, encoding="utf-8")


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n", encoding="utf-8")


def copy_required(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise RuntimeError(f"Phase265 required Golden file missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def self_test() -> None:
    assert FILES == ("sensors_ssc.c", "sensors_core.c")
    assert CONFIGS == ("SENSORS_SSC", "SENSORS")
    print("Phase 265 SSC sensor staging self-test: PASS", flush=True)


def apply(root: Path) -> None:
    ws = workspace(root)
    golden = ws / "workspace/touchgrass-a52xq"
    cfg = ws / "workspace/gki-phase199-out/.config"
    if not root.is_dir() or not (golden / ".git").is_dir() or not cfg.is_file():
        raise RuntimeError("Phase265 reconstructed GKI/Golden/config boundary missing")

    # Phase265 intentionally requires the Phase264 vendor FastRPC ABI first.
    phase264 = root / "drivers/a52_fastrpc/adsprpc_shared.h"
    if not phase264.is_file() or 'DEVICE_NAME      "adsprpc-smd"' not in phase264.read_text(encoding="utf-8"):
        raise RuntimeError("Phase265 requires Phase264 FastRPC/PDR staging first")

    dst = root / "drivers/a52_sensors"
    dst.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        copy_required(golden / "drivers/sensors" / name, dst / name)

    # Kernel-private Samsung sensor class declarations.
    if not (root / "include/linux/sensor/sensors_core.h").is_file():
        copy_required(
            golden / "include/linux/sensor/sensors_core.h",
            root / "include/linux/sensor/sensors_core.h",
        )

    # sensors_ssc.c includes <linux/msm_dsps.h>; kernel headers resolve that
    # through the UAPI tree when the vendor header is staged here.
    if not (root / "include/uapi/linux/msm_dsps.h").is_file():
        copy_required(
            golden / "include/uapi/linux/msm_dsps.h",
            root / "include/uapi/linux/msm_dsps.h",
        )

    mk = f"""# {MARKER}
ccflags-y += -include $(srctree)/a52-port-compat.h
obj-$(CONFIG_SENSORS_SSC) += sensors_ssc.o
obj-$(CONFIG_SENSORS) += sensors_core.o
"""
    (dst / "Makefile").write_text(mk, encoding="utf-8")

    kc = f"""# {MARKER}
config SENSORS_SSC
    bool "A52 SSC / SLPI loader contract"
    default y
    depends on MSM_SUBSYSTEM_RESTART

config SENSORS
    bool "A52 Samsung sensors class"
    default y
"""
    (dst / "Kconfig").write_text(kc, encoding="utf-8")

    append_once(root / "drivers/Makefile", MARKER, f"# {MARKER}\nobj-y += a52_sensors/")
    append_once(root / "drivers/Kconfig", MARKER, f"# {MARKER}\nsource \"drivers/a52_sensors/Kconfig\"")

    for symbol in CONFIGS:
        set_config(cfg, symbol)

    ssc = (dst / "sensors_ssc.c").read_text(encoding="utf-8")
    for token in (
        'subsystem_get_with_fwname("slpi", firmware_name)',
        'kobject_create_and_add("boot_slpi", kernel_kobj)',
        '.compatible = "qcom,msm-ssc-sensors"',
        '.name = "sensors-ssc"',
    ):
        if token not in ssc:
            raise RuntimeError(f"Phase265 SSC runtime contract missing: {token}")

    core = (dst / "sensors_core.c").read_text(encoding="utf-8")
    if 'class_create(THIS_MODULE, "sensors")' not in core:
        raise RuntimeError("Phase265 Samsung sensors class contract missing")

    for symbol in CONFIGS:
        if f"CONFIG_{symbol}=y" not in cfg.read_text(encoding="utf-8").splitlines():
            raise RuntimeError(f"Phase265 config did not apply: CONFIG_{symbol}=y")

    print(f"{MARKER}: Golden SSC loader and Samsung sensor class staged", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
