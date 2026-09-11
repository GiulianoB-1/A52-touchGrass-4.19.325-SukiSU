#!/usr/bin/env python3
from __future__ import annotations
import argparse, difflib, hashlib, json, re
from pathlib import Path

DISPLAY_PAIRS = {
    "dsi/dsi_display.c": (
        "dsi_display_aspace_cb_locked",
        "dsi_host_alloc_cmd_tx_buffer",
        "dsi_config_host_engine_state_for_cont_splash",
        "dsi_display_cont_splash_config",
        "dsi_display_splash_res_cleanup",
        "dsi_display_config_ctrl_for_cont_splash",
        "dsi_display_wake_up",
        "dsi_display_cmd_engine_enable",
        "dsi_host_transfer",
    ),
    "dsi/dsi_ctrl.c": (
        "dsi_ctrl_set_power_state",
        "dsi_ctrl_set_cmd_engine_state",
        "dsi_ctrl_cmd_transfer",
        "dsi_message_tx",
        "dsi_kickoff_msg_tx",
        "dsi_ctrl_dma_cmd_wait_for_done",
    ),
    "dsi/dsi_phy.c": (
        "dsi_phy_enable",
        "dsi_phy_disable",
    ),
    "msm_gem.c": (
        "msm_gem_get_iova",
        "msm_gem_put_iova",
    ),
    "msm_gem_vma.c": (
        "msm_gem_smmu_address_space_get",
    ),
    "msm_smmu.c": (),
}

TG_ROOT = Path("techpack/display/msm")
GKI_ROOT = Path("drivers/a52_display/msm")

TOKENS = {
    "cmd_buffer_uncached": "MSM_BO_UNCACHED",
    "cmd_buffer_unsecure_aspace": "MSM_SMMU_DOMAIN_UNSECURE",
    "cmd_buffer_get_iova": "msm_gem_get_iova",
    "cmd_buffer_iova_field": "cmd_buffer_iova",
    "samsung_cmd_buffer_1m": "display_ctrl->ctrl->cmd_buffer_size = SZ_1M",
}

SMMU_TOKENS = {
    "skip_init": "qcom,skip-init",
    "three_level_tables": "qcom,use-3-lvl-tables",
    "actlr": "qcom,actlr",
    "tbu_compatible": "qcom,qsmmuv500-tbu",
    "dma_addr_pool": "qcom,iommu-dma-addr-pool",
    "earlymap": "qcom,iommu-earlymap",
    "cache_lock_symbol": "ARM_MMU500_ACR_CACHE_LOCK",
    "sacr_register": "ARM_SMMU_GR0_sACR",
    "tbu_register": "qsmmuv500_tbu_register",
    "tbu_probe": "qsmmuv500_tbu_probe",
    "arch_init": "qsmmuv500_arch_init",
    "actlr_init_cb": "qsmmuv500_init_cb",
}

def read(p: Path) -> str:
    return p.read_text(errors="replace") if p.is_file() else ""

def sha(s: str) -> str:
    return hashlib.sha256(s.encode(errors="replace")).hexdigest()

def mask_c(text: str) -> str:
    out = list(text)
    i=0; state="n"; esc=False
    while i < len(text):
        c=text[i]; n=text[i+1] if i+1<len(text) else ""
        if state=="n":
            if c=="/" and n=="/":
                out[i]=out[i+1]=" "; state="l"; i+=2; continue
            if c=="/" and n=="*":
                out[i]=out[i+1]=" "; state="b"; i+=2; continue
            if c=='"': out[i]=" "; state="s"; esc=False
            elif c=="'": out[i]=" "; state="c"; esc=False
        elif state=="l":
            if c=="\n": state="n"
            else: out[i]=" "
        elif state=="b":
            if c=="*" and n=="/":
                out[i]=out[i+1]=" "; state="n"; i+=2; continue
            if c!="\n": out[i]=" "
        else:
            q='"' if state=="s" else "'"
            if c!="\n":
                out[i]=" "
                if esc: esc=False
                elif c=="\\": esc=True
                elif c==q: state="n"
        i+=1
    return "".join(out)

