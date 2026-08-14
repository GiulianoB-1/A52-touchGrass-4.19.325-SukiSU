#!/usr/bin/env python3
"""Phase264: stage the Golden A52 FastRPC + PDR/service-registry kernel contract.

This is intentionally the first post-SurfaceFlinger peripheral parity layer.
The healthy A52 runtime exposes /dev/adsprpc-smd and runs adsprpcd/sscrpcd, so
upstream CONFIG_QCOM_FASTRPC is not an ABI substitute for the downstream
MSM_ADSPRPC driver used by the vendor image.

The script is cumulative on top of Phase263. It copies source only from the
pinned TouchGrass tree already restored by CI, isolates the legacy objects
under drivers/a52_fastrpc, and does not replace the modern QRTR/RPMSG core.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52_PHASE264_FASTRPC_PDR_PARITY_V1"
GOLDEN_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"

FASTRPC_FILES = (
    "adsprpc.c",
    "adsprpc_compat.c",
    "adsprpc_compat.h",
    "adsprpc_shared.h",
)

SERVICE_FILES = (
    "service-locator.c",
    "service-locator-private.h",
    "service-notifier.c",
    "service-notifier-private.h",
)

# Vendor-only headers used by the Golden FastRPC/PDR implementation.  Copy
# only when the reconstructed 5.10 tree does not already provide a header,
# so modern core interfaces remain authoritative where they already exist.
HEADER_CANDIDATES = (
    "include/linux/ipc_logging.h",
    "include/linux/msm_dma_iommu_mapping.h",
    "include/uapi/linux/msm_ion.h",
    "include/soc/qcom/secure_buffer.h",
    "include/soc/qcom/service-locator.h",
    "include/soc/qcom/service-notifier.h",
    "include/soc/qcom/sysmon.h",
    "include/trace/events/fastrpc.h",
)

CONFIGS = (
    "MSM_ADSPRPC",
    "A52_SERVICE_REGISTRY",
)


def locate(argv: list[str]) -> Path:
    if argv and argv[0] != "--self-test":
        return Path(argv[0]).resolve()
    return Path("gki/common").resolve()


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


def copy_required(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise RuntimeError(f"Phase264 required Golden file missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def stage_optional_header(golden: Path, root: Path, rel: str) -> None:
    src = golden / rel
    dst = root / rel
    if dst.is_file():
        return
    copy_required(src, dst)


def self_test() -> None:
    assert len(FASTRPC_FILES) == len(set(FASTRPC_FILES))
    assert len(SERVICE_FILES) == len(set(SERVICE_FILES))
    assert "adsprpc.c" in FASTRPC_FILES
    assert "service-locator.c" in SERVICE_FILES
    assert "service-notifier.c" in SERVICE_FILES
    assert "include/uapi/linux/msm_ion.h" in HEADER_CANDIDATES
    assert "include/trace/events/fastrpc.h" in HEADER_CANDIDATES
    assert CONFIGS == ("MSM_ADSPRPC", "A52_SERVICE_REGISTRY")
    print("Phase 264 FastRPC/PDR staging self-test: PASS", flush=True)


def apply(root: Path) -> None:
    ws = workspace(root)
    golden = ws / "workspace/touchgrass-a52xq"
    cfg = ws / "workspace/gki-phase199-out/.config"

    if not root.is_dir():
        raise RuntimeError(f"Phase264 GKI root missing: {root}")
    if not (golden / ".git").is_dir():
        raise RuntimeError(f"Phase264 pinned Golden checkout missing: {golden}")
    if not cfg.is_file():
        raise RuntimeError(f"Phase264 build config missing: {cfg}")

    dst = root / "drivers/a52_fastrpc"
    dst.mkdir(parents=True, exist_ok=True)

    for name in FASTRPC_FILES:
        copy_required(golden / "drivers/char" / name, dst / name)

    service_dst = dst / "services"
    for name in SERVICE_FILES:
        copy_required(golden / "drivers/soc/qcom" / name, service_dst / name)

    for rel in HEADER_CANDIDATES:
        stage_optional_header(golden, root, rel)

    # The downstream FastRPC source depends on the Phase263 SSR/PIL contract.
    for rel in (
        "include/soc/qcom/subsystem_notif.h",
        "include/soc/qcom/subsystem_restart.h",
        "include/soc/qcom/ramdump.h",
    ):
        if not (root / rel).is_file():
            copy_required(golden / rel, root / rel)

    mk = f"""# {MARKER}
ccflags-y += -include $(srctree)/a52-port-compat.h
ccflags-y += -I$(srctree)/a52-compat/include
ccflags-y += -I$(srctree)/a52-compat/include/uapi

obj-$(CONFIG_A52_SERVICE_REGISTRY) += a52_service_registry.o
a52_service_registry-y := services/service-locator.o services/service-notifier.o

obj-$(CONFIG_MSM_ADSPRPC) += a52_adsprpc.o
a52_adsprpc-y := adsprpc.o
ifeq ($(CONFIG_COMPAT),y)
a52_adsprpc-y += adsprpc_compat.o
endif
"""
    (dst / "Makefile").write_text(mk, encoding="utf-8")

    kc = f"""# {MARKER}
config A52_SERVICE_REGISTRY
    bool "A52 legacy Qualcomm service locator/notifier"
    default y
    depends on QRTR

config MSM_ADSPRPC
    bool "A52 legacy Qualcomm FastRPC vendor ABI"
    default y
    depends on RPMSG && A52_SERVICE_REGISTRY
"""
    (dst / "Kconfig").write_text(kc, encoding="utf-8")

    append_once(
        root / "drivers/Makefile",
        MARKER,
        f"# {MARKER}\nobj-y += a52_fastrpc/",
    )
    append_once(
        root / "drivers/Kconfig",
        MARKER,
        f"# {MARKER}\nsource \"drivers/a52_fastrpc/Kconfig\"",
    )

    for symbol in CONFIGS:
        set_config(cfg, symbol)

    adsprpc = (dst / "adsprpc.c").read_text(encoding="utf-8")
    for token in (
        'DEVICE_NAME',
        'FASTRPC_GLINK_GUID',
        'fastrpc_rpmsg_probe',
        'register_rpmsg_driver',
    ):
        if token not in adsprpc and token not in (dst / "adsprpc_shared.h").read_text(encoding="utf-8"):
            raise RuntimeError(f"Phase264 Golden FastRPC contract token missing: {token}")

    shared = (dst / "adsprpc_shared.h").read_text(encoding="utf-8")
    for token in ('DEVICE_NAME      "adsprpc-smd"', 'DEVICE_NAME_SECURE "adsprpc-smd-secure"'):
        if token not in shared:
            raise RuntimeError(f"Phase264 device-node ABI token missing: {token}")

    service = (service_dst / "service-locator.c").read_text(encoding="utf-8")
    if "AF_QIPCRTR" not in service or "qmi_handle" not in service:
        raise RuntimeError("Phase264 service locator is not the expected QRTR/QMI implementation")

    final = cfg.read_text(encoding="utf-8").splitlines()
    for symbol in CONFIGS:
        line = f"CONFIG_{symbol}=y"
        if line not in final:
            raise RuntimeError(f"Phase264 config did not apply: {line}")

    print(f"{MARKER}: Golden FastRPC/PDR vendor ABI staged", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
