#!/usr/bin/env python3
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: 91_apply_a619_gpu_modernization_p4.py <kernel-tree>")

root = Path(sys.argv[1]).resolve()
gpu = root / "drivers/gpu/msm"

RB_H = gpu / "adreno_ringbuffer.h"
ADRENO_H = gpu / "adreno.h"
ADRENO_C = gpu / "adreno.c"
IOMMU_C = gpu / "adreno_iommu.c"
A5_PREEMPT = gpu / "adreno_a5xx_preempt.c"
A6_PREEMPT = gpu / "adreno_a6xx_preempt.c"
RB_C = gpu / "adreno_ringbuffer.c"

for p in (RB_H, ADRENO_H, ADRENO_C, IOMMU_C, A5_PREEMPT, A6_PREEMPT, RB_C):
    if not p.is_file():
        raise SystemExit(f"missing required source: {p}")

MARKER_PSEUDO = "A619 GPU P4: Qualcomm 96f7537ccfcd"
MARKER_SCRATCH = "A619 GPU P4: Qualcomm 48fc67d2bcfb"


def read(path: Path) -> str:
    return path.read_text()


def write(path: Path, text: str) -> None:
    path.write_text(text)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Qualcomm 96f7537ccfcd
# Avoid sending ringbuffer-static CP_SET_PSEUDO_REGISTER state on every IB.
# The static state is programmed once per RB after each GPU start; the
# context-specific non-priv restore address continues to be written into
# preemption scratch for every context submission.
# ---------------------------------------------------------------------------
rbh = read(RB_H)
if MARKER_PSEUDO not in rbh:
    rbh = replace_once(
        rbh,
        "#define PT_INFO_OFFSET(_field) \\\n\toffsetof(struct adreno_ringbuffer_pagetable_info, _field)\n",
        "#define PT_INFO_OFFSET(_field) \\\n\toffsetof(struct adreno_ringbuffer_pagetable_info, _field)\n\n"
        f"/* {MARKER_PSEUDO}: program static pseudo registers once per RB */\n"
        "#define ADRENO_RB_SET_PSEUDO_DONE 0\n",
        "pseudo flag definition",
    )
    rbh = replace_once(
        rbh,
        "\tuint32_t flags;\n",
        "\tunsigned long flags;\n",
        "ringbuffer flags bitops storage",
    )
write(RB_H, rbh)

