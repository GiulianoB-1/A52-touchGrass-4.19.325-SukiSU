#!/usr/bin/env python3
"""Final narrow format/header guard for the Phase 252 GKI 5.10 MSM-bus port."""
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
        if not dbg.is_file():
            continue
        text = dbg.read_text(encoding="utf-8")
        if "ktime_to_timespec64(ktime_get())" not in text:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(f"expected one Phase252 compat-generated gki/common root, found {len(hits)}")
    return hits[0]


def patch(root: Path) -> None:
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

    print(f"{MARKER}: signed timespec64 formatting and ktime include verified", flush=True)


def self_test() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        "gki/common",
        "ktime_to_timespec64(ktime_get())",
        "%lld.%09ld",
        "(long long)ts.tv_sec, (long)ts.tv_nsec",
        "#include <linux/ktime.h>",
        "stale unsigned tv_nsec format",
    ):
        if token not in source:
            raise RuntimeError(f"Phase252 format-guard self-test missing {token!r}")
    print("Phase 252 MSM-bus GKI 5.10 format guard self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    patch(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
