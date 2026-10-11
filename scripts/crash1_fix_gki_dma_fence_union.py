#!/usr/bin/env python3
"""Fix the 4.19 SDE fence timestamp write for the Android 5.10 dma_fence union.

4.19 dma_fence stores cb_list and timestamp separately. Android 5.10 aliases
them. Writing fc->base.timestamp before dma_fence_is_signaled_locked destroys
the pending callback list and causes a kernel Oops in list_replace().

This is a narrowly scoped GKI-only port. It retains the exact SDE frame-event
timestamp via dma_fence_signal_timestamp_locked(), under the existing fence lock.
"""
import argparse
from pathlib import Path

MARK = "A52_GKI_DMA_FENCE_UNION_V1"
TARGET = "drivers/a52_display/msm/sde/sde_fence.c"
HEADER = "include/linux/dma-fence.h"
OLD = (
    "\t\tfc->base.timestamp = ts;\n"
    "\t\tis_signaled = dma_fence_is_signaled_locked(&fc->base);"
)
NEW = (
    "\t\t/* A52_GKI_DMA_FENCE_UNION_V1: cb_list aliases timestamp on 5.10.\n"
    "\t\t * Let the signal API detach callbacks BEFORE it writes timestamp.\n"
    "\t\t * Keep the original display frame-event timestamp.\n"
    "\t\t */\n"
    "\t\tis_signaled = test_bit(DMA_FENCE_FLAG_SIGNALED_BIT,\n"
    "\t\t\t\t\t       &fc->base.flags);\n"
    "\t\tif (!is_signaled && sde_fence_signaled(&fc->base))\n"
    "\t\t\tis_signaled =\n"
    "\t\t\t\tdma_fence_signal_timestamp_locked(&fc->base, ts) == 0;"
)


def check_layout(root):
    header = (root / HEADER).read_text()
    if "struct dma_fence {" not in header:
        raise RuntimeError("GKI dma_fence structure not found")
    snippet = header.split("struct dma_fence {", 1)[1].split("};", 1)[0]
    # The first end brace is that of the union in 5.10.
    if not all(item in snippet for item in ("union {", "struct list_head cb_list;", "ktime_t timestamp;")):
        raise RuntimeError("expected GKI dma_fence cb_list/timestamp union missing")


def check_source(source):
    if source.count(MARK) != 1 or source.count(NEW) != 1:
        raise RuntimeError("GKI SDE fence union fix absent or duplicated")
    if OLD in source or "fc->base.timestamp = ts;" in source:
        raise RuntimeError("unsafe pre-signal timestamp write remains")
    if "static bool sde_fence_signaled(struct dma_fence *fence)" not in source:
        raise RuntimeError("unexpected SDE fence readiness callback")
    print("A52_GKI_DMA_FENCE_UNION_V1 PASS: callback list intact until signal, frame timestamp preserved")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    opts = parser.parse_args()
    check_layout(opts.root)
    target = opts.root / TARGET
    source = target.read_text()
    if not opts.check_only and MARK not in source:
        if source.count(OLD) != 1:
            raise RuntimeError("expected exactly one legacy SDE pre-signal timestamp write, found %d" % source.count(OLD))
        source = source.replace(OLD, NEW, 1)
        target.write_text(source)
    check_source(source)


if __name__ == "__main__":
    main()
