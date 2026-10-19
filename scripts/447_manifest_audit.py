#!/usr/bin/env python3
"""P447 planning manifest audit: unresolved source paths remain explicit blockers.

This verifies metadata, pinned TG source existence for verified rows, and
reports trap candidates with file:line. It does NOT assert working ports.
"""
import argparse,csv,json,re,runpy
from pathlib import Path
from collections import Counter
COLS=("wave","subsystem","function","TG path","Kconfig","DT compatible(s)",
      "ACK has same compatible? (conflict)","strategy",
      "suppliers (regulator/clock/icc/pinctrl/IRQ/mbox/iommu/reserved-mem)",
      "firmware files + path","userspace paths (/sys,/dev)","switch name","status")
STRATEGY={"ACK-as-is","vendor copy from TG (unique name)","mainline backport",
          "Qualcomm msm-5.10 compatibility backport","Qualcomm audio-kernel 5.10 backport"}
TRAPS={
 "19 GSI-only":r"GENI_IF_FIFO_DISABLE_RO|fifo_disable_ro",
 "20 map-device SID":r"dma_map_single\(|dma_map_sg\(|dma_alloc_coherent\(",
 "21 IRQ sequencing":r"enable_irq\(|request_(threaded_)?irq\(",
 "22 firmware fallback":r"request_firmware\(",
 "23 4.19 removed API":r"set_fs\(|get_fs\(|ioremap_nocache\(|PDE_DATA\(|access_ok\(|setup_timer\(",
 "DMA fence":r"fence\.cb_list|dma_fence_init\(",
 "regulator recursion":r"regulator_(enable|disable)\(",
 "clock/devfreq init":r"clk_hw|devfreq_monitor_start\(",
}
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",type=Path,default=Path("merge/manifest.csv"))
    p.add_argument("--tg",type=Path,required=True)
    p.add_argument("--ack",type=Path,required=True)
    p.add_argument("--out",type=Path,default=Path("phase447-inventory"))
    a=p.parse_args()
    with a.manifest.open(newline="") as f:
        reader=csv.DictReader(f)
        if tuple(reader.fieldnames or ())!=COLS:
            raise RuntimeError("P447 manifest header mismatch")
        rows=list(reader)
    if not rows:raise RuntimeError("P447 manifest empty")
    gates=runpy.run_path(str(Path(__file__).with_name("447_stage_control.py")))["GATES"]
    mapping={n:w for w,n in gates}
    seen=set(); counts=Counter(); missing=[]; hits=[]
    for r in rows:
        w=int(r["wave"]);n=r["subsystem"];sw=r["switch name"]
        key=(w,n,r["function"])
        if key in seen:raise RuntimeError("P447 duplicate "+str(key))
        seen.add(key)
        if w not in range(2,9) or mapping.get(sw.removeprefix("a52."))!=w:
            raise RuntimeError("P447 unregistered wave gate "+str(key))
        if r["strategy"] not in STRATEGY:raise RuntimeError("P447 unrecognized strategy "+str(key))
        if r["ACK has same compatible? (conflict)"] not in ("UNVERIFIED","YES","NO"):
            raise RuntimeError("P447 unverified ACK compatible claim "+str(key))
        counts[w]+=1
        path=r["TG path"]
        if path.startswith("UNRESOLVED"):
            missing.append({"wave":w,"subsystem":n,"function":r["function"],
                            "blocker":"pinned TG path, R02 DT compatible and suppliers still need validation"})
            continue
        src=a.tg/path
        if r["status"]=="TG-path-verified" and not src.is_file():
            raise RuntimeError("P447 verified TG path missing "+str(src))
        if src.is_file() and src.suffix in (".c",".h"):
            lines=src.read_text(errors="replace").splitlines()
            for name,pat in TRAPS.items():
                found=[f"{path}:{i} {line.strip()[:100]}" for i,line in enumerate(lines,1) if re.search(pat,line)]
                if found:
                    hits.append({"function":r["function"],"trap":name,"count":len(found),
                                 "source_lines":found[:16],"classification":"SUSPECTED"})
    a.out.mkdir(parents=True,exist_ok=True)
    j={"phase":"P447 inventory only; no W2-W8 port claim","row_count":len(rows),
       "counts":dict(counts),"gate_count":len(gates),"default_on_gates":0,
       "unresolved":missing,"traps_for_review":hits,
       "missing_census":["platform_unbound.txt","devices_deferred","TG bound list"],
       "missing_comparison_reports":["B1","B2","B3","B4","T5","B5"]}
    (a.out/"phase447-inventory.json").write_text(json.dumps(j,indent=2)+"\n")
    md=["# P447 Big Merge inventory", "",
        f"Planning rows: {len(rows)}; independent gates: {len(gates)}; default wave: 1 (W2-W8 OFF).",
        "",
        "| Wave | Rows | Driver port status |","|---:|---:|---|"]
    for wave,num in sorted(counts.items()):
        md.append(f"| {wave} | {num} | Pending 5.10 port and runtime test |")
    md+=["","## Unresolved dependencies",
        "- Supplied work order references B1-B5 and T5; these reports were not provided as Phase447 inputs.",
        "- Missing live platform_unbound and devices_deferred from the device.",
        f"- {len(missing)} rows require pinned TG path/DT source mapping.",
        f"- {len(hits)} source trap groups flagged, all SUSPECTED until reviewed.",
        "",
        "## Wave 2 phone test (only AFTER battery drivers are safely ported)",
        "- Verify battery/usb/ac/otg power_supply nodes and Samsung batt_* sysfs nodes.",
        "- Inspect power percentage; plug/unplug charger; run dumpsys battery.",
        "- Never write learned fuel-gauge capacity, firmware, modem NV/EFS or PMIC NVM.",
        "","Note: Compiling a switch is not proof its subsystem works."]
    (a.out/"phase447-summary.md").write_text("\n".join(md)+"\n")
    print(f"P447 MANIFEST PASS rows={len(rows)} switches={len(gates)} unresolved={len(missing)} traps={len(hits)}")
    for wave,num in sorted(counts.items()):print(f"P447 W{wave} rows={num} ported=NO enabled=NO")
if __name__=="__main__":main()
