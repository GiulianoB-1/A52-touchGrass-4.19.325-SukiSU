#!/usr/bin/env python3
from __future__ import annotations
import argparse, difflib, hashlib, json, re
from pathlib import Path

GKI_ROOT = Path("drivers/a52_display/msm/dsi")
TG_ROOT = Path("techpack/display/msm/dsi")

FILES = (
    "dsi_ctrl.c",
    "dsi_ctrl_hw_cmn.c",
    "dsi_ctrl_hw_2_0.c",
    "dsi_ctrl_hw_2_2.c",
    "dsi_catalog.c",
    "dsi_clk.c",
    "dsi_phy.c",
    "dsi_ctrl_reg.h",
    "dsi_ctrl_hw.h",
)

FUNCTIONS = {
    "dsi_ctrl.c": (
        "dsi_ctrl_dma_cmd_wait_for_done",
        "dsi_ctrl_enable_status_interrupt",
        "dsi_ctrl_disable_status_interrupt",
        "dsi_kickoff_msg_tx",
        "dsi_message_tx",
        "dsi_ctrl_cmd_transfer",
        "dsi_ctrl_isr",
    ),
    "dsi_ctrl_hw_cmn.c": (
        "dsi_ctrl_hw_cmn_kickoff_command",
        "dsi_ctrl_hw_cmn_kickoff_fifo_command",
        "dsi_ctrl_hw_cmn_trigger_command_dma",
        "dsi_ctrl_hw_cmn_get_interrupt_status",
        "dsi_ctrl_hw_cmn_clear_interrupt_status",
        "dsi_ctrl_hw_cmn_enable_status_interrupts",
        "dsi_ctrl_hw_cmn_get_error_status",
        "dsi_ctrl_hw_cmn_clear_error_status",
        "dsi_ctrl_hw_cmn_wait_for_lane_idle",
        "dsi_ctrl_hw_cmn_set_continuous_clk",
    ),
}

TOKENS = (
    "DSI_CMD_MODE_DMA_SW_TRIGGER",
    "DSI_COMMAND_MODE_DMA_CTRL",
    "DSI_DMA_CMD_OFFSET",
    "DSI_DMA_CMD_LENGTH",
    "DSI_DMA_FIFO_CTRL",
    "DSI_INT_CTRL",
    "DSI_STATUS",
    "DSI_FIFO_STATUS",
    "DSI_CLK_CTRL",
    "DSI_CLK_STATUS",
    "DSI_TIMEOUT_STATUS",
    "DSI_ACK_ERR_STATUS",
    "DSI_DLN0_PHY_ERR",
    "wmb()",
    "DSI_CMD_MODE_DMA_DONE",
    "BIT(0)",
    "BIT(1)",
)

def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

def mask_c(text: str) -> str:
    out=list(text); i=0; state="n"; esc=False
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
            depth += 1 if ch=="{" else -1 if ch=="}" else 0
        if depth != 0:
            continue
        p=m.end()-1; d=0; close=-1
        for i in range(p,len(masked)):
            if masked[i]=="(": d+=1
            elif masked[i]==")":
                d-=1
                if d==0: close=i; break
        if close < 0: continue
        tail=masked[close+1:close+1500]
        br=tail.find("{"); semi=tail.find(";")
        if br < 0 or (semi >= 0 and semi < br): continue
        opening=close+1+br; d=0
        for i in range(opening,len(masked)):
            if masked[i]=="{": d+=1
            elif masked[i]=="}":
                d-=1
                if d==0:
                    start=text.rfind("\n",0,m.start())+1
                    return text[start:i+1]
    return None

def normalize(s: str) -> str:
    s=re.sub(r"/\*.*?\*/","",s,flags=re.S)
    s=re.sub(r"//[^\n]*","",s)
    lines=[]
    for line in s.splitlines():
        if "a52_ackfr_record(" in line or "A52_PHASE" in line or "A52_P" in line:
            continue
        lines.append(line)
    return re.sub(r"\s+","","\n".join(lines))

def diff_excerpt(a: str, b: str, limit=200):
    return list(difflib.unified_diff(a.splitlines(), b.splitlines(),
        fromfile="TouchGrass", tofile="GKI", lineterm=""))[:limit]