def extract_function(text: str, name: str) -> str | None:
    masked=mask_c(text)
    for m in re.finditer(r"\b"+re.escape(name)+r"\s*\(", masked):
        depth=0
        for ch in masked[:m.start()]:
            if ch=="{": depth+=1
            elif ch=="}": depth-=1
        if depth != 0:
            continue
        p=m.end()-1; d=0; close=-1
        for i in range(p,len(masked)):
            if masked[i]=="(": d+=1
            elif masked[i]==")":
                d-=1
                if d==0: close=i; break
        if close<0: continue
        tail=masked[close+1:close+2000]
        br=tail.find("{"); semi=tail.find(";")
        if br<0 or (semi>=0 and semi<br): continue
        opening=close+1+br
        d=0
        for i in range(opening,len(masked)):
            if masked[i]=="{": d+=1
            elif masked[i]=="}":
                d-=1
                if d==0:
                    start=text.rfind("\n",0,m.start())+1
                    return text[start:i+1]
    return None

def normalize_function(s: str) -> str:
    s=re.sub(r"/\*.*?\*/","",s,flags=re.S)
    s=re.sub(r"//[^\n]*","",s)
    lines=[]
    for line in s.splitlines():
        if "a52_ackfr_record(" in line or "A52_PHASE" in line:
            continue
        if re.search(r"\ba52_p(?:276|293|303|307|308|310|312|314|316|319|329|330|331|332|333)_", line):
            continue
        lines.append(line)
    s="\n".join(lines)
    return re.sub(r"\s+","",s)

def diff_excerpt(a: str, b: str, limit=80):
    return list(difflib.unified_diff(a.splitlines(),b.splitlines(),fromfile="TouchGrass",tofile="GKI",lineterm=""))[:limit]

def token_map(text: str, mapping: dict[str,str]) -> dict[str,bool]:
    return {k:(v in text) for k,v in mapping.items()}

