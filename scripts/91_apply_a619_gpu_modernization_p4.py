#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


MARKER_PSEUDO = "A619 GPU P4: Qualcomm 96f7537ccfcd"
MARKER_OOB = "A619 GPU P4: Qualcomm 06f837b9da4f"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_function(text: str, start: str, end: str, new: str, label: str) -> str:
    a = text.find(start)
    if a < 0:
        raise SystemExit(f"{label}: start anchor not found")
    b = text.find(end, a)
    if b < 0:
        raise SystemExit(f"{label}: end anchor not found")
    return text[:a] + new + text[b:]


def patch_ringbuffer_flag(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno_ringbuffer.h"
    text = path.read_text()

    if MARKER_PSEUDO in text:
        print(f"[already] {path}: pseudo-register ringbuffer flag")
        return

    anchor = """#define PT_INFO_OFFSET(_field) \\\n\toffsetof(struct adreno_ringbuffer_pagetable_info, _field)\n"""
    insert = anchor + f"""\n/* {MARKER_PSEUDO}\n * Track whether the ringbuffer-constant CP_SET_PSEUDO_REGISTER payload\n * has already been emitted since the latest GPU/preemption start.\n */\n#define ADRENO_RB_SET_PSEUDO_DONE 0\n"""
    text = replace_once(text, anchor, insert, "ringbuffer pseudo flag define")
    text = replace_once(
        text,
        "\tuint32_t flags;\n",
        "\tunsigned long flags;\n",
        "ringbuffer flags bitops storage",
    )
    path.write_text(text)
    print(f"[patched] {path}: ringbuffer pseudo-register state")


def patch_preemption_packets(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno_a6xx_preempt.c"
    text = path.read_text()

    if MARKER_PSEUDO in text:
        print(f"[already] {path}: redundant pseudo-register packet reduction")
        return

    new_func = f"""unsigned int a6xx_preemption_pre_ibsubmit(
\t\tstruct adreno_device *adreno_dev,
\t\tstruct adreno_ringbuffer *rb,
\t\tunsigned int *cmds, struct kgsl_context *context)
{{
\tunsigned int *cmds_orig = cmds;
\tuint64_t gpuaddr = 0;

\t/*
\t * {MARKER_PSEUDO}
\t *
\t * SMMU_INFO, the secure/non-secure ringbuffer preemption records and
\t * the perfcounter save area are constant for the lifetime of a
\t * ringbuffer. Qualcomm later stopped resending those addresses for
\t * every IB submission. This legacy-tree adaptation emits that 12-dword
\t * setup once per ringbuffer after each GPU start, while retaining the
\t * context-specific non-privileged save address as a small 3-dword
\t * packet on submissions that carry a context.
\t */
\tif (!test_and_set_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags)) {{
\t\t*cmds++ = cp_type7_packet(CP_SET_PSEUDO_REGISTER, 12);

\t\t/* NULL SMMU_INFO buffer - we track in KMD */
\t\t*cmds++ = SET_PSEUDO_REGISTER_SAVE_REGISTER_SMMU_INFO;
\t\tcmds += cp_gpuaddr(adreno_dev, cmds, 0x0);

\t\t*cmds++ =
\t\t\tSET_PSEUDO_REGISTER_SAVE_REGISTER_PRIV_NON_SECURE_SAVE_ADDR;
\t\tcmds += cp_gpuaddr(adreno_dev, cmds,
\t\t\trb->preemption_desc.gpuaddr);

\t\t*cmds++ =
\t\t\tSET_PSEUDO_REGISTER_SAVE_REGISTER_PRIV_SECURE_SAVE_ADDR;
\t\tcmds += cp_gpuaddr(adreno_dev, cmds,
\t\t\trb->secure_preemption_desc.gpuaddr);

\t\t*cmds++ = SET_PSEUDO_REGISTER_SAVE_REGISTER_COUNTER;
\t\tcmds += cp_gpuaddr(adreno_dev, cmds,
\t\t\trb->perfcounter_save_restore_desc.gpuaddr);
\t}}

\tif (context) {{
\t\tgpuaddr = context->user_ctxt_record->memdesc.gpuaddr;

\t\t/*
\t\t * The non-privileged context record changes with the context, so
\t\t * keep only this small payload in the per-submit path.
\t\t */
\t\t*cmds++ = cp_type7_packet(CP_SET_PSEUDO_REGISTER, 3);
\t\t*cmds++ =
\t\t\tSET_PSEUDO_REGISTER_SAVE_REGISTER_NON_PRIV_SAVE_ADDR;
\t\tcmds += cp_gpuaddr(adreno_dev, cmds, gpuaddr);

\t\t{{
\t\t\tstruct adreno_context *drawctxt = ADRENO_CONTEXT(context);
\t\t\tstruct adreno_ringbuffer *ctx_rb = drawctxt->rb;
\t\t\tuint64_t dest =
\t\t\t\tPREEMPT_SCRATCH_ADDR(adreno_dev, ctx_rb->id);

\t\t\t*cmds++ = cp_mem_packet(adreno_dev, CP_MEM_WRITE, 2, 2);
\t\t\tcmds += cp_gpuaddr(adreno_dev, cmds, dest);
\t\t\t*cmds++ = lower_32_bits(gpuaddr);
\t\t\t*cmds++ = upper_32_bits(gpuaddr);
\t\t}}

\t\t/*
\t\t * Preserve touchGrass' KMD post-amble behavior exactly. This is
\t\t * independent of Qualcomm's redundant pseudo-packet cleanup.
\t\t */
\t\tif (!adreno_dev->perfcounter) {{
\t\t\tu64 kmd_postamble_addr = SCRATCH_POSTAMBLE_ADDR(
\t\t\t\tKGSL_DEVICE(adreno_dev));

\t\t\t*cmds++ = cp_type7_packet(CP_SET_AMBLE, 3);
\t\t\t*cmds++ = lower_32_bits(kmd_postamble_addr);
\t\t\t*cmds++ = upper_32_bits(kmd_postamble_addr);
\t\t\t*cmds++ = ((CP_KMD_AMBLE_TYPE << 20) | GENMASK(22, 20))
\t\t\t\t| (adreno_dev->preempt.postamble_len |
\t\t\t\tGENMASK(19, 0));
\t\t}}
\t}}

\treturn (unsigned int) (cmds - cmds_orig);
}}

"""
    text = replace_function(
        text,
        "unsigned int a6xx_preemption_pre_ibsubmit(",
        "unsigned int a6xx_preemption_post_ibsubmit",
        new_func,
        "a6xx pre-submit function",
    )

    old = """\t\tadreno_ringbuffer_set_pagetable(rb,
\t\t\tdevice->mmu.defaultpagetable);
\t}
}
"""
    new = """\t\tadreno_ringbuffer_set_pagetable(rb,
\t\t\tdevice->mmu.defaultpagetable);

\t\t/*
\t\t * The CP loses pseudo-register programming across a GPU restart,
\t\t * so force the constant setup packet to be emitted again.
\t\t */
\t\tclear_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags);
\t}
}
"""
    text = replace_once(text, old, new, "clear pseudo state on preemption start")

    path.write_text(text)
    print(f"[patched] {path}: Qualcomm 96f7537ccfcd adapted to legacy A6xx submission")


def patch_oob_refcount(root: Path) -> None:
    hdr = root / "drivers/gpu/msm/kgsl_gmu.h"
    text = hdr.read_text()

    if MARKER_OOB not in text:
        old = """\tunsigned int fault_count;
\tstruct kgsl_mailbox mailbox;
"""
        new = f"""\tunsigned int fault_count;
\t/* {MARKER_OOB}: active perf-counter OOB users */
\tunsigned int num_oob_perfcntr;
\tstruct kgsl_mailbox mailbox;
"""
        text = replace_once(text, old, new, "GMU perf OOB refcount field")
        hdr.write_text(text)
        print(f"[patched] {hdr}: perf-counter OOB refcount field")

    path = root / "drivers/gpu/msm/adreno_a6xx_gmu.c"
    text = path.read_text()

    if MARKER_OOB in text:
        print(f"[already] {path}: concurrent perf-counter OOB")
        return

    old = """\tint ret = 0;
\tint set, check;

\tif (!adreno_is_a630(adreno_dev) && !adreno_is_a615_family(adreno_dev)) {
"""
    new = f"""\tint ret = 0;
\tint set, check;

\t/*
\t * {MARKER_OOB}
\t *
\t * Multiple KGSL users can need perf counters at the same time. Keep
\t * one GMU OOB handshake active until the final user releases it
\t * instead of bouncing the request for every overlapping caller.
\t */
\tif (req == oob_perfcntr && gmu->num_oob_perfcntr++)
\t\treturn 0;

\tif (!adreno_is_a630(adreno_dev) && !adreno_is_a615_family(adreno_dev)) {{
"""
    text = replace_once(text, old, new, "OOB set refcount entry")

    old = """\tif (timed_poll_check(device,
\t\t\tA6XX_GMU_GMU2HOST_INTR_INFO,
\t\t\tcheck,
\t\t\tGPU_START_TIMEOUT,
\t\t\tcheck)) {
\t\tret = -ETIMEDOUT;
"""
    new = """\tif (timed_poll_check(device,
\t\t\tA6XX_GMU_GMU2HOST_INTR_INFO,
\t\t\tcheck,
\t\t\tGPU_START_TIMEOUT,
\t\t\tcheck)) {
\t\tif (req == oob_perfcntr)
\t\t\tgmu->num_oob_perfcntr--;
\t\tret = -ETIMEDOUT;
"""
    text = replace_once(text, old, new, "OOB timeout refcount rollback")

    old = """\tstruct gmu_device *gmu = KGSL_GMU_DEVICE(device);
\tint clear;

\tif (!adreno_is_a630(adreno_dev) && !adreno_is_a615_family(adreno_dev)) {
"""
    new = """\tstruct gmu_device *gmu = KGSL_GMU_DEVICE(device);
\tint clear;

\tif (req == oob_perfcntr && --gmu->num_oob_perfcntr)
\t\treturn;

\tif (!adreno_is_a630(adreno_dev) && !adreno_is_a615_family(adreno_dev)) {
"""
    text = replace_once(text, old, new, "OOB clear refcount exit")

    path.write_text(text)
    print(f"[patched] {path}: Qualcomm 06f837b9da4f concurrent perf-counter OOB")


def audit_applicability(root: Path) -> None:
    gpulist = (root / "drivers/gpu/msm/adreno-gpulist.h").read_text()
    start = gpulist.find("adreno_gpu_core_a619")
    if start < 0:
        raise SystemExit("A619 core block not found")
    block = gpulist[start:start + 1800]

    for token in ("ADRENO_PREEMPTION", "ADRENO_GPMU", "ADRENO_IFPC"):
        if token not in block:
            raise SystemExit(f"A619 applicability audit: missing {token}")

    if "ADRENO_PROCESS_RECLAIM" in block or "ADRENO_USE_SHMEM" in block:
        raise SystemExit("A619 reclaim feature unexpectedly enabled; re-audit Phase4")

    preempt = (root / "drivers/gpu/msm/adreno_a6xx_preempt.c").read_text()
    for token in (
        MARKER_PSEUDO,
        "test_and_set_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags)",
        "cp_type7_packet(CP_SET_PSEUDO_REGISTER, 3)",
        "clear_bit(ADRENO_RB_SET_PSEUDO_DONE, &rb->flags)",
        "SCRATCH_POSTAMBLE_ADDR",
    ):
        if token not in preempt:
            raise SystemExit(f"pseudo packet audit missing: {token}")

    gmu = (root / "drivers/gpu/msm/adreno_a6xx_gmu.c").read_text()
    gh = (root / "drivers/gpu/msm/kgsl_gmu.h").read_text()
    for token in (
        "gmu->num_oob_perfcntr++",
        "--gmu->num_oob_perfcntr",
        "gmu->num_oob_perfcntr--",
    ):
        if token not in gmu:
            raise SystemExit(f"OOB audit missing: {token}")
    if "unsigned int num_oob_perfcntr;" not in gh:
        raise SystemExit("OOB refcount field audit failed")

    print("[audit] A619 preemption path: active")
    print("[audit] A619 legacy A615-family GMU OOB path: active")
    print("[audit] async process reclaim: skipped because A619 does not enable PROCESS_RECLAIM/USE_SHMEM")
    print("[audit] per-ringbuffer scratch consolidation: deferred; newer Qualcomm ringbuffer split is not present in this legacy tree")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    patch_ringbuffer_flag(root)
    patch_preemption_packets(root)
    patch_oob_refcount(root)
    audit_applicability(root)

    print("[done] A619 GPU modernization Phase4 applied")
    print("[source] Qualcomm 96f7537ccfcd: remove redundant SET_PSEUDO_REGISTER packets")
    print("[source] Qualcomm 06f837b9da4f: allow concurrent perf-counter OOB requests")
    print("[effect] constant preemption pseudo state is no longer resent for every IB")
    print("[effect] overlapping perf-counter users share one GMU OOB handshake")
    print("[excluded] async reclaim a07afc4e1477: A619 reclaim feature is disabled")
    print("[deferred] per-ringbuffer scratch 48fc67d2bcfb: requires broader ringbuffer-layout adaptation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
