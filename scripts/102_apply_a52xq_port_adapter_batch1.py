#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


def copy_first_named(root: Path, name: str, destination: Path) -> str:
    candidates = sorted(
        (path for path in root.rglob(name) if path.is_file()),
        key=lambda path: (len(path.parts), str(path)),
    )
    if not candidates:
        raise SystemExit(f"TouchGrass source does not contain {name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], destination)
    return str(candidates[0].relative_to(root))


def replace_required(path: Path, old: str, new: str, label: str) -> int:
    text = path.read_text(errors="replace")
    count = text.count(old)
    if count == 0:
        raise SystemExit(f"{label}: expected text not found in {path}")
    path.write_text(text.replace(old, new))
    return count


def patch_all(root: Path, replacements: tuple[tuple[str, str], ...]) -> dict[str, int]:
    counts = {old: 0 for old, _ in replacements}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".c", ".h"}:
            continue
        text = path.read_text(errors="replace")
        original = text
        for old, new in replacements:
            count = text.count(old)
            if count:
                counts[old] += count
                text = text.replace(old, new)
        if text != original:
            path.write_text(text)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--touchgrass", type=Path, required=True)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    touchgrass = args.touchgrass.resolve()
    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    compat = gki / "a52-compat/include"
    if not compat.is_dir():
        raise SystemExit("Workflow 101 compatibility include tree is missing")

    copied = {}
    copied["msm_ion.h"] = copy_first_named(
        touchgrass, "msm_ion.h", compat / "linux/msm_ion.h"
    )
    shutil.copy2(compat / "linux/msm_ion.h", compat / "uapi/linux/msm_ion.h")
    copied["dma-iommu.h"] = copy_first_named(
        touchgrass, "dma-iommu.h", compat / "asm/dma-iommu.h"
    )
    copied["trace_secure_buffer.h"] = copy_first_named(
        touchgrass,
        "trace_secure_buffer.h",
        gki / "drivers/a52_secure/trace_secure_buffer.h",
    )

    # This legacy include only provided the SZ_* constants. Android common 5.10
    # exposes them from linux/sizes.h and no longer ships asm/sizes.h.
    asm_sizes = compat / "asm/sizes.h"
    asm_sizes.parent.mkdir(parents=True, exist_ok=True)
    asm_sizes.write_text(
        "#ifndef _A52_COMPAT_ASM_SIZES_H\n"
        "#define _A52_COMPAT_ASM_SIZES_H\n"
        "#include <linux/sizes.h>\n"
        "#endif\n"
    )

    # The copied 4.19 bootmem header redefines memblock helpers already present
    # in 5.10. The downstream display code only needs the modern declarations.
    bootmem = compat / "linux/bootmem.h"
    bootmem.write_text(
        "#ifndef _A52_COMPAT_LINUX_BOOTMEM_H\n"
        "#define _A52_COMPAT_LINUX_BOOTMEM_H\n"
        "#include <linux/memblock.h>\n"
        "#endif\n"
    )

    # drm_can_sleep moved into the modern DRM core. Remove only the duplicate
    # fallback definition while preserving the rest of the old drmP aggregator.
    drmp = compat / "drm/drmP.h"
    drmp_text = drmp.read_text(errors="replace")
    drm_pattern = re.compile(
        r"/\* returns true if currently okay to sleep \*/\n"
        r"static inline bool drm_can_sleep\(void\)\n"
        r"\{.*?\n\}\n",
        re.S,
    )
    drmp_text, drm_count = drm_pattern.subn(
        "/* drm_can_sleep is supplied by Android common 5.10 DRM core. */\n",
        drmp_text,
    )
    if drm_count != 1:
        raise SystemExit(f"expected one drm_can_sleep fallback, found {drm_count}")
    drmp.write_text(drmp_text)

    compat_header = gki / "a52-port-compat.h"
    header_text = compat_header.read_text(errors="replace")
    header_anchor = "#define A52_PORT_COMPAT_H\n"
    additions = """#include <linux/interrupt.h>
#include <linux/timekeeping.h>
#include <linux/mm.h>
#include <linux/err.h>
#ifndef PTR_RET
#define PTR_RET(ptr) PTR_ERR_OR_ZERO(ptr)
#endif
#ifndef NR_UNRECLAIMABLE_PAGES
#define NR_UNRECLAIMABLE_PAGES NR_UNEVICTABLE
#endif
"""
    if additions not in header_text:
        if header_anchor not in header_text:
            raise SystemExit("compatibility header guard anchor missing")
        header_text = header_text.replace(header_anchor, header_anchor + additions, 1)
        compat_header.write_text(header_text)

    secure = gki / "drivers/a52_secure"
    smcinvoke = secure / "smcinvoke.c"
    replace_required(
        smcinvoke,
        '#include "../../misc/qseecom_kernel.h"',
        '#include "qseecom_kernel.h"',
        "SMCInvoke QSEECom header relocation",
    )

    secure_makefile = secure / "Makefile"
    secure_mk = secure_makefile.read_text(errors="replace")
    # Original TouchGrass builds qseecom.c as one object. compat_qseecom.c is not
    # a separate Kbuild object and directly depends on qseecom.c internals.
    secure_mk = secure_mk.replace(" compat_qseecom.o", "")
    secure_makefile.write_text(secure_mk)

    kgsl = gki / "drivers/gpu/msm"
    kgsl_device = kgsl / "kgsl_device.h"
    device_text = kgsl_device.read_text(errors="replace")
    helper_anchor = "static inline void kgsl_process_add_stats("
    helper = """/*
 * Android common 5.10 removed Qualcomm's MM_UNRECLAIMABLE mm counter.
 * Keep GPU allocation accounting local until the vendor counter is ported.
 */
static inline void a52_kgsl_account_unreclaimable(struct mm_struct *mm,
        long pages)
{
    (void)mm;
    (void)pages;
}

"""
    if helper not in device_text:
        if helper_anchor not in device_text:
            raise SystemExit("KGSL process-stat anchor missing")
        device_text = device_text.replace(helper_anchor, helper + helper_anchor, 1)
    device_text = device_text.replace(
        "add_mm_counter(current->mm, MM_UNRECLAIMABLE, (size >> PAGE_SHIFT));",
        "a52_kgsl_account_unreclaimable(current->mm, size >> PAGE_SHIFT);",
    )
    device_text = device_text.replace(
        "add_mm_counter(mm, MM_UNRECLAIMABLE,\n\t\t\t\t\t-(size >> PAGE_SHIFT));",
        "a52_kgsl_account_unreclaimable(mm, -(long)(size >> PAGE_SHIFT));",
    )
    if "MM_UNRECLAIMABLE" in device_text:
        raise SystemExit("unconverted MM_UNRECLAIMABLE use remains in kgsl_device.h")
    kgsl_device.write_text(device_text)

    kgsl_c = kgsl / "kgsl.c"
    text = kgsl_c.read_text(errors="replace")
    text = text.replace("\tmm_segment_t old_fs;\n", "")
    text = text.replace("\told_fs = get_fs();\n\tset_fs(get_ds());\n\n", "")
    text = text.replace("\tset_fs(old_fs);\n", "")
    text = text.replace(
        "vfs_read(fp, (char __user *)buf,", "kernel_read(fp, buf,"
    )
    if any(token in text for token in ("get_ds()", "get_fs()", "set_fs(")):
        raise SystemExit("removed address-limit API remains in kgsl.c")
    kgsl_c.write_text(text)

    mechanical = patch_all(
        kgsl,
        (
            ("->mmap_sem", "->mmap_lock"),
            (".mmap_sem", ".mmap_lock"),
            ("struct timespec ", "struct timespec64 "),
            ("getnstimeofday(", "ktime_get_real_ts64("),
            ("getboottime(", "ktime_get_boottime_ts64("),
        ),
    )

    # The display build copy was made before this adapter batch. Header shims are
    # global, but source-local adjustments must stay synchronized in both trees.
    display_source = gki / "techpack/display"
    display_build = gki / "drivers/a52_display"
    for relative in ("msm", "rotator", "pll"):
        if not (display_source / relative).exists() or not (display_build / relative).exists():
            raise SystemExit(f"display source/build subtree missing: {relative}")

    report = {
        "status": "adapter-batch-1-staged",
        "flashable": False,
        "hardware_validated": False,
        "copied_legacy_headers": copied,
        "shims": [
            "asm/sizes.h -> linux/sizes.h",
            "minimal linux/bootmem.h -> linux/memblock.h",
            "remove duplicate drm_can_sleep fallback",
            "legacy msm_ion.h and dma-iommu.h fallback",
            "secure-buffer trace header",
        ],
        "secure": {
            "smcinvoke_header_relocated": True,
            "compat_qseecom_separate_object_removed": True,
        },
        "kgsl": {
            "removed_set_fs_conversion": True,
            "mmap_sem_to_mmap_lock": mechanical["->mmap_sem"] + mechanical[".mmap_sem"],
            "timespec64_conversions": mechanical["struct timespec "],
            "time_api_conversions": mechanical["getnstimeofday("] + mechanical["getboottime("],
            "mm_unreclaimable_policy": "temporary local no-op accounting helper; no global mm counter alias",
        },
    }
    (output / "adapter-batch-1.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