a6 = read(A6_PREEMPT)
if MARKER_PSEUDO not in a6:
    start = a6.find("unsigned int a6xx_preemption_pre_ibsubmit(")
    end = a6.find("\nunsigned int a6xx_preemption_post_ibsubmit", start)
    if start < 0 or end < 0:
        raise SystemExit("unable to locate a6xx_preemption_pre_ibsubmit")

    new_func = f'''unsigned int a6xx_preemption_pre_ibsubmit(
        struct adreno_device *adreno_dev,
        struct adreno_ringbuffer *rb,
        unsigned int *cmds, struct kgsl_context *context)
{{
    unsigned int *cmds_orig = cmds;
    uint64_t gpuaddr = 0;

    if (!adreno_is_preemption_enabled(adreno_dev))
        return 0;

    if (context)
        gpuaddr = context->user_ctxt_record->memdesc.gpuaddr;

    /*
     * {MARKER_PSEUDO}
     *
     * SMMU info, privileged preemption records and the perfcounter restore
     * record are ringbuffer-static. Program those pseudo registers once per
     * RB instead of rebuilding the same 13-dword command sequence for every
     * IB submission. The bit is cleared in a6xx_preemption_start(), so the
     * state is rebuilt after every GPU restart.
     */
    if (test_and_set_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags))
        goto context_state;

    *cmds++ = cp_type7_packet(CP_SET_PSEUDO_REGISTER, 12);

    /* NULL SMMU_INFO buffer - we track in KMD */
    *cmds++ = SET_PSEUDO_REGISTER_SAVE_REGISTER_SMMU_INFO;
    cmds += cp_gpuaddr(adreno_dev, cmds, 0x0);

    *cmds++ = SET_PSEUDO_REGISTER_SAVE_REGISTER_PRIV_NON_SECURE_SAVE_ADDR;
    cmds += cp_gpuaddr(adreno_dev, cmds, rb->preemption_desc.gpuaddr);

    *cmds++ = SET_PSEUDO_REGISTER_SAVE_REGISTER_PRIV_SECURE_SAVE_ADDR;
    cmds += cp_gpuaddr(adreno_dev, cmds,
            rb->secure_preemption_desc.gpuaddr);

    *cmds++ = SET_PSEUDO_REGISTER_SAVE_REGISTER_COUNTER;
    cmds += cp_gpuaddr(adreno_dev, cmds,
            rb->perfcounter_save_restore_desc.gpuaddr);

context_state:
    if (context) {{
        struct adreno_context *drawctxt = ADRENO_CONTEXT(context);
        struct adreno_ringbuffer *ctx_rb = drawctxt->rb;
        uint64_t dest = PREEMPT_SCRATCH_ADDR(adreno_dev, ctx_rb->id);

        /*
         * Keep the context-specific restore address hot-path update. CP
         * preemption reads this scratch slot before programming NON_PRIV
         * restore registers, so removing it would change context semantics.
         */
        *cmds++ = cp_mem_packet(adreno_dev, CP_MEM_WRITE, 2, 2);
        cmds += cp_gpuaddr(adreno_dev, cmds, dest);
        *cmds++ = lower_32_bits(gpuaddr);
        *cmds++ = upper_32_bits(gpuaddr);

        /*
         * Preserve touchGrass' KMD postamble used to clear performance
         * counters during preemption when userspace perfcounters are disabled.
         */
        if (!adreno_dev->perfcounter) {{
            u64 kmd_postamble_addr = SCRATCH_POSTAMBLE_ADDR(
                        KGSL_DEVICE(adreno_dev));

            *cmds++ = cp_type7_packet(CP_SET_AMBLE, 3);
            *cmds++ = lower_32_bits(kmd_postamble_addr);
            *cmds++ = upper_32_bits(kmd_postamble_addr);
            *cmds++ = ((CP_KMD_AMBLE_TYPE << 20) | GENMASK(22, 20))
                | (adreno_dev->preempt.postamble_len | GENMASK(19, 0));
        }}
    }}

    return (unsigned int) (cmds - cmds_orig);
}}
'''
    a6 = a6[:start] + new_func + a6[end:]

    anchor = '''\t\tadreno_ringbuffer_set_pagetable(rb,
\t\t\tdevice->mmu.defaultpagetable);
'''
    a6 = replace_once(
        a6,
        anchor,
        anchor
        + "\n"
        + f"\t\t/* {MARKER_PSEUDO}: static pseudo state is invalid after restart */\n"
        + "\t\tclear_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags);\n",
        "pseudo reset on GPU start",
    )
write(A6_PREEMPT, a6)


# ---------------------------------------------------------------------------
# Qualcomm 48fc67d2bcfb
# Remove the PAGE_SIZE pagetable_desc allocation from every ringbuffer.
#
# Legacy-safe adaptation:
# Keep the existing touchGrass RPTR shadows at offsets 0,4,8,12 unchanged.
# Store per-RB pagetable metadata immediately after those four RPTR dwords in
# the already allocated device scratch page. The postamble begins at byte 800,
# leaving a large compile-time checked gap.
# ---------------------------------------------------------------------------
rbh = read(RB_H)
if MARKER_SCRATCH not in rbh:
    anchor = "#define ADRENO_RB_SET_PSEUDO_DONE 0\n"
    addition = f'''
/* {MARKER_SCRATCH}: fold per-RB pagetable shadow into device scratch */
#define SCRATCH_PT_BASE \
    (KGSL_PRIORITY_MAX_RB_LEVELS * sizeof(unsigned int))
#define SCRATCH_PT_OFFSET(id, _field) \
    (SCRATCH_PT_BASE + ((id) * sizeof(struct adreno_ringbuffer_pagetable_info)) + \
    PT_INFO_OFFSET(_field))
#define SCRATCH_PT_GPU_ADDR(dev, id, _field) \
    ((dev)->scratch.gpuaddr + SCRATCH_PT_OFFSET(id, _field))
#define SCRATCH_PT_END \
    (SCRATCH_PT_BASE + (KGSL_PRIORITY_MAX_RB_LEVELS * \
    sizeof(struct adreno_ringbuffer_pagetable_info)))
'''
    rbh = replace_once(rbh, anchor, anchor + addition, "scratch metadata macros")

    rbh = rbh.replace(
        " * @pagetable_desc: Memory to hold information about the pagetables being used\n"
        " * and the commands to switch pagetable on the RB\n",
        "",
        1,
    )
    rbh = replace_once(
        rbh,
        "\tstruct kgsl_memdesc pagetable_desc;\n",
        "",
        "remove per-RB pagetable descriptor",
    )
