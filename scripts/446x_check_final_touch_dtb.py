#!/usr/bin/env python3
"""Check actual A526B boot FDT for applied A52 R02 STM overlay, fail closed.

Inspect packed DTB (not DTS source or an unapplied fragment). Support concatenated
FDTs and reject an entry if the touchscreen is disabled or supplies absent.
Extract through the same Android boot header v2 parser used by packaging.
"""
import argparse,re,json,runpy,struct,subprocess,tempfile
from pathlib import Path
MAGIC=b"\xd0\x0d\xfe\xed"
def trees(data):
    off=0
    while True:
        p=data.find(MAGIC,off)
        if p<0:break
        if p+8>len(data):break
        n=struct.unpack_from(">I",data,p+4)[0]
        if 40<=n<=len(data)-p:
            yield p,data[p:p+n]
            off=p+n
        else:off=p+4

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--boot",type=Path,required=True)
    ap.add_argument("--report",type=Path,required=True);a=ap.parse_args()
    parser=runpy.run_path("scripts/38_repack_a52_p1_boot.py")["parse_boot"]
    d=parser(a.boot.read_bytes())
    blobs=[("boot_dtb",d["dtb"]),("recovery_dtbo",d.get("recovery_dtbo",b""))]
    rows=[]
    with tempfile.TemporaryDirectory() as tmp:
        for label,raw in blobs:
            for ix,(ofs,tree) in enumerate(trees(raw)):
                path=Path(tmp)/"entry.dtb";path.write_bytes(tree)
                p=subprocess.run(["dtc","-I","dtb","-O","dts",str(path)],
                                 capture_output=True,text=True)
                if p.returncode:continue
                dts=p.stdout
                nodes=list(re.finditer(r'touchscreen@49\s*\{([^{}]*)\}',dts,re.S))
                for match in nodes:
                    body=match.group(1)
                    status=re.search(r'\bstatus\s*=\s*"([^"]+)"',body)
                    row={"source":label,"dtb_index":ix,"offset":ofs,
                         "node":"touchscreen@49","compatible_stm":bool(re.search(r'compatible\s*=\s*"stm,fts_touch"',body)),
                         "status":status.group(1) if status else "okay(default)",
                         "irq_gpio": "stm,irq_gpio" in body,
                         "interrupts": "interrupts" in body,
                         "avdd_supply":"avdd-supply" in body,
                         "dvdd_supply":"dvdd-supply" in body,
                         "fw_name":"tsp_stm/fts5cu56a_a52xq.bin" in body,
                         "node_excerpt":body[:2200]}
                    rows.append(row)
    passed=any(row["source"]=="boot_dtb" and row["compatible_stm"] and
               row["status"] not in ("disabled","fail") and row["irq_gpio"] and
               row["avdd_supply"] and row["dvdd_supply"] and row["fw_name"]
               for row in rows)
    a.report.write_text(json.dumps({"applied_to_boot_dtb":passed,"candidates":rows},
                                   indent=2,sort_keys=True)+"\n")
    if not passed:
        raise RuntimeError("P446X Touch overlay NOT applied to final boot DTB: touchscreen@49 stm,fts_touch required with IRQ/regulators/firmware; report "+str(a.report))
    print("P446X DTB CHECK PASS: R02 touch@49 compatible, GPIO IRQ, avdd/dvdd supplies and firmware found in FINAL boot DTB")

if __name__=="__main__":main()
