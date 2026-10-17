#!/usr/bin/env python3
"""Phase446Q: reconcile required pre-429 ION/secure heap markers after cache.

Repair only missing, already-existing legacy compatibility stages. Abort if a
required secure contract cannot be restored rather than weakening its semantics.
Full marker census covers all phase-like 'A52_' markers in assembled sources.
"""
from pathlib import Path
import argparse,json,re,subprocess,sys

PHASES={
 "146":("drivers/staging/android/ion/ion.c",
        "A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT",
        "146_apply_a52xq_legacy_system_heap_mask.py"),
 "148":("drivers/staging/android/ion/ion_dma_buf.c",
        "A52_ION_DMABUF_FLAGS_FALLBACK",
        "148_apply_a52xq_ion_dmabuf_contract.py"),
 "149":("drivers/staging/android/ion/ion.c",
        "A52_ION_SYSTEM_HEAP_NONSECURE_GATE",
        "149_apply_a52xq_ion_system_heap_secure_gate.py"),
}
# Heap driver from the older 152/153 stages supersedes separately attempted
# 166/169 heap19 implementations. Require heap *functionality*, not conflicting
# old markers from implementations that are intentionally not built together.
REQUIRED = {
 "154_failure_window":("include/linux/a52_ack_secure_flight_recorder.h",
                       "A52_FAILURE_WINDOW_PROBE_V1"),
}
def marker_present(root,rel,mark):
    p=root/rel
    return p.is_file() and mark in p.read_text(errors="replace")

def run_stage(name,root,output):
    rel,mark,script=PHASES[name]
    if name in ("146","149"):
        path=root/"drivers/staging/android/ion/ion.c"
        s=path.read_text()
        # The Phase213 allocator rewrote the Phase146/149 block but retained
        # exactly one conditional heap25->ION_HEAP_SYSTEM retry.
        prefix="\t\tif (fd == -ENODEV &&\n\t\t    (data.allocation.heap_id_mask & (1U << 25))) {"
        if s.count(prefix)!=1 or s.count("fd == -ENODEV")!=1:
            raise RuntimeError("P446Q unexpected allocator gate; refusing duplicate retry")
        i=s.index(prefix)
        tail=s[i:i+700]
        for required in ("effective_mask =", "(data.allocation.heap_id_mask & ~(1U << 25)) |",
                         "ION_HEAP_SYSTEM", "fd = ion_alloc_fd(", "data.allocation.flags);"):
            if required not in tail:
                raise RuntimeError("P446Q legacy translation invariant missing: "+required)
        if name=="146":
            # No new retry: the Phase213 implementation preserved Phase146.
            comment=("\t\t/* A52_ION_LEGACY_SYSTEM_HEAP_MASK_COMPAT\n"
                     "\t\t * Preserved Phase213 one-retry allocation path.\n"
                     "\t\t */\n")
            s=s.replace(prefix,comment+prefix,1)
        else:
            # Phase149: secure/CP allocations MUST NOT be translated to
            # non-secure HLOS generic system memory.
            guarded=("\t\t/* A52_ION_SYSTEM_HEAP_NONSECURE_GATE\n"
                     "\t\t * Secure/CP allocations cannot retry in HLOS system heap.\n"
                     "\t\t */\n"
                     "\t\tif (fd == -ENODEV &&\n"
                     "\t\t    (data.allocation.heap_id_mask & (1U << 25)) &&\n"
                     "\t\t    !(data.allocation.flags & (0x6FFE0000U | (1U << 31)))) {")
            s=s.replace(prefix,guarded,1)
        path.write_text(s)
    else:
        cmd=[sys.executable,str(Path("scripts")/script),"--gki",str(root),
             "--output",str(output)]
        subprocess.run(cmd,check=True)
    if not marker_present(root,rel,mark):
        raise RuntimeError("P446Q repair did not leave marker: "+name+"/"+mark)

def reconcile(root,output,repair):
    out={"stages":{},"missing_before":[],"restored":[],"missing_after":[]}
    # Secure heap gate checks the 146 mask in its own audit; ordering matters.
    for phase in ("146","149","148"):
        rel,mark,script=PHASES[phase]
        present=marker_present(root,rel,mark)
        out["stages"][phase]={"marker":mark,"file":rel,
                              "before":"present" if present else "missing"}
        if not present:
            out["missing_before"].append(phase)
            if repair:
                run_stage(phase,root,output)
                out["restored"].append(phase)
        if not marker_present(root,rel,mark):
            out["missing_after"].append(phase)
    ion=(root/"drivers/staging/android/ion/ion_dma_buf.c").read_text()
    # Required in the final, compiled exporter. This is deliberately stricter
    # than the Phase148 marker on its own.
    if "*flags = buffer->flags;" not in ion:
        out["missing_after"].append("148_flags_assignment")
    if "if (heap->buf_ops.get_flags)" not in ion:
        out["missing_after"].append("148_heap_callback_override")

    # 152/153 heap ID19+27 combined stage exists in the current lineage:
    # do not instantiate an additional competing 166 heap driver.
    heap_rel="drivers/a52_secure/a52_qseecom_ion_heap.c"
    heap=root/heap_rel
    core=root/"drivers/a52_secure/Makefile"
    if not heap.exists() or not core.exists() or \
       "a52_qseecom_ion_heap.o" not in core.read_text() or \
       "qsecom_ta" not in heap.read_text(errors="replace") or \
       "qsecom" not in heap.read_text(errors="replace"):
        out["missing_after"].append("153_combined_secure_heaps_19_27")
    out["stages"]["153_combined_heap"]={
        "file":heap_rel,"present":heap.exists() and
        "a52_qseecom_ion_heap.o" in (core.read_text() if core.exists() else "")
    }
    for name,(rel,mark) in REQUIRED.items():
        hit=marker_present(root,rel,mark)
        out["stages"][name]={"marker":mark,"file":rel,"present":hit}
        if not hit:out["missing_after"].append(name)

    # Audit full final-tree phase marker census (diagnostic). This is not
    # a claim that every historical experiment should be simultaneously active.
    counts={}
    scan_dirs=("drivers/staging/android/ion","drivers/a52_secure",
               "drivers/a52_display","drivers/iommu",
               "drivers/regulator")
    for d in scan_dirs:
        loc=root/d
        if not loc.is_dir():continue
        for p in loc.rglob("*.c"):
            try:s=p.read_text(errors="replace")
            except OSError:continue
            for tok in set(re.findall(r"\bA52_[A-Z0-9_]{6,}\b",s)):
                counts.setdefault(tok,[]).append(str(p.relative_to(root)))
    out["final_marker_census"]=counts
    out["source_counts"]={"distinct_A52_markers":len(counts),
                           "files_with_markers":len(set(x for v in counts.values() for x in v))}
    output.mkdir(parents=True,exist_ok=True)
    (output/"phase446q-final-tree-marker-audit.json").write_text(
        json.dumps(out,sort_keys=True,indent=2)+"\n")
    print("P446Q PRE-429 MARKER AUDIT missing_before="+str(out["missing_before"])+
          " restored="+str(out["restored"])+
          " missing_after="+str(out["missing_after"])+
          " census="+str(out["source_counts"]),flush=True)
    if out["missing_after"]:
        raise RuntimeError("P446Q required final marker(s) absent: "+
                           str(out["missing_after"]))
    return out

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--output",type=Path,default=Path("/tmp/phase446q-audit"))
    p.add_argument("--repair",action="store_true")
    a=p.parse_args()
    reconcile(a.root.resolve(),a.output.resolve(),a.repair)

if __name__=="__main__":main()