def scan_dt(root: Path) -> dict[str, list[str]]:
    needles = (
        "qcom,skip-init", "qcom,use-3-lvl-tables", "qcom,actlr",
        "qcom,qsmmuv500-tbu", "qcom,iommu-dma-addr-pool",
        "qcom,iommu-earlymap", "qcom,stream-id-range",
        "qcom,cont-splash-enabled", "power-domains", "interconnects",
    )
    out={n:[] for n in needles}
    if not root.exists():
        return out
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in (".dts",".dtsi"):
            continue
        try: s=p.read_text(errors="replace")
        except OSError: continue
        for n in needles:
            if n in s:
                for line in s.splitlines():
                    if n in line:
                        out[n].append(f"{p.relative_to(root)}:{line.strip()}")
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--gki",type=Path,required=True)
    ap.add_argument("--touchgrass",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    ns=ap.parse_args()
    ns.out.mkdir(parents=True,exist_ok=True)

    funcs=[]
    files=[]
    for rel,names in DISPLAY_PAIRS.items():
        gp=ns.gki/GKI_ROOT/rel
        tp=ns.touchgrass/TG_ROOT/rel
        gs,ts=read(gp),read(tp)
        files.append({
            "file":rel,"gki_exists":gp.is_file(),"touchgrass_exists":tp.is_file(),
            "gki_sha256":sha(gs) if gs else None,"touchgrass_sha256":sha(ts) if ts else None,
            "byte_identical": bool(gs and ts and gs==ts),
        })
        for fn in names:
            gf,tf=extract_function(gs,fn),extract_function(ts,fn)
            rec={"file":rel,"function":fn,"gki_present":gf is not None,"touchgrass_present":tf is not None}
            if gf is not None and tf is not None:
                gn,tn=normalize_function(gf),normalize_function(tf)
                rec.update({
                    "normalized_equal":gn==tn,
                    "gki_norm_sha256":sha(gn),
                    "touchgrass_norm_sha256":sha(tn),
                    "diff_excerpt":[] if gn==tn else diff_excerpt(tf,gf),
                })
            funcs.append(rec)

    gki_disp=read(ns.gki/GKI_ROOT/"dsi/dsi_display.c")
    tg_disp=read(ns.touchgrass/TG_ROOT/"dsi/dsi_display.c")
    cmd_contract={
        "gki":token_map(gki_disp,TOKENS),
        "touchgrass":token_map(tg_disp,TOKENS),
    }
    cmd_contract["same"]={k:cmd_contract["gki"][k]==cmd_contract["touchgrass"][k] for k in TOKENS}

    gki_smmu=read(ns.gki/"drivers/iommu/arm/arm-smmu/arm-smmu.c")
    gki_smmu_qcom=read(ns.gki/"drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c")
    tg_smmu=read(ns.touchgrass/"drivers/iommu/arm-smmu.c")
    gki_smmu_all=gki_smmu+"\n"+gki_smmu_qcom
    smmu={
        "gki":token_map(gki_smmu_all,SMMU_TOKENS),
        "touchgrass":token_map(tg_smmu,SMMU_TOKENS),
    }
    smmu["same"]={k:smmu["gki"][k]==smmu["touchgrass"][k] for k in SMMU_TOKENS}

    tg_arch=extract_function(tg_smmu,"qsmmuv500_arch_init") or ""
    gki_has_cache_unlock=(
        "ARM_MMU500_ACR_CACHE_LOCK" in gki_smmu_all and
        "ARM_SMMU_GR0_sACR" in gki_smmu_all
    )
    tg_has_cache_unlock=(
        "val &= ~ARM_MMU500_ACR_CACHE_LOCK;" in tg_arch and
        "writel_relaxed(val, reg + ARM_SMMU_GR0_sACR);" in tg_arch
    )

    dt_gki=scan_dt(ns.gki/"arch/arm64/boot/dts")
    dt_tg=scan_dt(ns.touchgrass/"arch/arm64/boot/dts/vendor/qcom")

    findings=[]
    def add(sev,key,title,evidence,next_step):
        findings.append({"severity":sev,"key":key,"title":title,"evidence":evidence,"next_step":next_step})

    alloc=next((x for x in funcs if x["function"]=="dsi_host_alloc_cmd_tx_buffer"),None)
    ascb=next((x for x in funcs if x["function"]=="dsi_display_aspace_cb_locked"),None)
    if alloc and alloc.get("normalized_equal") and ascb and ascb.get("normalized_equal"):
        add("LOW","cmd-buffer-display-front-end-parity",
            "DSI command-buffer allocation and aspace callback are source-parity with TouchGrass",
            "dsi_host_alloc_cmd_tx_buffer and dsi_display_aspace_cb_locked normalize equal, including UNCACHED, UNSECURE aspace and msm_gem_get_iova contract.",
            "Do not spend the next hardware cycle changing the DSI front-end allocation code.")
    else:
        add("HIGH","cmd-buffer-display-front-end-diff",
            "DSI command-buffer allocation/aspace source still differs from TouchGrass",
            "At least one of dsi_host_alloc_cmd_tx_buffer or dsi_display_aspace_cb_locked is not normalized-equal.",
            "Inspect the emitted function diff before any new phone test.")

    if tg_has_cache_unlock and not gki_has_cache_unlock:
        add("HIGH","qsmmuv500-sacr-cache-lock-unported",
            "TouchGrass clears MMU-500 sACR CACHE_LOCK during QSMMUv500 init; current GKI does not",
            "TouchGrass qsmmuv500_arch_init reads ARM_SMMU_GR0_sACR, clears ARM_MMU500_ACR_CACHE_LOCK, writes it back and verifies the bit cleared. No equivalent token exists in current GKI SMMU source.",
            "Treat this as a new pre-trigger SMMU contract candidate. Audit the A52 sACR reset/firmware value and, if safe, make the next A/B test only this initialization semantic.")

    if smmu["touchgrass"]["tbu_compatible"] and not smmu["gki"]["tbu_compatible"]:
        add("MEDIUM","qsmmuv500-tbu-backend-unported",
            "Full TouchGrass QSMMUv500 TBU child backend is still absent",
            "TouchGrass populates qcom,qsmmuv500-tbu children, requires successful TBU probe and keeps per-SID TBU state. Current GKI intentionally preserves firmware state instead of implementing this backend.",
            "Separate normal-translation semantics from debug/ECATS-only semantics before deciding to port it wholesale.")

    ported=[k for k in ("skip_init","three_level_tables","actlr","dma_addr_pool","earlymap") if smmu["gki"].get(k)]
    if ported:
        add("INFO","already-ported-smmu-contracts",
            "Several TouchGrass SMMU contracts are already present in GKI",
            ", ".join(ported),
            "Do not retest these as broad bundles; only test a remaining semantic difference.")

    splash=[x for x in funcs if "splash" in x["function"]]
    splash_diffs=[x["function"] for x in splash if x.get("touchgrass_present") and x.get("gki_present") and x.get("normalized_equal") is False]
    splash_equal=[x["function"] for x in splash if x.get("normalized_equal") is True]
    if splash_diffs:
        add("HIGH","continuous-splash-source-differences",
            "Continuous-splash takeover still has source differences",
            "Different functions: "+", ".join(splash_diffs)+"; equal functions: "+(", ".join(splash_equal) if splash_equal else "none"),
            "Review these exact diffs before any new hardware observer. A difference here can establish the bad q0 state before SW_TRIGGER.")
    elif splash and all(x.get("normalized_equal") for x in splash if x.get("touchgrass_present") and x.get("gki_present")):
        add("LOW","continuous-splash-parity",
            "Compared continuous-splash functions are source-parity with TouchGrass",
            ", ".join(x["function"] for x in splash if x.get("normalized_equal")),
            "Deprioritize splash control-flow source as the q0 root cause.")

    report={
        "status":"phase334-touchgrass-pretrigger-static-audit-v1",
        "files":files,
        "functions":funcs,
        "command_buffer_contract":cmd_contract,
        "smmu_contract":smmu,
        "touchgrass_qsmmuv500_arch_init_has_sacr_cache_unlock":tg_has_cache_unlock,
        "gki_has_equivalent_sacr_cache_unlock_tokens":gki_has_cache_unlock,
        "dt_occurrences":{"gki":dt_gki,"touchgrass":dt_tg},
        "findings":findings,
    }
    (ns.out/"phase334-static-audit.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")

    md=["# Phase334 TouchGrass pre-trigger static audit","",
        "No kernel behavior was changed. This audit compares the current reconstructed GKI tree with pinned TouchGrass source.","",
        "## Prioritized findings",""]
    order={"HIGH":0,"MEDIUM":1,"LOW":2,"INFO":3}
    for f in sorted(findings,key=lambda x:order.get(x["severity"],9)):
        md += ["### ["+f["severity"]+"] "+f["title"],f["evidence"],"","Next: "+f["next_step"],""]
    md += ["## Function comparison","",
           "| Function | File | Present | Normalized equal |",
           "|---|---|---:|---:|"]
    for x in funcs:
        present=x.get("gki_present") and x.get("touchgrass_present")
        eq=x.get("normalized_equal")
        md.append("| "+x["function"]+" | "+x["file"]+" | "+("yes" if present else "no")+" | "+("yes" if eq is True else "no" if eq is False else "n/a")+" |")
    (ns.out/"PHASE334-REPORT.md").write_text("\n".join(md)+"\n")

    for x in funcs:
        if x.get("diff_excerpt"):
            name=x["file"].replace("/","__")+"__"+x["function"]+".diff"
            (ns.out/name).write_text("\n".join(x["diff_excerpt"])+"\n")

    print(json.dumps({"findings":findings},indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