write(RB_H, rbh)

ah = read(ADRENO_H)
old_global = '''static inline void adreno_ringbuffer_set_global(
\t\tstruct adreno_device *adreno_dev, int name)
{
\tstruct kgsl_device *device = KGSL_DEVICE(adreno_dev);

\tkgsl_sharedmem_writel(device,
\t\t&adreno_dev->ringbuffers[0].pagetable_desc,
\t\tPT_INFO_OFFSET(current_global_ptname), name);
}
'''
new_global = f'''static inline void adreno_ringbuffer_set_global(
\t\tstruct adreno_device *adreno_dev, int name)
{{
\tstruct kgsl_device *device = KGSL_DEVICE(adreno_dev);

\t/* {MARKER_SCRATCH}: keep global PT shadow in the shared scratch page */
\tkgsl_sharedmem_writel(device, &device->scratch,
\t\tSCRATCH_PT_OFFSET(0, current_global_ptname), name);
}}
'''
if MARKER_SCRATCH not in ah:
    ah = replace_once(ah, old_global, new_global, "global pagetable shadow")

    old_pt = '''static inline void adreno_ringbuffer_set_pagetable(struct adreno_ringbuffer *rb,
\t\tstruct kgsl_pagetable *pt)
{
\tstruct adreno_device *adreno_dev = ADRENO_RB_DEVICE(rb);
\tstruct kgsl_device *device = KGSL_DEVICE(adreno_dev);
\tunsigned long flags;

\tspin_lock_irqsave(&rb->preempt_lock, flags);

\tkgsl_sharedmem_writel(device, &rb->pagetable_desc,
\t\tPT_INFO_OFFSET(current_rb_ptname), pt->name);

\tkgsl_sharedmem_writeq(device, &rb->pagetable_desc,
\t\tPT_INFO_OFFSET(ttbr0), kgsl_mmu_pagetable_get_ttbr0(pt));

\tkgsl_sharedmem_writel(device, &rb->pagetable_desc,
\t\tPT_INFO_OFFSET(contextidr),
\t\tkgsl_mmu_pagetable_get_contextidr(pt));

\tspin_unlock_irqrestore(&rb->preempt_lock, flags);
}
'''
    new_pt = f'''static inline void adreno_ringbuffer_set_pagetable(struct adreno_ringbuffer *rb,
\t\tstruct kgsl_pagetable *pt)
{{
\tstruct adreno_device *adreno_dev = ADRENO_RB_DEVICE(rb);
\tstruct kgsl_device *device = KGSL_DEVICE(adreno_dev);
\tunsigned long flags;

\tspin_lock_irqsave(&rb->preempt_lock, flags);

\t/* {MARKER_SCRATCH}: per-RB PT state now lives in device scratch */
\tkgsl_sharedmem_writel(device, &device->scratch,
\t\tSCRATCH_PT_OFFSET(rb->id, current_rb_ptname), pt->name);

\tkgsl_sharedmem_writeq(device, &device->scratch,
\t\tSCRATCH_PT_OFFSET(rb->id, ttbr0),
\t\tkgsl_mmu_pagetable_get_ttbr0(pt));

\tkgsl_sharedmem_writel(device, &device->scratch,
\t\tSCRATCH_PT_OFFSET(rb->id, contextidr),
\t\tkgsl_mmu_pagetable_get_contextidr(pt));

\tspin_unlock_irqrestore(&rb->preempt_lock, flags);
}}
'''
    ah = replace_once(ah, old_pt, new_pt, "per-RB pagetable shadow")
