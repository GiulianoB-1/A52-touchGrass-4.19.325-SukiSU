#!/usr/bin/env python3
"""P450 DSP staging: 5.10-native SM6350 AOSS source compatibility.

IMPORTANT: this alone does NOT bring up AOSS or ADSP. AOSS still requires
a mutually exclusive live-DTB ownership migration, real SM6350 ADSP PAS
descriptor, correct power domains and validated firmware/SSR sequence.
Never enable qcom,qmp-mbox and qcom,sm6350-aoss-qmp together.
"""
import argparse
from pathlib import Path

SRC=Path("drivers/soc/qcom/qcom_aoss.c")
TAG="A52_PHASE450_SM6350_AOSS_SOURCE_PREP_V1"

def once(s,a,b,why):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P450 AOSS {why}: {n} anchors")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:
        if s.count(TAG)!=1:raise RuntimeError("P450 AOSS duplicate patch marker")
        return s
    if "A52_PHASE448A1_IPC_STAGE_V1" not in s:
        raise RuntimeError("P450 AOSS requires verified Phase448 IPC baseline")
    if '"qcom,qmp-mbox"' in s:
        raise RuntimeError("P450 AOSS refuses downstream legacy mailbox binding")
    s=once(s,
       '\t{ .compatible = "qcom,sm8250-aoss-qmp", },',
       '\t/* '+TAG+' */\n'
       '\t{ .compatible = "qcom,sm6350-aoss-qmp", },\n'
       '\t{ .compatible = "qcom,sm8250-aoss-qmp", },',
       "SM6350 compatible")
    # Log after the onecell genpd is registered; no success marker on probe fail.
    s=once(s,
       'platform_set_drvdata(pdev, qmp);\n\n\treturn 0;',
       'platform_set_drvdata(pdev, qmp);\n'
       '    dev_info(&pdev->dev,\n'
       '        "P448 AOSS probe rc=0 pd_count=%u mbox=IPCC native_5_10=1\\n",\n'
       '        qmp->pd_data.num_domains);\n\n\treturn 0;',
       "successful provider publication")
    # Preserve 5.10 AOSS load_state semantics instead of backporting 6.x ABI.
    s=once(s,
       '\treturn qmp_send(res->qmp, buf, sizeof(buf));',
       '''    {
        int rc=qmp_send(res->qmp, buf, sizeof(buf));
        dev_info(res->qmp->dev,
            "P448 AOSS LOAD_STATE name=%s val=%s rc=%d\\n",
            res->pd.name,enable?"on":"off",rc);
        return rc;
    }''',
       "5.10 load_state diagnostic")
    return s
def check(root):
    s=(root/SRC).read_text()
    for marker in (TAG,'"qcom,sm6350-aoss-qmp"',
                   "P448 AOSS probe rc=0 pd_count=%u",
                   "P448 AOSS LOAD_STATE name=%s",
                   "AOSS_QMP_LS_LPASS","AOSS_QMP_LS_MODEM",
                   'res: load_state, name: %s'):
        if marker not in s:raise RuntimeError("P450 AOSS source missing "+marker)
    if '"qcom,qmp-mbox"' in s:
        raise RuntimeError("P450 AOSS legacy QMP alias is UNSAFE")
    print("P450 AOSS SOURCE-ONLY PASS: SM6350 match and 5.10 load_state traces; DTB/PAS NOT PORTED")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args();file=a.root/SRC
    if not a.check_only:file.write_text(patch(file.read_text()))
    check(a.root)
if __name__=="__main__":main()
