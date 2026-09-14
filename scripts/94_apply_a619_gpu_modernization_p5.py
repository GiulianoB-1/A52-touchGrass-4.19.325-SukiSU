#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = "A619 GPU P5: Qualcomm 48fc67d2bcfb"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, repl: str, label: str, flags: int = 0) -> str:
    new, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one regex match, found {count}")
    return new


def patch_global_scratch_layout(root: Path) -> None:
    path = root / "drivers/gpu/msm/kgsl.h"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: global ringbuffer scratch layout")
        return

    pattern = r"(struct adreno_rb_shadow\s*\{.*?)(\n\};)"
    match = re.search(pattern, text, re.S)
    if not match:
        raise SystemExit("kgsl.h: struct adreno_rb_shadow not found")

    block = match.group(1)
    for required in ("rptr", "wptr"):
        if required not in block:
            raise SystemExit(f"kgsl.h: adreno_rb_shadow missing expected {required}")

    fields = f"""
	/* {MARKER}: page-table state moved out of per-RB allocations */
	u32 current_rb_ptname;
	u64 ttbr0;
	u32 contextidr;"""

    text = text[:match.start(2)] + fields + text[match.start(2):]

    if "SCRATCH_RB_OFFSET" not in text or "SCRATCH_RB_GPU_ADDR" not in text:
        raise SystemExit("kgsl.h: global scratch ringbuffer offset helpers are missing")

    path.write_text(text)
    print(f"[patched] {path}: added page-table fields to global RB scratch")


def patch_pagetable_helper(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno.h"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: scratch-backed pagetable helper")
        return

    pattern = (
        r"static inline void adreno_ringbuffer_set_global\(.*?\n\}\n\n"
        r"static inline void adreno_ringbuffer_set_pagetable\(.*?\n\}\n"
    )

    replacement = f"""/* {MARKER}
 * Store per-ringbuffer page-table state in the already privileged KGSL
 * scratch page instead of allocating a separate page for every ringbuffer.
 */
static inline void adreno_ringbuffer_set_pagetable(struct kgsl_device *device,
		struct adreno_ringbuffer *rb, struct kgsl_pagetable *pt)
{{
	unsigned long flags;

	spin_lock_irqsave(&rb->preempt_lock, flags);

	kgsl_sharedmem_writel(device->scratch,
		SCRATCH_RB_OFFSET(rb->id, current_rb_ptname), pt->name);

	kgsl_sharedmem_writeq(device->scratch,
		SCRATCH_RB_OFFSET(rb->id, ttbr0),
		kgsl_mmu_pagetable_get_ttbr0(pt));

	kgsl_sharedmem_writel(device->scratch,
		SCRATCH_RB_OFFSET(rb->id, contextidr), 0);

	spin_unlock_irqrestore(&rb->preempt_lock, flags);
}}
"""

    text = sub_once(text, pattern, replacement, "adreno pagetable helpers", re.S)
    path.write_text(text)
    print(f"[patched] {path}: page-table helper uses device scratch")


def remove_per_rb_allocation(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno_ringbuffer.c"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: per-RB pagetable allocation removed")
        return

    pattern = (
        r"\n\t/\*\s*\n"
        r"\t \* Allocate mem for storing RB pagetables and commands to\s*\n"
        r"\t \* switch pagetable\s*\n"
        r"\t \*/\s*\n"
        r"\tret = adreno_allocate_global\(device, &rb->pagetable_desc, PAGE_SIZE,\s*\n"
        r"\t\tSZ_16K, 0, KGSL_MEMDESC_PRIVILEGED, \"pagetable_desc\"\);\s*\n"
        r"\tif \(ret\)\s*\n"
        r"\t\treturn ret;\s*\n"
    )

    replacement = f"""
	/*
	 * {MARKER}
	 * Page-table metadata now lives in device->scratch. Avoid allocating
	 * one dedicated PAGE_SIZE object for each ringbuffer.
	 */
"""

    text = sub_once(text, pattern, replacement, "per-ringbuffer pagetable allocation", re.S)
    path.write_text(text)
    print(f"[patched] {path}: removed per-RB pagetable memory allocation")


