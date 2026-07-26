#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SOURCE_SUFFIXES = {".c", ".h"}


def replace_required(path: Path, old: str, new: str, label: str) -> int:
    text = path.read_text(errors="replace")
    count = text.count(old)
    if count == 0:
        raise SystemExit(f"{label}: expected text not found in {path}")
    path.write_text(text.replace(old, new))
    return count


def replace_statements(path: Path, token: str, replacement: str, label: str) -> int:
    text = path.read_text(errors="replace")
    pattern = re.compile(
        r"mod_node_page_state\((?:(?!;).)*?" + re.escape(token) + r"(?:(?!;).)*?\);",
        re.S,
    )
    text, count = pattern.subn(replacement, text)
    if count == 0:
        raise SystemExit(f"{label}: no matching statements in {path}")
    path.write_text(text)
    return count


def patch_sources(root: Path) -> dict[str, int]:
    patterns = (
        (re.compile(r"\bstruct timespec\b"), "struct timespec64", "timespec64"),
        (re.compile(r"\bktime_to_timespec\("), "ktime_to_timespec64(", "ktime_to_timespec64"),
        (re.compile(r"\btimespec_to_jiffies\("), "timespec64_to_jiffies(", "timespec64_to_jiffies"),
        (re.compile(r"\bgetnstimeofday\("), "ktime_get_real_ts64(", "ktime_get_real_ts64"),
        (re.compile(r"\bgetboottime\("), "getboottime64(", "getboottime64"),
        (re.compile(r"\bkzfree\("), "kfree_sensitive(", "kfree_sensitive"),
    )
    counts = {label: 0 for _, _, label in patterns}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        text = path.read_text(errors="replace")
        original = text
        for pattern, replacement, label in patterns:
            text, count = pattern.subn(replacement, text)
            counts[label] += count
        if text != original:
            path.write_text(text)
    return counts


def add_global_compatibility(gki: Path) -> dict[str, bool]:
    path = gki / "a52-port-compat.h"
    text = path.read_text(errors="replace")
    anchor = "#define A52_PORT_COMPAT_H\n"
    block = """/* Workflow 104: native Android common 5.10 interface visibility. */
#include <linux/err.h>
#include <linux/interrupt.h>
#include <linux/jiffies.h>
#include <linux/mm.h>
#include <linux/timekeeping.h>
#ifndef PTR_RET
#define PTR_RET(ptr) PTR_ERR_OR_ZERO(ptr)
#endif
#define a52_kgsl_skip_unreclaimable_mm_accounting() do { } while (0)
#define a52_kgsl_skip_unreclaimable_node_accounting() do { } while (0)
"""
    if block not in text:
        if anchor not in text:
            raise SystemExit("A52 compatibility header guard anchor missing")
        text = text.replace(anchor, anchor + block, 1)
        path.write_text(text)
    final = path.read_text(errors="replace")
    return {
        "interrupt_header": "#include <linux/interrupt.h>" in final,
        "mm_header": "#include <linux/mm.h>" in final,
        "timekeeping_header": "#include <linux/timekeeping.h>" in final,
        "jiffies_header": "#include <linux/jiffies.h>" in final,
        "ptr_ret": "#define PTR_RET(ptr) PTR_ERR_OR_ZERO(ptr)" in final,
    }


