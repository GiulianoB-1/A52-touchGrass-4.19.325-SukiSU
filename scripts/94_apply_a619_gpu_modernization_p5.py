#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = "A619 GPU P5: Qualcomm 48fc67d2bcfb"
MSM = Path("drivers/gpu/msm")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, repl: str, label: str, flags: int = 0) -> str:
    out, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one regex match, found {count}")
    return out


def patch_scratch_layout(root: Path) -> None:
    path = root / MSM / "kgsl.h"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: scratch shadow layout")
        return

    old = """/* Shadow global helpers */
#define SCRATCH_RPTR_OFFSET(id) ((id) * sizeof(unsigned int))
#define SCRATCH_RPTR_GPU_ADDR(dev, id) \\
\t((dev)->scratch.gpuaddr + SCRATCH_RPTR_OFFSET(id))
"""

    new = f"""/*
 * {MARKER}
 *
 * The legacy Samsung/Qualcomm tree stored page-table switch state in one
 * separately allocated PAGE_SIZE object per ringbuffer.  Newer Qualcomm KGSL
 * folds that state into the already privileged global scratch page.
 *
 * Keep rptr as the first field so all existing RPTR users can move to the
 * same per-ringbuffer shadow without changing their semantics.
 */
struct adreno_rb_shadow {{
\tu32 rptr;
\tu32 current_rb_ptname;
\tu64 ttbr0;
\tu32 contextidr;
}};

#define SCRATCH_RB_OFFSET(id, field) \\
\t(((id) * sizeof(struct adreno_rb_shadow)) + \\
\t offsetof(struct adreno_rb_shadow, field))

#define SCRATCH_RB_GPU_ADDR(dev, id, field) \\
\t((dev)->scratch.gpuaddr + SCRATCH_RB_OFFSET(id, field))

#define SCRATCH_RPTR_OFFSET(id) SCRATCH_RB_OFFSET(id, rptr)
#define SCRATCH_RPTR_GPU_ADDR(dev, id) \\
\tSCRATCH_RB_GPU_ADDR(dev, id, rptr)
"""

    text = replace_once(text, old, new, "kgsl scratch helpers")
    path.write_text(text)
    print(f"[patched] {path}: expanded global scratch to per-RB shadow state")


def patch_ringbuffer_struct(root: Path) -> None:
    path = root / MSM / "adreno_ringbuffer.h"
    text = path.read_text()

    # Keep the old pagetable-info type harmlessly for source compatibility, but
    # remove the actual per-RB memory descriptor.  No remaining code may use it.
    old = "\tstruct kgsl_memdesc pagetable_desc;\n"
    if old in text:
        text = replace_once(text, old, "", "ringbuffer pagetable_desc field")
    elif "struct kgsl_memdesc pagetable_desc;" not in text:
        print(f"[already] {path}: pagetable_desc field removed")
    else:
        raise SystemExit("adreno_ringbuffer.h: unexpected pagetable_desc layout")

    path.write_text(text)
    print(f"[patched] {path}: removed per-ringbuffer pagetable memdesc")


def patch_pagetable_helper(root: Path) -> None:
    path = root / MSM / "adreno.h"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: scratch-backed pagetable helper")
        return

    pattern = (
        r"static inline void adreno_ringbuffer_set_global\(.*?\n\}\n\n"
        r"static inline void adreno_ringbuffer_set_pagetable\(.*?\n\}\n"
    )

    replacement = f"""/*
 * {MARKER}
 * Store per-ringbuffer page-table state in device->scratch instead of a
 * dedicated PAGE_SIZE allocation for every ringbuffer.
 */
static inline void adreno_ringbuffer_set_pagetable(struct kgsl_device *device,
\t\tstruct adreno_ringbuffer *rb, struct kgsl_pagetable *pt)
{{
\tunsigned long flags;

\tspin_lock_irqsave(&rb->preempt_lock, flags);

\tkgsl_sharedmem_writel(device, &device->scratch,
\t\tSCRATCH_RB_OFFSET(rb->id, current_rb_ptname), pt->name);

\tkgsl_sharedmem_writeq(device, &device->scratch,
\t\tSCRATCH_RB_OFFSET(rb->id, ttbr0),
\t\tkgsl_mmu_pagetable_get_ttbr0(pt));

\tkgsl_sharedmem_writel(device, &device->scratch,
\t\tSCRATCH_RB_OFFSET(rb->id, contextidr),
\t\tkgsl_mmu_pagetable_get_contextidr(pt));

\tspin_unlock_irqrestore(&rb->preempt_lock, flags);
}}
"""

    text = sub_once(text, pattern, replacement, "legacy pagetable helpers", re.S)
    path.write_text(text)
    print(f"[patched] {path}: page-table helper now uses device scratch")