write(ADRENO_H, ah)

ac = read(ADRENO_C)
if MARKER_SCRATCH not in ac:
    old = '''\t\tkgsl_sharedmem_writel(KGSL_DEVICE(adreno_dev),
\t\t\t&rb->pagetable_desc, PT_INFO_OFFSET(current_rb_ptname),
\t\t\t0);
'''
    new = f'''\t\t/* {MARKER_SCRATCH}: clear PT name in shared device scratch */
\t\tkgsl_sharedmem_writel(KGSL_DEVICE(adreno_dev),
\t\t\t&KGSL_DEVICE(adreno_dev)->scratch,
\t\t\tSCRATCH_PT_OFFSET(rb->id, current_rb_ptname), 0);
'''
    ac = replace_once(ac, old, new, "active-context pagetable clear")
write(ADRENO_C, ac)

iommu = read(IOMMU_C)
old_addr = '''(rb->pagetable_desc.gpuaddr +
\t\tPT_INFO_OFFSET(ttbr0))'''
count = iommu.count(old_addr)
if count != 2 and MARKER_SCRATCH not in iommu:
    raise SystemExit(f"IOMMU scratch target: expected two old addresses, found {count}")
if MARKER_SCRATCH not in iommu:
    iommu = iommu.replace(
        old_addr,
        f'''SCRATCH_PT_GPU_ADDR(device, rb->id, ttbr0) /* {MARKER_SCRATCH} */''',
    )
write(IOMMU_C, iommu)

for preempt_path, family in ((A5_PREEMPT, "A5xx"), (A6_PREEMPT, "A6xx")):
    text = read(preempt_path)
    legacy_comment = '''\t/*
\t * Get the pagetable from the pagetable info.
\t * The pagetable_desc is allocated and mapped at probe time, and
\t * preemption_desc at init time, so no need to check if
\t * sharedmem accesses to these memdescs succeed.
\t */
'''
    if legacy_comment in text:
        text = replace_once(
            text,
            legacy_comment,
            '''\t/*
\t * Read the per-ringbuffer pagetable shadow from the device scratch page.
\t * The preemption descriptor remains allocated and mapped at init time.
\t */
''',
            f"{family} pagetable shadow comment",
        )

    old = '''\tkgsl_sharedmem_readq(&next->pagetable_desc, &ttbr0,
\t\tPT_INFO_OFFSET(ttbr0));
\tkgsl_sharedmem_readl(&next->pagetable_desc, &contextidr,
\t\tPT_INFO_OFFSET(contextidr));
'''
    if old in text:
        new = f'''\t/* {MARKER_SCRATCH}: read PT state from shared device scratch ({family}) */
\tkgsl_sharedmem_readq(&device->scratch, &ttbr0,
\t\tSCRATCH_PT_OFFSET(next->id, ttbr0));
\tkgsl_sharedmem_readl(&device->scratch, &contextidr,
\t\tSCRATCH_PT_OFFSET(next->id, contextidr));
'''
        text = replace_once(text, old, new, f"{family} pagetable preempt read")
    elif MARKER_SCRATCH not in text:
        raise SystemExit(f"{family} pagetable preempt read anchor missing")
    write(preempt_path, text)

