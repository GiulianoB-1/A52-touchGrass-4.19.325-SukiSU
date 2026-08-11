#!/usr/bin/env python3
"""Phase 252: restore the TouchGrass legacy MSM-bus RPMh contract.

Phase251 hardware proves GMU reaches gmu_gpu_bw_probe() but
kgsl_get_bus_scale_table() returns NULL. The downstream msm-bus header makes
msm_bus_pdata_from_node()/msm_bus_cl_get_pdata() unconditional NULL stubs when
CONFIG_QCOM_BUS_SCALING is disabled. TouchGrass enables QCOM_BUS_SCALING and
QCOM_BUS_CONFIG_RPMH and builds the legacy drivers/soc/qcom/msm_bus stack.

This overlay imports that exact stack from the hardware-proven TouchGrass
commit and enables only the two matching Kconfig symbols. K251 diagnostics stay
intact so hardware can show the first post-fix divergence without forcing any
return value or fabric vote.
"""
from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOUCHGRASS_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
RAW_BASE = (
    "https://raw.githubusercontent.com/micr0softstore/"
    f"samsung_android_kernel_a52xq/{TOUCHGRASS_COMMIT}/"
)
MARKER = "A52_PHASE252_LEGACY_MSM_BUS_RPMH_V1"
PHASE251 = "A52_PHASE251_GMU_POST_MMIO_TAIL_DIAG_V1"
PHASE252_CONFIG_DELTA = frozenset((
    "CONFIG_QCOM_BUS_SCALING",
    "CONFIG_QCOM_BUS_CONFIG_RPMH",
))

BUS_FILES = (
    "drivers/soc/qcom/msm_bus/Makefile",
    "drivers/soc/qcom/msm_bus/msm_bus_adhoc.h",
    "drivers/soc/qcom/msm_bus/msm_bus_arb_adhoc.c",
    "drivers/soc/qcom/msm_bus/msm_bus_arb_rpmh.c",
    "drivers/soc/qcom/msm_bus/msm_bus_bimc.h",
    "drivers/soc/qcom/msm_bus/msm_bus_bimc_adhoc.c",
    "drivers/soc/qcom/msm_bus/msm_bus_bimc_rpmh.c",
    "drivers/soc/qcom/msm_bus/msm_bus_client_api.c",
    "drivers/soc/qcom/msm_bus/msm_bus_core.c",
    "drivers/soc/qcom/msm_bus/msm_bus_core.h",
    "drivers/soc/qcom/msm_bus/msm_bus_dbg.c",
    "drivers/soc/qcom/msm_bus/msm_bus_dbg_rpmh.c",
    "drivers/soc/qcom/msm_bus/msm_bus_fabric_adhoc.c",
    "drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c",
    "drivers/soc/qcom/msm_bus/msm_bus_noc.h",
    "drivers/soc/qcom/msm_bus/msm_bus_noc_adhoc.c",
    "drivers/soc/qcom/msm_bus/msm_bus_noc_rpmh.c",
    "drivers/soc/qcom/msm_bus/msm_bus_of.c",
    "drivers/soc/qcom/msm_bus/msm_bus_of_adhoc.c",
    "drivers/soc/qcom/msm_bus/msm_bus_of_rpmh.c",
    "drivers/soc/qcom/msm_bus/msm_bus_proxy_client.c",
    "drivers/soc/qcom/msm_bus/msm_bus_qnoc_adhoc.c",
    "drivers/soc/qcom/msm_bus/msm_bus_rpm_smd.c",
    "drivers/soc/qcom/msm_bus/msm_bus_rpmh.h",
    "drivers/soc/qcom/msm_bus/msm_bus_rules.c",
    "drivers/soc/qcom/msm_bus/msm_buspm_coresight_adhoc.c",
)

HEADER_FILES = (
    "include/linux/msm-bus.h",
    "include/linux/msm-bus-board.h",
    "include/linux/msm_bus_rules.h",
    "include/dt-bindings/msm/msm-bus-ids.h",
    "include/dt-bindings/msm/msm-bus-rule-ops.h",
    "include/trace/events/trace_msm_bus.h",
)

KCONFIG_BLOCK = f'''\n# {MARKER}\nconfig QCOM_BUS_SCALING\n\tbool "Bus scaling driver"\n\thelp\n\t  Restore the downstream Qualcomm legacy MSM bus client/provider API.\n\nconfig QCOM_BUS_CONFIG_RPMH\n\tbool "RPMH Bus scaling driver"\n\tdepends on QCOM_BUS_SCALING\n\thelp\n\t  Use the downstream RPMh/BCM implementation for legacy MSM bus votes.\n'''