def remove_per_rb_allocation(root: Path) -> None:
    path = root / MSM / "adreno_ringbuffer.c"
    text = path.read_text()

    alloc_pattern = (
        r"\n\t/\*\n"
        r"\t \* Allocate mem for storing RB pagetables and commands to\n"
        r"\t \* switch pagetable\n"
        r"\t \*/\n"
        r"\tret = kgsl_allocate_global\(KGSL_DEVICE\(adreno_dev\), &rb->pagetable_desc,\n"
        r"\t\tPAGE_SIZE, 0, KGSL_MEMDESC_PRIVILEGED, \"pagetable_desc\"\);\n"
        r"\tif \(ret\)\n"
        r"\t\treturn ret;\n"
    )

    if re.search(alloc_pattern, text):
        text = re.sub(
            alloc_pattern,
            f"""
\t/*
\t * {MARKER}
\t * Page-table state now reuses the privileged device scratch page.
\t */
""",
            text,
            count=1,
        )
    elif "&rb->pagetable_desc" in text:
        raise SystemExit("adreno_ringbuffer.c: legacy allocation shape changed")

    text = text.replace("\tkgsl_free_global(device, &rb->pagetable_desc);\n", "")
    path.write_text(text)
    print(f"[patched] {path}: removed per-RB allocation/free")


def patch_active_context_reset(root: Path) -> None:
    path = root / MSM / "adreno.c"
    text = path.read_text()

    old_start = """static void adreno_set_active_ctxs_null(struct adreno_device *adreno_dev)
{
\tint i;
\tstruct adreno_ringbuffer *rb;
"""
    new_start = """static void adreno_set_active_ctxs_null(struct adreno_device *adreno_dev)
{
\tint i;
\tstruct adreno_ringbuffer *rb;
\tstruct kgsl_device *device = KGSL_DEVICE(adreno_dev);
"""
    if old_start in text:
        text = replace_once(text, old_start, new_start, "active context device pointer")

    old_write = """\t\tkgsl_sharedmem_writel(KGSL_DEVICE(adreno_dev),
\t\t\t&rb->pagetable_desc, PT_INFO_OFFSET(current_rb_ptname),
\t\t\t0);
"""
    new_write = """\t\tkgsl_sharedmem_writel(device, &device->scratch,
\t\t\tSCRATCH_RB_OFFSET(rb->id, current_rb_ptname), 0);
"""
    if old_write in text:
        text = replace_once(text, old_write, new_write, "active context reset")
    elif "SCRATCH_RB_OFFSET(rb->id, current_rb_ptname)" not in text:
        raise SystemExit("adreno.c: active context pagetable reset anchor not found")

    # The old global PT-name slot existed only in ringbuffers[0].  Once the
    # per-RB state is in scratch there is no separate global slot to clear.
    text, removed = re.subn(
        r"\n\s*adreno_ringbuffer_set_global\(adreno_dev, 0\);\n",
        "\n",
        text,
    )
    if removed not in (0, 2):
        raise SystemExit(f"adreno.c: expected 0 or 2 set_global resets, found {removed}")

    path.write_text(text)
    print(f"[patched] {path}: active-context reset uses global scratch; obsolete global resets removed")


