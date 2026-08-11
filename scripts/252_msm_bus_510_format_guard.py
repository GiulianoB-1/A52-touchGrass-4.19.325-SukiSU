#!/usr/bin/env python3
"""Final semantic/header guard for the Phase 252 GKI 5.10 MSM-bus port.

Runs after the main 4.19 -> 5.10 API compatibility pass.  It closes two
compile/runtime traps that are easy to miss in a mechanical port:
  * timespec64 printf signedness/header ownership; and
  * command-db BCM aux-data endianness/address validity.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE252_MSM_BUS_GKI510_FORMAT_GUARD_V1"


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
        dbg = root / "drivers/soc/qcom/msm_bus/msm_bus_dbg_rpmh.c"
        fabric = root / "drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
        if not dbg.is_file() or not fabric.is_file():
            continue
        dbg_text = dbg.read_text(encoding="utf-8")
        fabric_text = fabric.read_text(encoding="utf-8")
        if "ktime_to_timespec64(ktime_get())" not in dbg_text:
            continue
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
    path = root / "drivers/soc/qcom/msm_bus/msm_bus_dbg_rpmh.c"
    text = path.read_text(encoding="utf-8")

    # Avoid relying on the hrtimer include chain for the 5.10 ktime conversion API.
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
        raise RuntimeError("Phase252 format guard lost timespec64 conversions")
    if final.count('"\\n%lld.%09ld\\n"') != 3:
        raise RuntimeError("Phase252 format guard signed timestamp count mismatch")
    if final.count("(long long)ts.tv_sec, (long)ts.tv_nsec") != 3:
        raise RuntimeError("Phase252 format guard explicit timestamp cast count mismatch")
    if "%09lu" in final:
        raise RuntimeError("Phase252 format guard found stale unsigned tv_nsec format")
    if final.count("#include <linux/ktime.h>") != 1:
        raise RuntimeError("Phase252 format guard ktime include count mismatch")


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
        f"{MARKER}: signed timespec64 formatting, ktime include, and command-db BCM semantics verified",
        flush=True,
    )


def self_test() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        "gki/common",
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
    ):
        if token not in source:
            raise RuntimeError(f"Phase252 semantic-guard self-test missing {token!r}")
    print("Phase 252 MSM-bus GKI 5.10 semantic guard self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    patch(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
