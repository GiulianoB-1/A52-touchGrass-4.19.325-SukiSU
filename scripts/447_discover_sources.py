#!/usr/bin/env python3
"""P447 read-only brute-force TG source sweep for W2-W8 port candidates.

Enumerates real files from the pinned TG checkout and compares same paths
against the exact assembled GKI tree. Does NOT copy or compile old 4.19
drivers, and does not invent a DT compatible or falsely claim port success.
"""
import argparse,csv,json,re
from collections import Counter
from pathlib import Path

SCOPES=[
 (2,"battery","drivers/battery/"),
 (2,"sm5714","drivers/mfd/sm/sm5714/"),
 (2,"muic","drivers/muic/"),
 (2,"pdic","drivers/usb/typec/sm/"),
 (2,"adc","drivers/iio/adc/"),
 (2,"adc","drivers/thermal/qcom/"),
 (3,"mss","drivers/remoteproc/"),
 (3,"glink","drivers/rpmsg/"),
 (3,"mss","drivers/soc/qcom/"),
 (3,"ipa","drivers/platform/msm/ipa/"),
 (4,"audio","sound/soc/qcom/"),
 (4,"codec","sound/soc/codecs/"),
 (5,"nfc","drivers/nfc/"),
 (5,"fingerprint","drivers/fingerprint/"),
 (5,"sensors","drivers/sensors/"),
 (5,"vibrator","drivers/vibrator/"),
 (6,"wifi","drivers/net/wireless/"),
 (7,"vidc","drivers/media/platform/msm/"),
 (7,"camera","techpack/camera/"),
 (8,"devbw","drivers/devfreq/"),
 (8,"suspend","drivers/iommu/"),
]
# For broad shared directories, keep only matching subject candidates.
FILTER={
 "drivers/soc/qcom/":r"qmp|glink|pil|q6|ssr|remote|qmi|smp2p|smem|sysmon|subsys|aoss|ipa|pdr",
 "drivers/iio/adc/":r"qcom|spmi|adc",
 "drivers/thermal/qcom/":r"qcom|adc|bcl|smb|qti",
 "drivers/net/wireless/":r"cnss|wlan|cld|qcacld|qca|icnss|wcnss",
 "drivers/devfreq/":r"devbw|bw|memlat|qcom",
 "drivers/iommu/":r"qcom|arm-smmu",
 "sound/soc/codecs/":r"wcd|wsa|qcom|bolero|sm57",
}
SUSPECT={
 "gsi-mapping":r"GENI_IF_FIFO_DISABLE_RO|dma_map_single\(|dma_map_sg\(",
 "nv-write-review":r"write.*(otp|flash|nv)|_burn\(|erase_.*flash",
 "old-api":r"\bset_fs\(|\bget_fs\(|ioremap_nocache\(|setup_timer\(",
 "fw-fallback":r"request_firmware\(",
 "irq-order-review":r"enable_irq\(",
}
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--tg",type=Path,required=True)
    ap.add_argument("--ack",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    found={}
    for wave,group,prefix in SCOPES:
        path=args.tg/prefix
        if not path.is_dir():continue
        pat=FILTER.get(prefix)
        for f in path.rglob("*.c"):
            rel=f.relative_to(args.tg).as_posix()
            if pat and not re.search(pat,rel,re.I):continue
            if rel not in found:found[rel]=(wave,group)
    rows=[];counts=Counter();ack_count=Counter();flags=Counter()
    for rel,(wave,group) in sorted(found.items()):
        tgfile=args.tg/rel
        same=(args.ack/rel).is_file()
        counts[wave]+=1
        ack_count[wave]+=int(same)
        text=tgfile.read_text(errors="replace")
        codes=[name for name,pat in SUSPECT.items() if re.search(pat,text,re.I)]
        for code in codes:flags[code]+=1
        rows.append([wave,group,rel,str(int(same)), "NOT_PORTED",
                    "SOURCE_CANDIDATE_ONLY",";".join(codes)])
    args.out.mkdir(parents=True,exist_ok=True)
    with (args.out/"tg-source-sweep.csv").open("w",newline="") as f:
        w=csv.writer(f)
        w.writerow(["wave_candidate","subsystem_candidate","TG source path",
                    "same path in assembled GKI?","port status","classification",
                    "potential trap flags (REVIEW only)"])
        w.writerows(rows)
    j={"pinned_tg_source_candidates":len(rows),"by_wave":dict(counts),
       "same_path_in_gki":dict(ack_count),"potential_trap_counts":dict(flags),
       "warning":"Source enumeration != compatible match, port, successful build or binding"}
    (args.out/"tg-source-sweep-summary.json").write_text(json.dumps(j,indent=2)+"\n")
    print(f"P447 SOURCE SWEEP PASS candidates={len(rows)} same_path={sum(ack_count.values())}")
    for wave in sorted(counts):
        print(f"P447 SOURCE W{wave} candidate_c_files={counts[wave]} same_gki_path={ack_count[wave]} state=NOT_PORTED")
if __name__=="__main__":main()