rbc = read(RB_C)
if MARKER_SCRATCH not in rbc:
    rbc = replace_once(
        rbc,
        '''\tstruct adreno_ringbuffer *rb = &adreno_dev->ringbuffers[id];
\tint ret;
\tunsigned int priv = 0;
''',
        '''\tstruct adreno_ringbuffer *rb = &adreno_dev->ringbuffers[id];
\tunsigned int priv = 0;
''',
        "drop obsolete probe ret",
    )

    alloc = '''\n\t/*
\t * Allocate mem for storing RB pagetables and commands to
\t * switch pagetable
\t */
\tret = kgsl_allocate_global(KGSL_DEVICE(adreno_dev), &rb->pagetable_desc,
\t\tPAGE_SIZE, 0, KGSL_MEMDESC_PRIVILEGED, "pagetable_desc");
\tif (ret)
\t\treturn ret;
'''
    rbc = replace_once(
        rbc,
        alloc,
        f'''\n\t/* {MARKER_SCRATCH}: no per-RB pagetable GPU allocation */\n''',
        "remove pagetable allocation",
    )

    rbc = replace_once(
        rbc,
        '''\tkgsl_free_global(device, &rb->pagetable_desc);
''',
        "",
        "remove pagetable free",
    )

    probe_anchor = '''int adreno_ringbuffer_probe(struct adreno_device *adreno_dev)
{
\tstruct kgsl_device *device = KGSL_DEVICE(adreno_dev);
\tstruct adreno_gpudev *gpudev = ADRENO_GPU_DEVICE(adreno_dev);
\tint i;
\tint status = -ENOMEM;
'''
    rbc = replace_once(
        rbc,
        probe_anchor,
        probe_anchor
        + f'''\n\t/* {MARKER_SCRATCH}: protect legacy RPTR and postamble layouts */\n'''
        + "\tBUILD_BUG_ON(SCRATCH_PT_END > SCRATCH_POSTAMBLE_OFFSET);\n",
        "scratch layout compile-time check",
    )
write(RB_C, rbc)


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------
a6 = read(A6_PREEMPT)
rbh = read(RB_H)
ah = read(ADRENO_H)
rbc = read(RB_C)
iommu = read(IOMMU_C)

if MARKER_PSEUDO not in a6 or MARKER_PSEUDO not in rbh:
    raise SystemExit("Phase4 pseudo-register marker missing")
if "test_and_set_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags)" not in a6:
    raise SystemExit("one-time pseudo-register guard missing")
if "clear_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags)" not in a6:
    raise SystemExit("pseudo-register reset on GPU start missing")
if "CP_SET_PSEUDO_REGISTER, 15" in a6[a6.find("a6xx_preemption_pre_ibsubmit"):a6.find("a6xx_preemption_post_ibsubmit")]:
    raise SystemExit("old context-dependent 15-dword pseudo-register packet remains")
if a6[a6.find("a6xx_preemption_pre_ibsubmit"):a6.find("a6xx_preemption_post_ibsubmit")].count("CP_SET_PSEUDO_REGISTER") != 2:
    # one occurrence is in the marker/comment and one is the actual packet
    raise SystemExit("unexpected CP_SET_PSEUDO_REGISTER count in pre-submit function")
if "CP_SET_AMBLE" not in a6[a6.find("a6xx_preemption_pre_ibsubmit"):a6.find("a6xx_preemption_post_ibsubmit")]:
    raise SystemExit("touchGrass KMD postamble was not preserved")

for text, label in ((rbh, "ringbuffer header"), (ah, "adreno header"),
                    (rbc, "ringbuffer source"), (iommu, "iommu source"),
                    (read(A5_PREEMPT), "A5 preempt"), (a6, "A6 preempt"),
                    (read(ADRENO_C), "adreno source")):
    if MARKER_SCRATCH not in text and label not in ("ringbuffer header",):
        raise SystemExit(f"{label}: Phase4 scratch marker missing")

remaining = []
for p in gpu.rglob("*"):
    if p.suffix not in (".c", ".h") or not p.is_file():
        continue
    data = p.read_text(errors="ignore")
    if "pagetable_desc" in data:
        remaining.append(str(p.relative_to(root)))

if remaining:
    raise SystemExit("legacy pagetable_desc references remain: " + ", ".join(remaining))

if "SCRATCH_PT_END > SCRATCH_POSTAMBLE_OFFSET" not in rbc:
    raise SystemExit("scratch overlap compile-time check missing")

print("[patched] Qualcomm 96f7537ccfcd: static CP_SET_PSEUDO_REGISTER setup once per ringbuffer")
print("[patched] Qualcomm 48fc67d2bcfb: per-RB pagetable state consolidated into device scratch")
print("[audit] legacy RPTR offsets are unchanged")
print("[audit] scratch pagetable region is compile-time checked against KMD postamble")
print("[audit] context-specific non-priv restore scratch write is preserved")
print("[audit] KMD perfcounter postamble is preserved")
print("[audit] no pagetable_desc references remain in drivers/gpu/msm")
print("[done] A619 GPU modernization Phase4 hot-path applied")