def patch_kgsl(gki: Path) -> dict[str, int | bool]:
    kgsl = gki / "drivers/gpu/msm"
    device = kgsl / "kgsl_device.h"
    device_text = device.read_text(errors="replace")

    mm_patterns = (
        re.compile(
            r"add_mm_counter\(current->mm,\s*MM_UNRECLAIMABLE,\s*"
            r"\(size >> PAGE_SHIFT\)\);",
            re.S,
        ),
        re.compile(
            r"add_mm_counter\(mm,\s*MM_UNRECLAIMABLE,\s*"
            r"-\(size >> PAGE_SHIFT\)\);",
            re.S,
        ),
    )
    mm_count = 0
    for pattern in mm_patterns:
        device_text, count = pattern.subn(
            "a52_kgsl_skip_unreclaimable_mm_accounting();", device_text
        )
        mm_count += count
    if mm_count != 2:
        raise SystemExit(f"expected two KGSL MM_UNRECLAIMABLE calls, replaced {mm_count}")
    device.write_text(device_text)

    node_count = 0
    node_count += replace_statements(
        kgsl / "kgsl_sharedmem.c",
        "NR_UNRECLAIMABLE_PAGES",
        "a52_kgsl_skip_unreclaimable_node_accounting();",
        "KGSL shared-memory node accounting",
    )
    node_count += replace_statements(
        kgsl / "kgsl_pool.c",
        "NR_UNRECLAIMABLE_PAGES",
        "a52_kgsl_skip_unreclaimable_node_accounting();",
        "KGSL pool node accounting",
    )
    if node_count != 5:
        raise SystemExit(f"expected five KGSL node-accounting calls, replaced {node_count}")

    mmap_count = 0
    for path in kgsl.rglob("*"):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        text = path.read_text(errors="replace")
        count = text.count("mmap_sem")
        if count:
            path.write_text(text.replace("mmap_sem", "mmap_lock"))
            mmap_count += count
    if mmap_count == 0:
        raise SystemExit("no KGSL mmap_sem references were converted")

    kgsl_c = kgsl / "kgsl.c"
    text = kgsl_c.read_text(errors="replace")
    address_limit_counts = {
        "mm_segment_t": text.count("\tmm_segment_t old_fs;\n"),
        "get_set_fs": text.count("\told_fs = get_fs();\n\tset_fs(get_ds());\n\n"),
        "restore_fs": text.count("\tset_fs(old_fs);\n"),
        "vfs_read": text.count("vfs_read(fp, (char __user *)buf,"),
    }
    text = text.replace("\tmm_segment_t old_fs;\n", "")
    text = text.replace("\told_fs = get_fs();\n\tset_fs(get_ds());\n\n", "")
    text = text.replace("\tset_fs(old_fs);\n", "")
    text = text.replace("vfs_read(fp, (char __user *)buf,", "kernel_read(fp, buf,")

    old_fault = "static int\nkgsl_gpumem_vm_fault(struct vm_fault *vmf)"
    new_fault = "static vm_fault_t\nkgsl_gpumem_vm_fault(struct vm_fault *vmf)"
    fault_count = text.count(old_fault)
    if fault_count != 1:
        raise SystemExit(f"expected one KGSL VM-fault return declaration, found {fault_count}")
    text = text.replace(old_fault, new_fault, 1)
    kgsl_c.write_text(text)

    if not all(value > 0 for value in address_limit_counts.values()):
        raise SystemExit(f"incomplete address-limit conversion: {address_limit_counts}")

    return {
        "mm_accounting_calls_removed": mm_count,
        "node_accounting_calls_removed": node_count,
        "mmap_sem_to_mmap_lock": mmap_count,
        "address_limit_api_removed": True,
        "kernel_read_calls": address_limit_counts["vfs_read"],
        "vm_fault_return_converted": bool(fault_count),
    }


def assert_removed(roots: list[Path]) -> None:
    forbidden = (
        re.compile(r"\bstruct timespec\b"),
        re.compile(r"\bktime_to_timespec\("),
        re.compile(r"\btimespec_to_jiffies\("),
        re.compile(r"\bgetnstimeofday\("),
        re.compile(r"\bgetboottime\("),
        re.compile(r"\bkzfree\("),
    )
    failures: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            text = path.read_text(errors="replace")
            for pattern in forbidden:
                if pattern.search(text):
                    failures.append(f"{path}: {pattern.pattern}")
    if failures:
        raise SystemExit("old Phase 2A interfaces remain:\n" + "\n".join(failures[:100]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    global_checks = add_global_compatibility(gki)
    kgsl_report = patch_kgsl(gki)

    roots = [
        gki / "techpack/display",
        gki / "drivers/a52_display",
        gki / "drivers/gpu/msm",
        gki / "drivers/a52_secure",
    ]
    mechanical: dict[str, int] = {}
    for root in roots:
        counts = patch_sources(root)
        for key, value in counts.items():
            mechanical[key] = mechanical.get(key, 0) + value

    assert_removed(roots)

    kgsl_text = "\n".join(
        path.read_text(errors="replace")
        for path in (gki / "drivers/gpu/msm").rglob("*")
        if path.is_file() and path.suffix in SOURCE_SUFFIXES
    )
    for token in (
        "MM_UNRECLAIMABLE",
        "NR_UNRECLAIMABLE_PAGES",
        "mmap_sem",
        "get_ds(",
        "get_fs(",
        "set_fs(",
    ):
        if token in kgsl_text:
            raise SystemExit(f"removed KGSL interface remains: {token}")

    report = {
        "status": "phase2a-mm-time-irq-compatibility-staged",
        "flashable": False,
        "hardware_validated": False,
        "global_native_headers": global_checks,
        "kgsl": kgsl_report,
        "mechanical_conversions": mechanical,
        "scope": [
            "include native 5.10 MM, interrupt, tasklet, jiffies and timekeeping declarations",
            "convert old timespec users to timespec64 equivalents",
            "replace removed get_fs/set_fs file reads with kernel_read",
            "rename mmap_sem to mmap_lock",
            "convert KGSL VM fault callback to vm_fault_t",
            "remove obsolete Qualcomm unreclaimable vmstat accounting without aliasing unrelated counters",
            "replace kzfree with kfree_sensitive",
            "provide PTR_RET through PTR_ERR_OR_ZERO",
        ],
        "explicitly_deferred": [
            "DRM private mode flags and downstream display UAPI",
            "reservation object to dma_resv conversion",
            "clock and regulator semantic adapters",
            "PM QoS conversion",
            "IOMMU fault/domain attribute translation",
            "DMA and cache-maintenance translation",
            "QSEECom ioctl and shared-memory ownership",
        ],
    }
    (output / "phase2a-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
