#!/usr/bin/env python3
"""P447 pin-a52xq defconfig vs FINAL 5.10 .config for manifest-required drivers.

Never turn configs on just because TG has them. Hardware API parity and
default-off probe gate implementation must precede any CONFIG=y enable.
"""
import argparse,csv,json,re
from pathlib import Path
def config(path):
    result={}
    for line in path.read_text(errors="replace").splitlines():
        m=re.match(r"CONFIG_([A-Za-z0-9_]+)=(.*)",line)
        if m:result[m[1]]=m[2]
        m=re.match(r"# CONFIG_([A-Za-z0-9_]+) is not set",line)
        if m:result[m[1]]="n"
    return result
def main():
    p=argparse.ArgumentParser()
    for name in ("tg","ack","manifest","out"):p.add_argument("--"+name,required=True,type=Path)
    a=p.parse_args()
    x,y=config(a.tg),config(a.ack)
    rows=[]
    with a.manifest.open(newline="") as f:
        for r in csv.DictReader(f):
            for name in r["Kconfig"].split(";"):
                name=name.removeprefix("CONFIG_").strip()
                if not name or name=="UNVERIFIED":continue
                rows.append([r["wave"],r["subsystem"],r["function"],name,
                            x.get(name,"NOT_FOUND"),y.get(name,"NOT_FOUND"),
                            str(x.get(name)!=y.get(name)),
                            "REVIEW - config diff is NOT proof of a safe port"])
    a.out.mkdir(parents=True,exist_ok=True)
    with (a.out/"phase447-config-diff.csv").open("w",newline="") as f:
        w=csv.writer(f)
        w.writerow(["wave","subsystem","function","config","TG-a52xq","GKI-final","diff","action"])
        w.writerows(rows)
    summary={"manifest_config_checks":len(rows),
             "mismatches":sum(r[6]=="True" for r in rows),
             "tg_enabled_gki_not_enabled":sum(r[4]=="y" and r[5]!="y" for r in rows),
             "message":"Informational until runtime gate and port are individually verified; all W2-W8 stay OFF"}
    (a.out/"phase447-config-diff-summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print("P447 CONFIG DIFF "+str(summary))
if __name__=="__main__":main()
