#!/usr/bin/env python3
"""Phase268 prep: stage the exact Golden STM FTS5CU56A A52 touch producer."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52_PHASE268_STM_FTS5CU56A_TOUCH_PARITY_V1"
FILES = ("fts_ts.c", "fts_ts.h", "fts_fwu.c", "fts_sec.c")
CONFIG = "TOUCHSCREEN_STM_FTS5CU56A"


def locate(argv: list[str]) -> Path:
    return Path(argv[0]).resolve() if argv and argv[0] != "--self-test" else Path("gki/common").resolve()


def workspace(root: Path) -> Path:
    return root.parent.parent


def copy_required(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise RuntimeError(f"Phase268 Golden file missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker not in text:
        path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n", encoding="utf-8")


def set_config(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    enabled = f"CONFIG_{CONFIG}=y"
    disabled = f"# CONFIG_{CONFIG} is not set"
    if enabled in text.splitlines():
        return
    text = text.replace(disabled, enabled, 1) if disabled in text else text.rstrip() + "\n" + enabled + "\n"
    path.write_text(text, encoding="utf-8")


def self_test() -> None:
    assert FILES == ("fts_ts.c", "fts_ts.h", "fts_fwu.c", "fts_sec.c")
    assert CONFIG == "TOUCHSCREEN_STM_FTS5CU56A"
    print("Phase 268 STM FTS5CU56A touch staging self-test: PASS", flush=True)


def apply(root: Path) -> None:
    ws = workspace(root)
    golden = ws / "workspace/touchgrass-a52xq"
    cfg = ws / "workspace/gki-phase199-out/.config"
    if not root.is_dir() or not (golden / ".git").is_dir() or not cfg.is_file():
        raise RuntimeError("Phase268 reconstructed source boundary missing")

    src = golden / "drivers/input/touchscreen/stm/fts5cu56a"
    dst = root / "drivers/a52_touch/fts5cu56a"
    for name in FILES:
        copy_required(src / name, dst / name)

    (dst / "Makefile").write_text(
        f"# {MARKER}\nccflags-y += -include $(srctree)/a52-port-compat.h\n"
        "ccflags-y += -Wformat\n"
        "obj-$(CONFIG_TOUCHSCREEN_STM_FTS5CU56A) += fts_ts.o fts_fwu.o\n",
        encoding="utf-8",
    )
    (root / "drivers/a52_touch/Makefile").write_text("obj-y += fts5cu56a/\n", encoding="utf-8")
    (root / "drivers/a52_touch/Kconfig").write_text(
        f"# {MARKER}\n"
        "config TOUCHSCREEN_STM_FTS5CU56A\n"
        "    bool \"A52 STM FTS5CU56A touchscreen\"\n"
        "    default y\n"
        "    depends on I2C && INPUT_TOUCHSCREEN\n",
        encoding="utf-8",
    )
    append_once(root / "drivers/Makefile", MARKER, f"# {MARKER}\nobj-y += a52_touch/")
    append_once(root / "drivers/Kconfig", MARKER, f"# {MARKER}\nsource \"drivers/a52_touch/Kconfig\"")
    set_config(cfg)

    source = (dst / "fts_ts.c").read_text(encoding="utf-8")
    if "fts_probe" not in source or "fts_touch" not in source:
        raise RuntimeError("Phase268 STM FTS touch producer contract missing")
    print(f"{MARKER}: Golden STM FTS5CU56A touch closure staged", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
