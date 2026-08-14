#!/usr/bin/env python3
"""Phase266 prep: stage the exact Golden ET713/ET7xx secure QCOM fingerprint closure.

The Golden kernel enables SENSORS_FINGERPRINT, SENSORS_FINGERPRINT_QCOM,
SENSORS_ET7XX and FINGERPRINT_SECURE.  The healthy A52 identifies the sensor as
ET713.  This patcher is committed as preparation only and is not wired into the
active Phase264 compile gate.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52_PHASE266_ET713_FINGERPRINT_PARITY_V1"
FILES = (
    "fingerprint.c",
    "fingerprint_sysfs.c",
    "fingerprint_common.c",
    "fingerprint_common_qcom.c",
    "fingerprint.h",
    "fingerprint_common.h",
    "et7xx-spi.c",
    "et7xx-spi_data_transfer.c",
    "et7xx.h",
)
CONFIGS = (
    "SENSORS_FINGERPRINT",
    "SENSORS_FINGERPRINT_QCOM",
    "SENSORS_ET7XX",
    "FINGERPRINT_SECURE",
)


def locate(argv: list[str]) -> Path:
    if argv and argv[0] != "--self-test":
        return Path(argv[0]).resolve()
    return Path("gki/common").resolve()


def workspace(root: Path) -> Path:
    return root.parent.parent


def copy_required(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise RuntimeError(f"Phase266 Golden file missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def set_config(path: Path, symbol: str) -> None:
    text = path.read_text(encoding="utf-8")
    enabled = f"CONFIG_{symbol}=y"
    disabled = f"# CONFIG_{symbol} is not set"
    if enabled in text.splitlines():
        return
    text = text.replace(disabled, enabled, 1) if disabled in text else text.rstrip() + "\n" + enabled + "\n"
    path.write_text(text, encoding="utf-8")


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker not in text:
        path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n", encoding="utf-8")


def self_test() -> None:
    assert "et7xx-spi.c" in FILES and "fingerprint_common_qcom.c" in FILES
    assert CONFIGS[-1] == "FINGERPRINT_SECURE"
    print("Phase 266 ET713 fingerprint staging self-test: PASS", flush=True)


def apply(root: Path) -> None:
    ws = workspace(root)
    golden = ws / "workspace/touchgrass-a52xq"
    cfg = ws / "workspace/gki-phase199-out/.config"
    if not root.is_dir() or not (golden / ".git").is_dir() or not cfg.is_file():
        raise RuntimeError("Phase266 reconstructed source boundary missing")

    dst = root / "drivers/a52_fingerprint"
    dst.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        copy_required(golden / "drivers/fingerprint" / name, dst / name)

    mk = f"""# {MARKER}
ccflags-y += -include $(srctree)/a52-port-compat.h
ccflags-y += -Wformat
obj-$(CONFIG_SENSORS_FINGERPRINT) += fingerprint.o
fingerprint-y := fingerprint_common.o fingerprint_sysfs.o
fingerprint-$(CONFIG_SENSORS_FINGERPRINT_QCOM) += fingerprint_common_qcom.o
fingerprint-$(CONFIG_SENSORS_ET7XX) += et7xx-spi.o et7xx-spi_data_transfer.o
"""
    (dst / "Makefile").write_text(mk, encoding="utf-8")

    kc = f"""# {MARKER}
config SENSORS_FINGERPRINT
    bool "A52 Samsung fingerprint framework"
    default y

config SENSORS_FINGERPRINT_QCOM
    bool "A52 Qualcomm fingerprint platform glue"
    default y
    depends on SENSORS_FINGERPRINT

config SENSORS_ET7XX
    bool "A52 ET713 / ET7xx fingerprint"
    default y
    depends on SENSORS_FINGERPRINT

config FINGERPRINT_SECURE
    bool "A52 secure fingerprint path"
    default y
    depends on SENSORS_ET7XX
"""
    (dst / "Kconfig").write_text(kc, encoding="utf-8")

    append_once(root / "drivers/Makefile", MARKER, f"# {MARKER}\nobj-y += a52_fingerprint/")
    append_once(root / "drivers/Kconfig", MARKER, f"# {MARKER}\nsource \"drivers/a52_fingerprint/Kconfig\"")
    for symbol in CONFIGS:
        set_config(cfg, symbol)

    et7 = (dst / "et7xx-spi.c").read_text(encoding="utf-8")
    for token in ("EGIS_IOC_MAGIC", "et7xx_power_control", "et7xx_pin_control"):
        if token not in et7 and token not in (dst / "et7xx.h").read_text(encoding="utf-8"):
            raise RuntimeError(f"Phase266 ET7xx contract token missing: {token}")
    qcom = (dst / "fingerprint_common_qcom.c").read_text(encoding="utf-8")
    if "spi_clk_enable" not in qcom or "cpu_speedup_enable" not in qcom:
        raise RuntimeError("Phase266 QCOM fingerprint glue contract missing")

    print(f"{MARKER}: Golden ET713 secure QCOM fingerprint closure staged", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
