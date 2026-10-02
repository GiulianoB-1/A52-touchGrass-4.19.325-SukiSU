#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE430_HARDENING_V2"
SYSCALL = Path("arch/arm64/kernel/syscall.c")
HWC = Path("drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)


def patch_syscall(text: str) -> str:
    packed = "u32 version;\n} __packed;\n\nstatic void *a52_p430_task_sideband;"
    natural = "u32 version;\n};\n\nstatic void *a52_p430_task_sideband;"
    if packed in text:
        text = one(text, packed, natural, "natural-align Phase430 task record")
    elif natural not in text:
        raise SystemExit("Phase430 task-record alignment anchor missing")

    wake = "wake_up_process(READ_ONCE(a52_p430_sampler));"
    if wake not in text:
        old = """\t\ta52_ackfr_record("P430 AT0 t=%d ns=%llu", tgid,
\t\t\t(unsigned long long)ktime_get_boottime_ns());
\t}
}
EXPORT_SYMBOL_GPL(a52_p430_note_first_atomic);
"""
        new = """\t\ta52_ackfr_record("P430 AT0 t=%d ns=%llu", tgid,
\t\t\t(unsigned long long)ktime_get_boottime_ns());
\t\tif (READ_ONCE(a52_p430_sampler))
\t\t\twake_up_process(READ_ONCE(a52_p430_sampler));
\t}
}
EXPORT_SYMBOL_GPL(a52_p430_note_first_atomic);
"""
        text = one(text, old, new, "wake endpoint sampler")

    task_barrier = """\t__flush_dcache_area(d0, sizeof(*r));
\t__flush_dcache_area(d1, sizeof(*r));
\t__flush_dcache_area(d2, sizeof(*r));
\tdsb(sy);
"""
    if task_barrier not in text:
        old = """\t__flush_dcache_area(d0, sizeof(*r));
\t__flush_dcache_area(d1, sizeof(*r));
\t__flush_dcache_area(d2, sizeof(*r));
"""
        text = one(text, old, task_barrier, "task three-copy persistence barrier")

    if MARK not in text:
        text += f"\n/* {MARK}: natural task alignment + atomic endpoint wake + task-copy dsb. */\n"
    return text


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

    task_barrier = """\t__flush_dcache_area(d0, sizeof(*r));
\t__flush_dcache_area(d1, sizeof(*r));
\t__flush_dcache_area(d2, sizeof(*r));
\tdsb(sy);
"""
    if task_barrier not in s:
        raise SystemExit("Phase430 task-copy dsb(sy) missing from syscall.c")

    dma_barrier = """\t__flush_dcache_area(d0, A52_P430_DMA_COPY_BYTES);
\t__flush_dcache_area(d1, A52_P430_DMA_COPY_BYTES);
\t__flush_dcache_area(d2, A52_P430_DMA_COPY_BYTES);
\tdsb(sy);
"""
    if dma_barrier not in h:
        raise SystemExit("Phase430 DMA-copy dsb(sy) missing from dsi_ctrl_hw_cmn.c")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if not ns.check_only:
        p = ns.root / SYSCALL
        p.write_text(patch_syscall(p.read_text(errors="replace")))
    validate(ns.root)
    print("Phase430 hardening v2: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
