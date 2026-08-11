#!/usr/bin/env python3
"""Final semantic/header guard for the Phase 252 GKI 5.10 MSM-bus port.

Runs after the main 4.19 -> 5.10 API compatibility pass.  It closes two
compile/runtime traps that are easy to miss in a mechanical port:
  * timespec64 printf signedness/header ownership in both debug variants; and
  * command-db BCM aux-data endianness/address validity.

On the Phase253 branch this remains the final Phase252 guard and then dispatches
the Phase253 KGSL ARM-SMMU domain-contract correction onto the same generated
gki/common tree.  This keeps every Phase252 correction ordered before the new
SMMU domain semantics without changing the cumulative wrapper's earlier order.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MARKER = "A52_PHASE252_MSM_BUS_GKI510_FORMAT_GUARD_V1"
PHASE253 = Path(__file__).resolve().parent / "253_phase252_kgsl_smmu_domain_contract_overlay.py"
DEBUG_TIMEKEEPING_FILES = (
    "drivers/soc/qcom/msm_bus/msm_bus_dbg.c",
    "drivers/soc/qcom/msm_bus/msm_bus_dbg_rpmh.c",
)


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
        fabric = root / "drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
        if not fabric.is_file():
            continue
        if any(not (root / relative).is_file() for relative in DEBUG_TIMEKEEPING_FILES):
            continue
        if any(
            "ktime_to_timespec64(ktime_get())"
            not in (root / relative).read_text(encoding="utf-8")
            for relative in DEBUG_TIMEKEEPING_FILES
        ):
            continue
        fabric_text = fabric.read_text(encoding="utf-8")
        if "aux = cmd_db_read_aux_data(bcmdev->name, &aux_len);" not in fabric_text:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(f"expected one Phase252 compat-generated gki/common root, found {len(hits)}")
    return hits[0]


def patch_debug_timekeeping(root: Path) -> None:
    for relative in DEBUG_TIMEKEEPING_FILES:
        path = root / relative
        text = path.read_text(encoding="utf-8")

        if "#include <linux/ktime.h>\n" not in text:
            anchor = "#include <linux/hrtimer.h>\n"
            if text.count(anchor) != 1:
                raise RuntimeError(f"{path}: hrtimer include anchor drifted")
            text = text.replace(anchor, anchor + "#include <linux/ktime.h>\n", 1)

        old = '"\\n%lld.%09lu\\n",\n\t\t(long long)ts.tv_sec, ts.tv_nsec);'
        new = '"\\n%lld.%09ld\\n",\n\t\t(long long)ts.tv_sec, (long)ts.tv_nsec);'
        count = text.count(old)
        if count != 3:
            raise RuntimeError(
                f"{path}: expected exactly 3 signed-timespec64 format repair sites, found {count}"
            )
        text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")

        final = path.read_text(encoding="utf-8")
        if final.count("ktime_to_timespec64(ktime_get())") != 3:
            raise RuntimeError(f"Phase252 format guard lost timespec64 conversions: {relative}")
        if final.count('"\\n%lld.%09ld\\n"') != 3:
            raise RuntimeError(f"Phase252 format guard signed timestamp count mismatch: {relative}")
        if final.count("(long long)ts.tv_sec, (long)ts.tv_nsec") != 3:
            raise RuntimeError(f"Phase252 format guard explicit timestamp cast count mismatch: {relative}")
        if "%09lu" in final:
            raise RuntimeError(f"Phase252 format guard found stale unsigned tv_nsec format: {relative}")
        if final.count("#include <linux/ktime.h>") != 1:
            raise RuntimeError(f"Phase252 format guard ktime include count mismatch: {relative}")


def patch_cmddb_semantics(root: Path) -> None:
    path = root / "drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
    text = path.read_text(encoding="utf-8")

    old_struct = """struct bcm_db {
\tuint32_t unit_size;
\tuint16_t width;
\tuint8_t clk_domain;
\tuint8_t reserved;
};
"""
    new_struct = """struct bcm_db {
\t__le32 unit_size;
\t__le16 width;
\tu8 clk_domain;
\tu8 reserved;
};
"""
    if text.count(old_struct) != 1:
        raise RuntimeError(f"{path}: legacy native-endian bcm_db anchor drifted")
    text = text.replace(old_struct, new_struct, 1)

    old_assign = """\tbcmdev->addr = cmd_db_read_addr(bcmdev->name);