def patch_preemption_file(path: Path) -> None:
    text = path.read_text()

    text, qcount = re.subn(
        r"kgsl_sharedmem_readq\(&next->pagetable_desc, &ttbr0,\s*"
        r"PT_INFO_OFFSET\(ttbr0\)\);",
        "kgsl_sharedmem_readq(&device->scratch, &ttbr0,\\n"
        "\\t\\tSCRATCH_RB_OFFSET(next->id, ttbr0));",
        text,
        flags=re.S,
    )
    text, lcount = re.subn(
        r"kgsl_sharedmem_readl\(&next->pagetable_desc, &contextidr,\s*"
        r"PT_INFO_OFFSET\(contextidr\)\);",
        "kgsl_sharedmem_readl(&device->scratch, &contextidr,\\n"
        "\\t\\tSCRATCH_RB_OFFSET(next->id, contextidr));",
        text,
        flags=re.S,
    )

    call_count = text.count("adreno_ringbuffer_set_pagetable(rb,")
    text = text.replace(
        "adreno_ringbuffer_set_pagetable(rb,",
        "adreno_ringbuffer_set_pagetable(device, rb,",
    )

    if qcount not in (0, 1) or lcount not in (0, 1):
        raise SystemExit(f"{path}: unexpected scratch read counts {qcount}/{lcount}")
    if qcount == 0 and "SCRATCH_RB_OFFSET(next->id, ttbr0)" not in text:
        raise SystemExit(f"{path}: TTBR0 scratch read not found")
    if lcount == 0 and "SCRATCH_RB_OFFSET(next->id, contextidr)" not in text:
        raise SystemExit(f"{path}: CONTEXTIDR scratch read not found")
    if call_count == 0 and "adreno_ringbuffer_set_pagetable(device, rb," not in text:
        raise SystemExit(f"{path}: set_pagetable call not found")

    # Fix the now-stale explanatory comment.
    text = text.replace(
        "The pagetable_desc is allocated and mapped at probe time, and\\n"
        "\\t * preemption_desc at init time, so no need to check if\\n"
        "\\t * sharedmem accesses to these memdescs succeed.",
        "The per-ringbuffer page-table state lives in the privileged global\\n"
        "\\t * scratch page, while preemption_desc is allocated at init time.",
    )

    path.write_text(text)
    print(f"[patched] {path}: preemption reads TTBR0/contextidr from global scratch")


def patch_iommu_cp_writes(root: Path) -> None:
    path = root / MSM / "adreno_iommu.c"
    text = path.read_text()

    pattern = (
        r"cmds \+= cp_gpuaddr\(adreno_dev, cmds, \(rb->pagetable_desc\.gpuaddr \+\s*"
        r"PT_INFO_OFFSET\(ttbr0\)\)\);"
    )
    replacement = (
        "cmds += cp_gpuaddr(adreno_dev, cmds,\\n"
        "\\t\\tSCRATCH_RB_GPU_ADDR(device, rb->id, ttbr0));"
    )

    text, count = re.subn(pattern, replacement, text, flags=re.S)
    if count == 0:
        existing = text.count("SCRATCH_RB_GPU_ADDR(device, rb->id, ttbr0)")
        if existing != 2:
            raise SystemExit(
                f"adreno_iommu.c: expected 2 scratch CP write targets, found {existing}"
            )
    elif count != 2:
        raise SystemExit(f"adreno_iommu.c: expected 2 legacy CP write targets, found {count}")

    path.write_text(text)
    print(f"[patched] {path}: A5xx/A6xx CP page-table writes target global scratch")


