#!/usr/bin/env python3
from __future__ import annotations
import argparse, difflib, hashlib, json, re
from pathlib import Path

PAIRS = {
 "dsi_catalog.c": ("drivers/a52_display/msm/dsi/dsi_catalog.c", (
   "dsi_catalog_ctrl_setup", "dsi_catalog_phy_setup")),
 "dsi_ctrl_hw_2_0.c": ("drivers/a52_display/msm/dsi/dsi_ctrl_hw_2_0.c", (
   "dsi_ctrl_hw_20_setup_lane_map", "dsi_ctrl_hw_20_wait_for_lane_idle",
   "dsi_ctrl_hw_20_setup_misr", "dsi_ctrl_hw_20_collect_misr")),
 "dsi_ctrl_hw_2_2.c": ("drivers/a52_display/msm/dsi/dsi_ctrl_hw_2_2.c", (
   "dsi_ctrl_hw_22_setup_lane_map", "dsi_ctrl_hw_22_wait_for_lane_idle")),
 "dsi_phy_hw_v3_0.c": ("drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c", (
   "dsi_phy_hw_v3_0_enable", "dsi_phy_hw_v3_0_disable",
   "dsi_phy_hw_v3_0_wait_for_lane_idle", "dsi_phy_hw_v3_0_config_lpcdrx",
   "dsi_phy_hw_v3_0_lane_settings")),
 "dsi_phy.c": ("drivers/a52_display/msm/dsi/dsi_phy.c", (
   "dsi_phy_enable", "dsi_phy_disable", "dsi_phy_set_clamp_state",
   "dsi_phy_sw_reset")),
}

TG_PREFIX="techpack/display/msm/dsi/"

def mask_c(t):
 out=list(t); i=0; st="n"; esc=False
 while i<len(t):
  c=t[i]; n=t[i+1] if i+1<len(t) else ""
  if st=="n":
   if c=="/" and n=="/": out[i]=out[i+1]=" "; st="l"; i+=2; continue
   if c=="/" and n=="*": out[i]=out[i+1]=" "; st="b"; i+=2; continue
   if c=='"': out[i]=" "; st="s"; esc=False
   elif c=="'": out[i]=" "; st="c"; esc=False
  elif st=="l":
   if c=="\n": st="n"
   else: out[i]=" "
  elif st=="b":
   if c=="*" and n=="/": out[i]=out[i+1]=" "; st="n"; i+=2; continue
   if c!="\n": out[i]=" "
  else:
   q='"' if st=="s" else "'"
   if c!="\n":
    out[i]=" "
    if esc: esc=False
    elif c=="\\": esc=True
    elif c==q: st="n"
  i+=1
 return "".join(out)

def func(t,name):
 msk=mask_c(t)
 for m in re.finditer(r"\b"+re.escape(name)+r"\s*\(",msk):
  p=m.end()-1; d=0; close=-1
  for i in range(p,len(msk)):
   if msk[i]=="(": d+=1
   elif msk[i]==")":
    d-=1
    if d==0: close=i; break
  if close<0: continue
  tail=msk[close+1:close+4096]; br=tail.find("{"); semi=tail.find(";")
  if br<0 or (semi>=0 and semi<br): continue
  op=close+1+br; d=0
  for i in range(op,len(msk)):
   if msk[i]=="{": d+=1
   elif msk[i]=="}":
    d-=1
    if d==0:
     return t[t.rfind("\n",0,m.start())+1:i+1]
 return None

def norm(s):
 if s is None:return None
 s=re.sub(r"/\*.*?\*/","",s,flags=re.S)
 s=re.sub(r"//[^\n]*","",s)
 lines=[]
 skip=False; depth=0
 for line in s.splitlines():
  if skip:
   depth += line.count("(")-line.count(")")
   if ";" in line and depth<=0: skip=False
   continue
  if any(x in line for x in ("a52_","A52_","P276","P303","P307","P308","P314","P316","P319")):
   if "(" in line and ";" not in line:
    skip=True; depth=line.count("(")-line.count(")")
   continue
  lines.append(line)
 return re.sub(r"\s+","", "\n".join(lines))

def h(s): return hashlib.sha256(s.encode()).hexdigest() if s is not None else None

def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--gki",type=Path,required=True); ap.add_argument("--tg",type=Path,required=True); ap.add_argument("--out",type=Path,required=True)
 a=ap.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
 rows=[]
 for label,(gp,names) in PAIRS.items():
  tp=TG_PREFIX+label
  gs=(a.gki/gp).read_text(errors="replace") if (a.gki/gp).is_file() else ""
  ts=(a.tg/tp).read_text(errors="replace") if (a.tg/tp).is_file() else ""
  for name in names:
   gf,tf=func(gs,name),func(ts,name); gn,tn=norm(gf),norm(tf)
   eq=(gn==tn) if gn is not None and tn is not None else None
   rows.append({"file":label,"function":name,"gki_present":gf is not None,"tg_present":tf is not None,"equal":eq,"gki_sha":h(gn),"tg_sha":h(tn),"gki_len":len(gn) if gn else None,"tg_len":len(tn) if tn else None})
   if eq is False:
    diff=list(difflib.unified_diff((tf or "").splitlines(),(gf or "").splitlines(),fromfile="TouchGrass",tofile="GKI",lineterm=""))[:180]
    (a.out/(label+"__"+name+".diff")).write_text("\n".join(diff)+"\n")
 (a.out/"results.json").write_text(json.dumps(rows,indent=2)+"\n")
 exact=sum(r["equal"] is True for r in rows); diffn=sum(r["equal"] is False for r in rows); miss=sum(r["equal"] is None for r in rows)
 report=["# Phase414C DSI hardware-near source audit","",f"- exact: **{exact}**",f"- different: **{diffn}**",f"- missing: **{miss}**","",
 "| File | Function | Result |","|---|---|---|"]
 for r in rows:
  v="exact" if r["equal"] is True else "different" if r["equal"] is False else "missing"
  report.append(f'| {r["file"]} | {r["function"]} | **{v}** |')
 (a.out/"REPORT.md").write_text("\n".join(report)+"\n"); print("\n".join(report))
if __name__=="__main__": main()