def locate(args: list[str]) -> Path:
    base = Path.cwd()
    candidates: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = base / p
        candidates.extend((p, p.parent))
    candidates.extend((base / "workspace/gki-phase199-src", base / "gki/common"))
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidates:
        if not (root / "drivers/gpu/msm/kgsl_gmu.c").is_file():
            continue
        if not (root / "drivers/soc/qcom/Kconfig").is_file():
            continue
        gmu = (root / "drivers/gpu/msm/kgsl_gmu.c").read_text(encoding="utf-8")
        if "K251 B gpu tbl=%d" not in gmu:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(f"expected one generated Phase251 gki/common root, found {len(hits)}")
    return hits[0]


def fetch(relative: str) -> bytes:
    url = RAW_BASE + relative
    last: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": "A52-Phase252-pinned-port"})
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                data = response.read()
            if not data or b"404: Not Found" in data[:64]:
                raise RuntimeError(f"empty/not-found upstream file: {relative}")
            return data
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            last = exc
            if attempt != 3:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch pinned TouchGrass file {relative}: {last}")


def stage_snapshot(root: Path) -> None:
    for relative in BUS_FILES + HEADER_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch(relative))
        print(f"P252 staged {relative}", flush=True)


def patch_kbuild(root: Path) -> None:
    makefile = root / "drivers/soc/qcom/Makefile"
    text = makefile.read_text(encoding="utf-8")
    token = "obj-$(CONFIG_QCOM_BUS_SCALING) += msm_bus/"
    if token not in text:
        text = text.rstrip() + f"\n# {MARKER}\n{token}\n"
        makefile.write_text(text, encoding="utf-8")

    kconfig = root / "drivers/soc/qcom/Kconfig"
    text = kconfig.read_text(encoding="utf-8")
    if "config QCOM_BUS_SCALING" not in text:
        matches = list(re.finditer(r"(?m)^endmenu\s*$", text))
        if not matches:
            text = text.rstrip() + KCONFIG_BLOCK + "\n"
        else:
            pos = matches[-1].start()
            text = text[:pos] + KCONFIG_BLOCK + "\n" + text[pos:]
        kconfig.write_text(text, encoding="utf-8")
    if "config QCOM_BUS_CONFIG_RPMH" not in kconfig.read_text(encoding="utf-8"):
        raise RuntimeError("QCOM_BUS_CONFIG_RPMH Kconfig stanza missing after Phase252 patch")