def audit(root: Path) -> None:
    msm = root / MSM

    kgsl = (msm / "kgsl.h").read_text()
    for token in (
        MARKER,
        "struct adreno_rb_shadow",
        "u32 rptr;",
        "u32 current_rb_ptname;",
        "u64 ttbr0;",
        "u32 contextidr;",
        "SCRATCH_RB_OFFSET",
        "SCRATCH_RB_GPU_ADDR",
        "SCRATCH_RPTR_OFFSET(id) SCRATCH_RB_OFFSET(id, rptr)",
    ):
        if token not in kgsl:
            raise SystemExit(f"kgsl.h audit missing: {token}")

    ring_h = (msm / "adreno_ringbuffer.h").read_text()
    if "struct kgsl_memdesc pagetable_desc;" in ring_h:
        raise SystemExit("adreno_ringbuffer.h: pagetable_desc field remains")
    if "ADRENO_RB_SET_PSEUDO_DONE" not in ring_h:
        raise SystemExit("P4 ringbuffer pseudo-register flag was lost")

    adreno_h = (msm / "adreno.h").read_text()
    for token in (
        MARKER,
        "adreno_ringbuffer_set_pagetable(struct kgsl_device *device,",
        "SCRATCH_RB_OFFSET(rb->id, current_rb_ptname)",
        "SCRATCH_RB_OFFSET(rb->id, ttbr0)",
        "SCRATCH_RB_OFFSET(rb->id, contextidr)",
    ):
        if token not in adreno_h:
            raise SystemExit(f"adreno.h audit missing: {token}")
    if "adreno_ringbuffer_set_global(" in adreno_h:
        raise SystemExit("obsolete adreno_ringbuffer_set_global helper remains")

    ring_c = (msm / "adreno_ringbuffer.c").read_text()
    if "pagetable_desc" in ring_c:
        raise SystemExit("adreno_ringbuffer.c: per-RB pagetable allocation/free remains")

    adreno_c = (msm / "adreno.c").read_text()
    if "SCRATCH_RB_OFFSET(rb->id, current_rb_ptname)" not in adreno_c:
        raise SystemExit("adreno.c: scratch-backed active context reset missing")
    if "adreno_ringbuffer_set_global(" in adreno_c:
        raise SystemExit("adreno.c: obsolete set_global reset remains")

    for name in ("adreno_a5xx_preempt.c", "adreno_a6xx_preempt.c"):
        data = (msm / name).read_text()
        for token in (
            "SCRATCH_RB_OFFSET(next->id, ttbr0)",
            "SCRATCH_RB_OFFSET(next->id, contextidr)",
            "adreno_ringbuffer_set_pagetable(device, rb,",
        ):
            if token not in data:
                raise SystemExit(f"{name}: missing {token}")
        if "->pagetable_desc" in data:
            raise SystemExit(f"{name}: legacy pagetable_desc reference remains")

    iommu = (msm / "adreno_iommu.c").read_text()
    if iommu.count("SCRATCH_RB_GPU_ADDR(device, rb->id, ttbr0)") != 2:
        raise SystemExit("adreno_iommu.c: expected A5xx and A6xx scratch CP targets")
    if "rb->pagetable_desc" in iommu:
        raise SystemExit("adreno_iommu.c: legacy pagetable_desc GPU address remains")

    leftovers = []
    for path in msm.glob("*.[ch]"):
        data = path.read_text(errors="ignore")
        if "->pagetable_desc" in data:
            leftovers.append(path.name)
        if "adreno_ringbuffer_set_global(" in data:
            leftovers.append(path.name + ":set_global")
    if leftovers:
        raise SystemExit(
            "legacy pagetable scratch references remain: "
            + ", ".join(sorted(set(leftovers)))
        )

    # Scratch layout must remain safely below the existing KMD postamble area.
    # Four legacy priority ringbuffers * 24-byte shadow = 96 bytes, while the
    # postamble begins at 800 bytes.
    if "SCRATCH_POSTAMBLE_OFFSET (100 * sizeof(u64))" not in kgsl:
        raise SystemExit("unexpected legacy scratch postamble layout")

    p4 = (msm / "adreno_a6xx_preempt.c").read_text()
    for token in (
        "A619 GPU P4: Qualcomm 96f7537ccfcd",
        "test_and_set_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags)",
    ):
        if token not in p4:
            raise SystemExit(f"P4 preservation audit missing: {token}")

    print("[audit] legacy Samsung 4.19 KGSL layout adapted directly")
    print("[audit] per-ringbuffer PAGE_SIZE pagetable allocations removed")
    print("[audit] RPTR + current PT + TTBR0 + CONTEXTIDR share device scratch")
    print("[audit] A5xx/A6xx preemption reads global scratch")
    print("[audit] A5xx/A6xx CP page-table writes target global scratch")
    print("[audit] scratch shadow remains well below KMD postamble offset")
    print("[audit] Phase4 pseudo-register optimization preserved")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    patch_scratch_layout(root)
    patch_ringbuffer_struct(root)
    patch_pagetable_helper(root)
    remove_per_rb_allocation(root)
    patch_active_context_reset(root)

    patch_preemption_file(root / MSM / "adreno_a5xx_preempt.c")
    patch_preemption_file(root / MSM / "adreno_a6xx_preempt.c")
    patch_iommu_cp_writes(root)

    audit(root)

    print("[done] A619 GPU modernization Phase5 applied")
    print("[source] Qualcomm 48fc67d2bcfb: get rid of per-ringbuffer scratch memory")
    print("[adaptation] legacy Samsung 4.19 pagetable_desc layout -> global KGSL scratch")
    print("[effect] removes one PAGE_SIZE privileged allocation per ringbuffer")
    print("[effect] page-table switch state now shares the existing randomized privileged scratch page")
    print("[safety] no clock, voltage, bus-policy, thermal, or scheduler tuning changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
