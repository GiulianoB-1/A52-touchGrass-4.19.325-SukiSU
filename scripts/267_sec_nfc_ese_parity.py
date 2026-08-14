#!/usr/bin/env python3
"""Phase267 prep: stage the exact Golden Samsung SEC_NFC + P3 eSE closure."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52_PHASE267_SEC_NFC_ESE_PARITY_V1"
ROOT_FILES = ("sec_nfc.c", "sec_nfc.h", "ese_p3.c", "ese_p3.h", "nfc_wakelock.h")
LOGGER_FILES = ("nfc_logger.c", "nfc_logger.h")
CONFIGS = ("SEC_NFC", "ESE_P3_LSI", "SEC_NFC_LOGGER")


def locate(argv: list[str]) -> Path:
    return Path(argv[0]).resolve() if argv and argv[0] != "--self-test" else Path("gki/common").resolve()


def workspace(root: Path) -> Path:
    return root.parent.parent


def copy_required(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise RuntimeError(f"Phase267 Golden file missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker not in text:
        path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n", encoding="utf-8")


def set_config(path: Path, symbol: str) -> None:
    text = path.read_text(encoding="utf-8")
    enabled = f"CONFIG_{symbol}=y"
    disabled = f"# CONFIG_{symbol} is not set"
    if enabled in text.splitlines():
        return
    text = text.replace(disabled, enabled, 1) if disabled in text else text.rstrip() + "\n" + enabled + "\n"
    path.write_text(text, encoding="utf-8")


def self_test() -> None:
    assert CONFIGS == ("SEC_NFC", "ESE_P3_LSI", "SEC_NFC_LOGGER")
    assert "nfc_wakelock.h" in ROOT_FILES and "nfc_logger.c" in LOGGER_FILES
    print("Phase 267 Samsung NFC/eSE staging self-test: PASS", flush=True)


def apply(root: Path) -> None:
    ws = workspace(root)
    golden = ws / "workspace/touchgrass-a52xq"
    cfg = ws / "workspace/gki-phase199-out/.config"
    if not root.is_dir() or not (golden / ".git").is_dir() or not cfg.is_file():
        raise RuntimeError("Phase267 reconstructed source boundary missing")

    dst = root / "drivers/a52_nfc"
    for name in ROOT_FILES:
        copy_required(golden / "drivers/nfc" / name, dst / name)
    for name in LOGGER_FILES:
        copy_required(golden / "drivers/nfc/nfc_logger" / name, dst / "nfc_logger" / name)

    mk = f"""# {MARKER}
ccflags-y += -include $(srctree)/a52-port-compat.h
obj-$(CONFIG_SEC_NFC) += sec_nfc.o
obj-$(CONFIG_ESE_P3_LSI) += ese_p3.o
obj-$(CONFIG_SEC_NFC_LOGGER) += nfc_logger/nfc_logger.o
"""
    (dst / "Makefile").write_text(mk, encoding="utf-8")

    kc = f"""# {MARKER}
config SEC_NFC
    bool "A52 Samsung NFC controller"
    default y
    depends on I2C

config ESE_P3_LSI
    bool "A52 Samsung P3 eSE"
    default y
    depends on SPI

config SEC_NFC_LOGGER
    bool "A52 Samsung NFC logger"
    default y
    depends on SEC_NFC
"""
    (dst / "Kconfig").write_text(kc, encoding="utf-8")

    append_once(root / "drivers/Makefile", MARKER, f"# {MARKER}\nobj-y += a52_nfc/")
    append_once(root / "drivers/Kconfig", MARKER, f"# {MARKER}\nsource \"drivers/a52_nfc/Kconfig\"")
    for symbol in CONFIGS:
        set_config(cfg, symbol)

    nfc = (dst / "sec_nfc.c").read_text(encoding="utf-8")
    if "SEC_NFC_DRIVER_NAME" not in (dst / "sec_nfc.h").read_text(encoding="utf-8"):
        raise RuntimeError("Phase267 Samsung NFC userspace ABI header missing")
    if "sec_nfc_irq_thread_fn" not in nfc or "SEC_NFC_GET_INFO" not in nfc:
        raise RuntimeError("Phase267 Samsung NFC producer contract missing")
    ese = (dst / "ese_p3.c").read_text(encoding="utf-8")
    if "MAX_BUFFER_SIZE" not in ese or "struct p3_data" not in ese:
        raise RuntimeError("Phase267 P3 eSE contract missing")

    print(f"{MARKER}: Golden Samsung NFC/eSE/logger closure staged", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
