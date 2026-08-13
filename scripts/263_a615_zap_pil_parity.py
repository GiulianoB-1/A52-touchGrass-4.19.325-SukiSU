#!/usr/bin/env python3
"""Phase263: restore Golden TouchGrass A615 ZAP PIL/SSR provider parity.

The downstream KGSL A615 path calls subsystem_get("a615_zap") from
A6xx ringbuffer start.  The port already carries the TouchGrass compatibility
headers, but the matching legacy PIL/subsystem provider objects were never
staged into the GKI build.  This overlay imports only the provider closure
needed by qcom,pil-tz-generic and enables the three matching Golden symbols.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52_PHASE263_A615_ZAP_PIL_PARITY_V1"
CONFIGS = (
    "CONFIG_MSM_SUBSYSTEM_RESTART=y",
    "CONFIG_MSM_PIL=y",
    "CONFIG_MSM_PIL_SSR_GENERIC=y",
)
SOURCES = (
    "peripheral-loader.c",
    "peripheral-loader.h",
    "subsys-pil-tz.c",
    "subsystem_restart.c",
    "subsystem_notif.c",
    "ramdump.c",
    "microdump_collector.c",
    "minidump_private.h",
)
OBJECTS = (
    "subsystem_notif.o",
    "subsystem_restart.o",
    "ramdump.o",
    "microdump_collector.o",
    "peripheral-loader.o",
    "subsys-pil-tz.o",
)


def locate(argv: list[str]) -> Path:
    if argv and argv[0] != "--self-test":
        root = Path(argv[0]).resolve()
    else:
        root = Path("gki/common").resolve()
    return root


def workspace(root: Path) -> Path:
    return root.parent.parent


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n", encoding="utf-8")


def set_config(path: Path, symbol: str) -> None:
    text = path.read_text(encoding="utf-8")
    enabled = f"CONFIG_{symbol}=y"
    disabled = f"# CONFIG_{symbol} is not set"
    lines = text.splitlines()
    if enabled in lines:
        return
    if disabled in lines:
        text = text.replace(disabled, enabled, 1)
    else:
        text = text.rstrip() + "\n" + enabled + "\n"
    path.write_text(text, encoding="utf-8")


def self_test() -> None:
    assert len(SOURCES) == len(set(SOURCES))
    assert len(OBJECTS) == len(set(OBJECTS))
    assert "subsys-pil-tz.c" in SOURCES
    assert "peripheral-loader.c" in SOURCES
    assert "subsystem_restart.c" in SOURCES
    assert CONFIGS == (
        "CONFIG_MSM_SUBSYSTEM_RESTART=y",
        "CONFIG_MSM_PIL=y",
        "CONFIG_MSM_PIL_SSR_GENERIC=y",
    )
    print("Phase 263 A615 ZAP PIL provider parity self-test: PASS", flush=True)


def apply(root: Path) -> None:
    ws = workspace(root)
    tg = ws / "workspace/touchgrass-a52xq/drivers/soc/qcom"
    cfg = ws / "workspace/gki-phase199-out/.config"
    if not root.is_dir():
        raise RuntimeError(f"Phase263 GKI root missing: {root}")
    if not tg.is_dir():
        raise RuntimeError(f"Phase263 TouchGrass qcom source missing: {tg}")
    if not cfg.is_file():
        raise RuntimeError(f"Phase263 build config missing: {cfg}")

    missing = [name for name in SOURCES if not (tg / name).is_file()]
    if missing:
        raise RuntimeError("Phase263 TouchGrass source closure missing: " + ", ".join(missing))

    dst = root / "drivers/a52_pil"
    dst.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        shutil.copy2(tg / name, dst / name)

    mk = f"""# {MARKER}\nccflags-y += -include $(srctree)/a52-port-compat.h\nccflags-y += -I$(srctree)/a52-compat/include\nccflags-y += -I$(srctree)/a52-compat/include/uapi\nobj-y += {' '.join(OBJECTS)}\n"""
    (dst / "Makefile").write_text(mk, encoding="utf-8")

    kc = f"""# {MARKER}\nconfig MSM_SUBSYSTEM_RESTART\n\tbool \"A52 legacy subsystem restart provider\"\n\tdefault y\n\nconfig MSM_PIL\n\tbool \"A52 legacy peripheral image loader\"\n\tdefault y\n\tselect FW_LOADER\n\nconfig MSM_PIL_SSR_GENERIC\n\tbool \"A52 legacy generic PIL/SSR provider\"\n\tdefault y\n\tdepends on MSM_PIL && MSM_SUBSYSTEM_RESTART\n"""
    (dst / "Kconfig").write_text(kc, encoding="utf-8")

    append_once(
        root / "drivers/Makefile",
        MARKER,
        f"# {MARKER}\nobj-y += a52_pil/",
    )
    append_once(
        root / "drivers/Kconfig",
        MARKER,
        f"# {MARKER}\nsource \"drivers/a52_pil/Kconfig\"",
    )

    for line in CONFIGS:
        set_config(cfg, line[len("CONFIG_"):].split("=", 1)[0])

    # Fail closed on the actual secure-ZAP contract we intend to restore.
    tz = (dst / "subsys-pil-tz.c").read_text(encoding="utf-8")
    sr = (dst / "subsystem_restart.c").read_text(encoding="utf-8")
    pl = (dst / "peripheral-loader.c").read_text(encoding="utf-8")
    if '"qcom,pil-tz-generic"' not in tz:
        raise RuntimeError("Phase263 imported provider lacks qcom,pil-tz-generic")
    if "subsys_register(&d->subsys_desc)" not in tz:
        raise RuntimeError("Phase263 imported provider lacks subsystem registration")
    if "void *subsystem_get(const char *name)" not in sr:
        raise RuntimeError("Phase263 imported SSR lacks subsystem_get provider")
    if "int pil_boot(struct pil_desc *desc)" not in pl:
        raise RuntimeError("Phase263 imported PIL lacks pil_boot provider")
    final = cfg.read_text(encoding="utf-8").splitlines()
    for line in CONFIGS:
        if line not in final:
            raise RuntimeError(f"Phase263 config did not apply: {line}")

    print(f"{MARKER}: Golden A615 ZAP PIL/SSR provider staged", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
