#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE430_HARDENING_V1"
SYSCALL = Path("arch/arm64/kernel/syscall.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    text = one(
        text,
        "u32 version;\n} __packed;\n\nstatic void *a52_p430_task_sideband;",
        "u32 version;\n};\n\nstatic void *a52_p430_task_sideband;",
        "natural-align Phase430 task record",
    )
    old = '''\t\ta52_ackfr_record("P430 AT0 t=%d ns=%llu", tgid,
\t\t\t(unsigned long long)ktime_get_boottime_ns());
\t}
}
EXPORT_SYMBOL_GPL(a52_p430_note_first_atomic);
'''
    new = '''\t\ta52_ackfr_record("P430 AT0 t=%d ns=%llu", tgid,
\t\t\t(unsigned long long)ktime_get_boottime_ns());
\t\tif (READ_ONCE(a52_p430_sampler))
\t\t\twake_up_process(READ_ONCE(a52_p430_sampler));
\t}
}
EXPORT_SYMBOL_GPL(a52_p430_note_first_atomic);
'''
    text = one(text, old, new, "wake endpoint sampler")
    return text + f"\n/* {MARK}: natural task alignment + atomic endpoint wake. */\n"


def patch_hwc(text: str) -> str:
    if MARK in text:
        return text
    old = '''\t__flush_dcache_area(d0, sizeof(*r));
\t__flush_dcache_area(d1, sizeof(*r));
\t__flush_dcache_area(d2, sizeof(*r));
}
'''
    new = '''\t__flush_dcache_area(d0, sizeof(*r));
\t__flush_dcache_area(d1, sizeof(*r));
\t__flush_dcache_area(d2, sizeof(*r));
\tdsb(sy);
}
'''
    text = one(text, old, new, "task three-copy persistence barrier")
    return text + f"\n/* {MARK}: task copies are globally ordered before execution continues. */\n"


def validate(root: Path) -> None:
    s = (root / SYSCALL).read_text(errors="replace")
    h = (root / HWC).read_text(errors="replace")
    for tok in (
        MARK,
        "u32 version;\n};\n\nstatic void *a52_p430_task_sideband;",
        "wake_up_process(READ_ONCE(a52_p430_sampler));",
        "BUILD_BUG_ON(sizeof(struct a52_p430_task_record) != A52_P430_SLOT_BYTES);",
    ):
        if tok not in s:
            raise SystemExit("Phase430 syscall hardening missing: " + tok)
    if "u32 version;\n} __packed;\n\nstatic void *a52_p430_task_sideband;" in s:
        raise SystemExit("Phase430 task record is still packed")
    if MARK not in h:
        raise SystemExit("Phase430 HWC hardening marker missing")
    anchor = '''\t__flush_dcache_area(d0, sizeof(*r));
\t__flush_dcache_area(d1, sizeof(*r));
\t__flush_dcache_area(d2, sizeof(*r));
\tdsb(sy);
'''
    if anchor not in h:
        raise SystemExit("Phase430 task-copy dsb(sy) missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if not ns.check_only:
        p = ns.root / SYSCALL
        p.write_text(patch_syscall(p.read_text(errors="replace")))
        p = ns.root / HWC
        p.write_text(patch_hwc(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase430 hardening: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