def patch_active_context_reset(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno.c"
    text = path.read_text()

    if "SCRATCH_RB_OFFSET(rb->id, current_rb_ptname)" not in text:
        old_start = """void adreno_set_active_ctxs_null(struct adreno_device *adreno_dev)
{
	int i;
	struct adreno_ringbuffer *rb;
"""
        new_start = """void adreno_set_active_ctxs_null(struct adreno_device *adreno_dev)
{
	int i;
	struct adreno_ringbuffer *rb;
	struct kgsl_device *device = KGSL_DEVICE(adreno_dev);
"""
        text = replace_once(text, old_start, new_start, "active context device pointer")

        pattern = (
            r"kgsl_sharedmem_writel\(rb->pagetable_desc,\s*"
            r"PT_INFO_OFFSET\(current_rb_ptname\), 0\);"
        )
        replacement = (
            "kgsl_sharedmem_writel(device->scratch,\n"
            "\t\t\tSCRATCH_RB_OFFSET(rb->id, current_rb_ptname), 0);"
        )
        text = sub_once(text, pattern, replacement, "active context scratch reset", re.S)

    path.write_text(text)
    print(f"[patched] {path}: active-context reset uses global scratch")


def patch_preemption_file(path: Path) -> None:
    text = path.read_text()
    original = text

    pattern_q = (
        r"kgsl_sharedmem_readq\(next->pagetable_desc, &ttbr0,\s*"
        r"PT_INFO_OFFSET\(ttbr0\)\);"
    )
    repl_q = (
        "kgsl_sharedmem_readq(device->scratch, &ttbr0,\n"
        "\t\tSCRATCH_RB_OFFSET(next->id, ttbr0));"
    )
    text, qcount = re.subn(pattern_q, repl_q, text, flags=re.S)

    pattern_l = (
        r"kgsl_sharedmem_readl\(next->pagetable_desc, &contextidr,\s*"
        r"PT_INFO_OFFSET\(contextidr\)\);"
    )
    repl_l = (
        "kgsl_sharedmem_readl(device->scratch, &contextidr,\n"
        "\t\tSCRATCH_RB_OFFSET(next->id, contextidr));"
    )
    text, lcount = re.subn(pattern_l, repl_l, text, flags=re.S)

    text = text.replace(
        "adreno_ringbuffer_set_pagetable(rb,",
        "adreno_ringbuffer_set_pagetable(device, rb,",
    )

    if original == text:
        raise SystemExit(f"{path}: no legacy pagetable scratch references were patched")

    if qcount != 1 or lcount != 1:
        raise SystemExit(f"{path}: expected one ttbr0/contextidr read, got {qcount}/{lcount}")

    path.write_text(text)
    print(f"[patched] {path}: preemption reads page-table state from global scratch")


def patch_ringbuffer_switch(path: Path) -> None:
    text = path.read_text()
    original = text

    low_pattern = (
        r"lower_32_bits\(rb->pagetable_desc->gpuaddr \+\s*"
        r"PT_INFO_OFFSET\(ttbr0\)\)"
    )
    high_pattern = (
        r"upper_32_bits\(rb->pagetable_desc->gpuaddr \+\s*"
        r"PT_INFO_OFFSET\(ttbr0\)\)"
    )

    low_repl = "lower_32_bits(SCRATCH_RB_GPU_ADDR(device,\n\t\t\trb->id, ttbr0))"
    high_repl = "upper_32_bits(SCRATCH_RB_GPU_ADDR(device,\n\t\t\trb->id, ttbr0))"

    text, low_count = re.subn(low_pattern, low_repl, text, flags=re.S)
    text, high_count = re.subn(high_pattern, high_repl, text, flags=re.S)

    if original == text:
        raise SystemExit(f"{path}: pagetable switch scratch address anchor not found")
    if low_count != 1 or high_count != 1:
        raise SystemExit(f"{path}: expected one scratch address pair, got {low_count}/{high_count}")

    path.write_text(text)
    print(f"[patched] {path}: CP page-table write targets global scratch")


def remove_global_ptname_calls(root: Path) -> None:
    total = 0
    for name in ("adreno.c", "adreno_a6xx_gmu.c", "adreno_a6xx_rgmu.c"):
        path = root / "drivers/gpu/msm" / name
        if not path.exists():
            continue
        text = path.read_text()
        new, count = re.subn(
            r"\n\s*adreno_ringbuffer_set_global\(adreno_dev, 0\);\s*\n",
            "\n",
            text,
        )
        if count:
            path.write_text(new)
            total += count
            print(f"[patched] {path}: removed {count} obsolete global PT-name reset(s)")

    if total == 0:
        print("[audit] no obsolete adreno_ringbuffer_set_global calls remained")


def patch_all_set_pagetable_calls(root: Path) -> None:
    changed = 0
    for path in (root / "drivers/gpu/msm").glob("*.c"):
        text = path.read_text()
        if "adreno_ringbuffer_set_pagetable(rb," in text:
            new = text.replace(
                "adreno_ringbuffer_set_pagetable(rb,",
                "adreno_ringbuffer_set_pagetable(device, rb,",
            )
            path.write_text(new)
            changed += 1
            print(f"[patched] {path}: updated scratch helper call signature")
    if changed == 0:
        print("[audit] all adreno_ringbuffer_set_pagetable call sites already updated")


def audit(root: Path) -> None:
    msm = root / "drivers/gpu/msm"

    kgsl = (msm / "kgsl.h").read_text()
    for token in (
        MARKER,
        "current_rb_ptname",
        "u64 ttbr0;",
        "u32 contextidr;",
        "SCRATCH_RB_OFFSET",
        "SCRATCH_RB_GPU_ADDR",
    ):
        if token not in kgsl:
            raise SystemExit(f"kgsl scratch audit missing: {token}")

    adreno_h = (msm / "adreno.h").read_text()
    if MARKER not in adreno_h:
        raise SystemExit("adreno.h scratch helper marker missing")
    if "adreno_ringbuffer_set_global(" in adreno_h:
        raise SystemExit("obsolete adreno_ringbuffer_set_global helper remains")

    ring = (msm / "adreno_ringbuffer.c").read_text()
    if "&rb->pagetable_desc" in ring:
        raise SystemExit("per-ringbuffer pagetable allocation still present")

    leftovers = []
    for path in msm.glob("*.[ch]"):
        data = path.read_text(errors="ignore")
        if "->pagetable_desc" in data:
            leftovers.append(path.name)
        if "adreno_ringbuffer_set_global(" in data:
            leftovers.append(path.name + ":set_global")
    if leftovers:
        raise SystemExit("legacy pagetable scratch references remain: " + ", ".join(sorted(set(leftovers))))

    for name in ("adreno_a5xx_preempt.c", "adreno_a6xx_preempt.c"):
        path = msm / name
        data = path.read_text()
        for token in (
            "SCRATCH_RB_OFFSET(next->id, ttbr0)",
            "SCRATCH_RB_OFFSET(next->id, contextidr)",
            "adreno_ringbuffer_set_pagetable(device, rb,",
        ):
            if token not in data:
                raise SystemExit(f"{name}: missing {token}")

    for name in ("adreno_a5xx_ringbuffer.c", "adreno_a6xx_ringbuffer.c"):
        data = (msm / name).read_text()
        if "SCRATCH_RB_GPU_ADDR(device" not in data:
            raise SystemExit(f"{name}: global scratch GPU address not present")

    p4 = (msm / "adreno_a6xx_preempt.c").read_text()
    for token in (
        "A619 GPU P4: Qualcomm 96f7537ccfcd",
        "ADRENO_RB_SET_PSEUDO_DONE",
        "test_and_set_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags)",
    ):
        if token not in p4 and token != "ADRENO_RB_SET_PSEUDO_DONE":
            raise SystemExit(f"P4 preservation audit missing: {token}")

    ring_h = (msm / "adreno_ringbuffer.h").read_text()
    if "ADRENO_RB_SET_PSEUDO_DONE" not in ring_h:
        raise SystemExit("P4 ringbuffer pseudo-register flag was lost")
    if "unsigned long flags;" not in ring_h:
        raise SystemExit("P4 bitops-compatible ringbuffer flags storage was lost")

    gpulist = (msm / "adreno-gpulist.h").read_text()
    start = gpulist.find("adreno_gpu_core_a619")
    if start < 0:
        raise SystemExit("A619 core block not found")
    block = gpulist[start:start + 1800]
    if "ADRENO_PROCESS_RECLAIM" in block or "ADRENO_USE_SHMEM" in block:
        raise SystemExit("A619 reclaim feature unexpectedly enabled; do not port async reclaim")

    print("[audit] per-ringbuffer pagetable allocations removed")
    print("[audit] A5xx/A6xx preemption reads scratch-backed TTBR0/contextidr")
    print("[audit] A5xx/A6xx CP pagetable writes target global scratch")
    print("[audit] Phase4 pseudo-register optimization preserved")
    print("[audit] async GPU reclaim remains excluded for A619")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    patch_global_scratch_layout(root)
    patch_pagetable_helper(root)
    remove_per_rb_allocation(root)
    patch_active_context_reset(root)

    patch_preemption_file(root / "drivers/gpu/msm/adreno_a5xx_preempt.c")
    patch_preemption_file(root / "drivers/gpu/msm/adreno_a6xx_preempt.c")

    patch_ringbuffer_switch(root / "drivers/gpu/msm/adreno_a5xx_ringbuffer.c")
    patch_ringbuffer_switch(root / "drivers/gpu/msm/adreno_a6xx_ringbuffer.c")

    patch_all_set_pagetable_calls(root)
    remove_global_ptname_calls(root)
    audit(root)

    print("[done] A619 GPU modernization Phase5 applied")
    print("[source] Qualcomm 48fc67d2bcfb: get rid of per-ringbuffer scratch memory")
    print("[effect] page-table metadata now reuses privileged KGSL global scratch")
    print("[effect] removes one PAGE_SIZE pagetable allocation per ringbuffer (16 KiB total on Qualcomm layout)")
    print("[excluded] async reclaim a07afc4e1477: A619 lacks PROCESS_RECLAIM/USE_SHMEM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