\tbcmdev->width = (uint32_t)aux_data.width;
\tbcmdev->clk_domain = aux_data.clk_domain;
\tbcmdev->unit_size = aux_data.unit_size;
"""
    new_assign = """\tbcmdev->addr = cmd_db_read_addr(bcmdev->name);
\tif (!bcmdev->addr) {
\t\tMSM_BUS_ERR(\"%s: Missing bcm address, bcm:%s\", __func__, bcmdev->name);
\t\tret = -ENXIO;
\t\tgoto exit_bcm_init;
\t}
\tbcmdev->width = le16_to_cpu(aux_data.width);
\tbcmdev->clk_domain = aux_data.clk_domain;
\tbcmdev->unit_size = le32_to_cpu(aux_data.unit_size);
"""
    if text.count(old_assign) != 1:
        raise RuntimeError(f"{path}: legacy native-endian bcm assignment anchor drifted")
    text = text.replace(old_assign, new_assign, 1)
    path.write_text(text, encoding="utf-8")

    final = path.read_text(encoding="utf-8")
    for token in (
        "__le32 unit_size;",
        "__le16 width;",
        "le16_to_cpu(aux_data.width)",
        "le32_to_cpu(aux_data.unit_size)",
        "Missing bcm address, bcm:%s",
        "if (!bcmdev->addr)",
    ):
        if token not in final:
            raise RuntimeError(f"Phase252 command-db semantic guard missing {token!r}")
    for stale in (
        "uint32_t unit_size;",
        "uint16_t width;",
        "bcmdev->width = (uint32_t)aux_data.width;",
        "bcmdev->unit_size = aux_data.unit_size;",
    ):
        if stale in final:
            raise RuntimeError(f"Phase252 command-db semantic guard found stale token {stale!r}")


def patch(root: Path) -> None:
    patch_debug_timekeeping(root)
    patch_cmddb_semantics(root)
    print(
        f"{MARKER}: both debug variants use signed timespec64 formatting/ktime headers; command-db BCM semantics verified",
        flush=True,
    )


def run_phase253(args: list[str]) -> int:
    if not PHASE253.is_file():
        raise RuntimeError(f"missing Phase253 overlay: {PHASE253}")
    result = subprocess.run([sys.executable, str(PHASE253), *args], check=False)
    if result.returncode:
        raise RuntimeError(f"Phase253 KGSL/SMMU domain-contract overlay failed rc={result.returncode}")
    return 0


def self_test() -> None:
    assert DEBUG_TIMEKEEPING_FILES == (
        "drivers/soc/qcom/msm_bus/msm_bus_dbg.c",
        "drivers/soc/qcom/msm_bus/msm_bus_dbg_rpmh.c",
    )
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        "gki/common",
        "msm_bus_dbg.c",
        "msm_bus_dbg_rpmh.c",
        "ktime_to_timespec64(ktime_get())",
        "%lld.%09ld",
        "(long long)ts.tv_sec, (long)ts.tv_nsec",
        "#include <linux/ktime.h>",
        "stale unsigned tv_nsec format",
        "__le32 unit_size",
        "__le16 width",
        "le16_to_cpu(aux_data.width)",
        "le32_to_cpu(aux_data.unit_size)",
        "Missing bcm address",
        "cmd_db_read_aux_data(bcmdev->name, &aux_len)",
        "253_phase252_kgsl_smmu_domain_contract_overlay.py",
    ):
        if token not in source:
            raise RuntimeError(f"Phase252/253 semantic-guard self-test missing {token!r}")
    print("Phase 252 MSM-bus GKI 5.10 semantic guard self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return run_phase253(["--self-test"])
    root = locate(sys.argv[1:])
    patch(root)
    return run_phase253([str(root)])


if __name__ == "__main__":
    raise SystemExit(main())