def token_counts(s: str):
    return {t:s.count(t) for t in TOKENS}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--gki",type=Path,required=True)
    ap.add_argument("--touchgrass",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    ns=ap.parse_args()
    ns.out.mkdir(parents=True,exist_ok=True)

    report={"status":"phase344-dsi-dma-touchgrass-audit-v1","files":[],"functions":[],"findings":[]}
    for name in FILES:
        gp=ns.gki/GKI_ROOT/name; tp=ns.touchgrass/TG_ROOT/name
        gs,ts=read(gp),read(tp)
        report["files"].append({
            "file":name,"gki_exists":bool(gs),"touchgrass_exists":bool(ts),
            "byte_identical":bool(gs and ts and gs==ts),
            "gki_sha256":sha(gs) if gs else None,
            "touchgrass_sha256":sha(ts) if ts else None,
            "gki_tokens":token_counts(gs),"touchgrass_tokens":token_counts(ts),
        })
        for fn in FUNCTIONS.get(name,()):
            gf,tf=extract_function(gs,fn),extract_function(ts,fn)
            row={"file":name,"function":fn,"gki_present":gf is not None,"touchgrass_present":tf is not None}
            if gf is not None and tf is not None:
                gn,tn=normalize(gf),normalize(tf)
                row.update({"normalized_equal":gn==tn,
                    "gki_norm_sha256":sha(gn),"touchgrass_norm_sha256":sha(tn)})
                if gn != tn:
                    row["diff_excerpt"]=diff_excerpt(tf,gf)
                    (ns.out/f"{name}__{fn}.diff".replace("/","__")).write_text(
                        "\n".join(row["diff_excerpt"])+"\n",encoding="utf-8")
            report["functions"].append(row)

    byfn={x["function"]:x for x in report["functions"]}
    critical=(
        "dsi_ctrl_hw_cmn_kickoff_command",
        "dsi_ctrl_hw_cmn_trigger_command_dma",
        "dsi_ctrl_hw_cmn_get_interrupt_status",
        "dsi_ctrl_hw_cmn_clear_interrupt_status",
        "dsi_ctrl_hw_cmn_enable_status_interrupts",
        "dsi_ctrl_dma_cmd_wait_for_done",
        "dsi_kickoff_msg_tx",
    )
    differing=[f for f in critical if byfn.get(f,{}).get("normalized_equal") is False]
    equal=[f for f in critical if byfn.get(f,{}).get("normalized_equal") is True]
    missing=[f for f in critical if not (byfn.get(f,{}).get("gki_present") and byfn.get(f,{}).get("touchgrass_present"))]

    if equal:
        report["findings"].append({
            "severity":"HIGH_CONFIDENCE","key":"critical-source-parity",
            "title":"Core SW_TRIGGER/DMA_DONE functions are source-parity after removing recorder-only instrumentation",
            "functions":equal,
            "implication":"If hardware diverges immediately after SW_TRIGGER, the cause is increasingly likely to be state established outside these function bodies rather than a different trigger write or IRQ bit definition."
        })
    if differing:
        report["findings"].append({
            "severity":"HIGH","key":"critical-source-diff",
            "title":"One or more core trigger/completion functions still differ from TouchGrass",
            "functions":differing,
            "implication":"Inspect these exact diffs before designing the Golden recorder."
        })
    if missing:
        report["findings"].append({
            "severity":"MEDIUM","key":"critical-function-missing",
            "title":"Some named critical functions could not be paired",
            "functions":missing,
            "implication":"Version-specific naming may differ; inspect file-level diffs and ops tables."
        })

    reg_g=next((x for x in report["files"] if x["file"]=="dsi_ctrl_reg.h"),{})
    if reg_g.get("gki_exists") and reg_g.get("touchgrass_exists"):
        report["findings"].append({
            "severity":"INFO","key":"register-map-hash",
            "title":"DSI register-map header comparison captured",
            "byte_identical":reg_g.get("byte_identical"),
            "implication":"A register-offset/bit-definition mismatch would be visible here."
        })

    outjson=ns.out/"phase344-dsi-dma-audit.json"
    outjson.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")

    md=["# Phase344 DSI DMA TouchGrass audit","",
        "Exact comparison of the current DSI source point to pinned TouchGrass around SW_TRIGGER and DMA_DONE.","",
        "## Critical functions","",
        "| Function | Present both | Normalized equal |",
        "|---|---:|---:|"]
    for f in critical:
        x=byfn.get(f,{})
        both=x.get("gki_present") and x.get("touchgrass_present")
        eq=x.get("normalized_equal")
        md.append(f"| {f} | {'yes' if both else 'no'} | {'yes' if eq is True else 'no' if eq is False else 'n/a'} |")
    md += ["","## Findings",""]
    for x in report["findings"]:
        md += [f"### [{x['severity']}] {x['title']}",x.get("implication",""),""]
    (ns.out/"PHASE344-DMA-AUDIT.md").write_text("\n".join(md)+"\n",encoding="utf-8")

    print(json.dumps({
        "critical_equal":equal,
        "critical_differing":differing,
        "critical_missing":missing,
        "reg_header_identical":reg_g.get("byte_identical"),
        "findings":report["findings"],
    },indent=2))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