def locate_config(root: Path) -> Path:
    candidates = (
        Path.cwd() / "workspace/gki-phase199-out/.config",
        root / ".config",
        Path.cwd() / "gki/common/.config",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("Phase252 authoritative GKI .config not found")


def set_builtin(config: Path, symbol: str) -> None:
    text = config.read_text(encoding="utf-8")
    lines = text.splitlines()
    prefixes = (f"{symbol}=", f"# {symbol} is not set")
    matches = [i for i, line in enumerate(lines) if line.startswith(prefixes)]
    if len(matches) > 1:
        raise RuntimeError(f"{config}: duplicate config state for {symbol}")
    if matches:
        lines[matches[0]] = f"{symbol}=y"
    else:
        lines.append(f"{symbol}=y")
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_config(path: Path) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            symbol, value = line.split("=", 1)
            states[symbol] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            states[line[2:-11]] = "n"
    return states


def repair_retention_snapshot(config: Path) -> None:
    """Teach the inherited byte-retention gate only the exact Phase252 delta."""
    snapshot = (
        Path.cwd()
        / "artifacts/a52xq-graphics-startup-trace/config/before-phase217.config"
    )
    if not snapshot.is_file():
        raise RuntimeError(f"Phase252 retention snapshot missing: {snapshot}")

    before = parse_config(snapshot)
    after = parse_config(config)
    changed = {
        symbol
        for symbol in set(before) | set(after)
        if before.get(symbol, "n") != after.get(symbol, "n")
    }
    if changed != PHASE252_CONFIG_DELTA:
        expected = ", ".join(sorted(PHASE252_CONFIG_DELTA))
        actual = ", ".join(sorted(changed)) or "<none>"
        raise RuntimeError(
            "Phase252 retention repair refused config drift; "
            f"expected exactly [{expected}], got [{actual}]"
        )
    for symbol in PHASE252_CONFIG_DELTA:
        if before.get(symbol, "n") != "n" or after.get(symbol) != "y":
            raise RuntimeError(
                f"Phase252 retention state mismatch for {symbol}: "
                f"before={before.get(symbol, 'n')} after={after.get(symbol, 'n')}"
            )

    snapshot.write_bytes(config.read_bytes())
    print(
        "Phase 252 retention snapshot updated for exact delta: "
        + ", ".join(sorted(PHASE252_CONFIG_DELTA)),
        flush=True,
    )


def audit(root: Path, config: Path) -> None:
    kconfig = (root / "drivers/soc/qcom/Kconfig").read_text(encoding="utf-8")
    makefile = (root / "drivers/soc/qcom/Makefile").read_text(encoding="utf-8")
    cfg = config.read_text(encoding="utf-8")
    header = (root / "include/linux/msm-bus.h").read_text(encoding="utf-8")
    busmk = (root / "drivers/soc/qcom/msm_bus/Makefile").read_text(encoding="utf-8")
    trace_header = root / "include/trace/events/trace_msm_bus.h"
    for token in (
        "config QCOM_BUS_SCALING",
        "config QCOM_BUS_CONFIG_RPMH",
    ):
        if token not in kconfig:
            raise RuntimeError(f"Phase252 Kconfig audit missing {token}")
    if "obj-$(CONFIG_QCOM_BUS_SCALING) += msm_bus/" not in makefile:
        raise RuntimeError("Phase252 qcom Makefile linkage missing")
    for token in ("CONFIG_QCOM_BUS_SCALING=y", "CONFIG_QCOM_BUS_CONFIG_RPMH=y"):
        if token not in cfg:
            raise RuntimeError(f"Phase252 config audit missing {token}")
    for token in (
        "defined(CONFIG_QCOM_BUS_SCALING)",
        "msm_bus_pdata_from_node",
        "msm_bus_cl_get_pdata",
        "msm_bus_scale_register_client",
    ):
        if token not in header:
            raise RuntimeError(f"Phase252 downstream msm-bus header missing {token}")
    for token in (
        "msm_bus_core.o msm_bus_client_api.o",
        "msm_bus_fabric_rpmh.o",
        "msm_bus_arb_rpmh.o",
        "msm_bus_of_rpmh.o",
    ):
        if token not in busmk:
            raise RuntimeError(f"Phase252 RPMh object closure missing {token}")
    if not trace_header.is_file() or "TRACE_SYSTEM msm_bus" not in trace_header.read_text(encoding="utf-8"):
        raise RuntimeError("Phase252 trace_msm_bus.h dependency missing")
    gmu = (root / "drivers/gpu/msm/kgsl_gmu.c").read_text(encoding="utf-8")
    if "K251 B gpu tbl=%d" not in gmu or "K251 G gpubw rc=%d" not in gmu:
        raise RuntimeError("Phase252 lost K251 GMU bandwidth diagnostics")


def self_test() -> None:
    assert TOUCHGRASS_COMMIT == "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
    assert "drivers/soc/qcom/msm_bus/msm_bus_core.c" in BUS_FILES
    assert "drivers/soc/qcom/msm_bus/msm_bus_of_rpmh.c" in BUS_FILES
    assert "include/linux/msm-bus.h" in HEADER_FILES
    assert "include/trace/events/trace_msm_bus.h" in HEADER_FILES
    assert PHASE252_CONFIG_DELTA == frozenset((
        "CONFIG_QCOM_BUS_SCALING",
        "CONFIG_QCOM_BUS_CONFIG_RPMH",
    ))
    assert "config QCOM_BUS_SCALING" in KCONFIG_BLOCK
    assert "config QCOM_BUS_CONFIG_RPMH" in KCONFIG_BLOCK
    print("Phase 252 legacy MSM-bus RPMh overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    stage_snapshot(root)
    patch_kbuild(root)
    config = locate_config(root)
    set_builtin(config, "CONFIG_QCOM_BUS_SCALING")
    set_builtin(config, "CONFIG_QCOM_BUS_CONFIG_RPMH")
    repair_retention_snapshot(config)
    audit(root, config)
    print(f"{MARKER}: pinned TouchGrass legacy MSM-bus RPMh stack applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
