#!/usr/bin/env python3
"""Phase451 SM6350 ADSP PAS source port: manual start, TG level regulators.

Uses 5.10 qcom_q6v5_pas and existing rpmh-arc-regulator lcx/lmx resources.
No rpmhpd provider, no modem/NV, no charger writes. The boot DTB is
converted separately and MUST pass compiled DTB audits before flashing.
"""
import argparse
from pathlib import Path
SRC=Path("drivers/remoteproc/qcom_q6v5_pas.c")
TAG="A52_PHASE451_SM6350_ADSP_PAS_V1"
def once(s,a,b,name):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P451 PAS {name}: expected one anchor, got {n}")
    return s.replace(a,b,1)
def patch(s):
    if TAG in s:
        if s.count(TAG)!=1:raise RuntimeError("P451 PAS duplicate marker")
        return s
    for marker in ("struct adsp_data {","struct qcom_adsp {",
                   "static int adsp_start(struct rproc *rproc)",
                   "static void qcom_pas_handover(struct qcom_q6v5 *q6v5)",
                   "static const struct adsp_data sm8250_adsp_resource"):
        if marker not in s:raise RuntimeError("P451 PAS missing "+marker)
    s=once(s,"\tbool auto_boot;\n\n\tchar **active_pd_names;",
        "\tbool auto_boot;\n\tbool a52_level_rails;\n\n\tchar **active_pd_names;","descriptor flag")
    s=once(s,"\tbool has_aggre2_clk;\n\tconst char *info_name;",
        "\tbool has_aggre2_clk;\n\tbool a52_level_rails;\n\tconst char *info_name;","runtime flag")
    s=once(s,"adsp->has_aggre2_clk = desc->has_aggre2_clk;",
        "adsp->has_aggre2_clk = desc->has_aggre2_clk;\n"
        "\tadsp->a52_level_rails = desc->a52_level_rails;","descriptor propagation")
    desc=r'''/* A52_PHASE451_SM6350_ADSP_PAS_V1
 * Manual remoteproc bringup only, PAS=1, Samsung/TG A52 LPI regulators.
 * AOSS load_state ID LPASS sends "name: adsp". SSR is "lpass".
 * cx-supply / px-supply in the boot DTB point to TG lcx.lvl/lmx.lvl.
 */
static const struct adsp_data a52_sm6350_adsp_resource = {
    .crash_reason_smem = 423,
    .firmware_name = "adsp.mdt",
    .pas_id = 1,
    .has_aggre2_clk = false,
    .auto_boot = false,
    .a52_level_rails = true,
    .active_pd_names = (char*[]){ "load_state", NULL },
    .ssr_name = "lpass",
    .sysmon_name = "adsp",
    .ssctl_id = 0x14,
};

'''
    s=once(s,"static const struct adsp_data sm8250_adsp_resource = {",
           desc+"static const struct adsp_data sm8250_adsp_resource = {","descriptor insert")
    s=once(s,'\t{ .compatible = "qcom,sm8250-adsp-pas", .data = &sm8250_adsp_resource},',
           '\t{ .compatible = "qcom,sm6350-adsp-pas", .data = &a52_sm6350_adsp_resource},\n'
           '\t{ .compatible = "qcom,sm8250-adsp-pas", .data = &sm8250_adsp_resource},',"DT compatible match")
    s=once(s,"regulator_set_load(adsp->cx_supply, 100000);",
        '''if (!adsp->a52_level_rails)
        regulator_set_load(adsp->cx_supply, 100000);''',"skip nonsensical level regulator load vote")
    start=s.index("static int adsp_start(struct rproc *rproc)")
    end=s.index("static void qcom_pas_handover(",start)
    part=s[start:end]
    part=once(part,'''ret = regulator_enable(adsp->cx_supply);''',
        '''/* TG lcx.lvl/lmx.lvl level 0x180 (384), same downstream
     * regulator provider as display/GPU. Never add a competing rpmhpd.
     * Provider's maximum encoded performance level is 0xffff.
     */
    if (adsp->a52_level_rails) {
        ret=regulator_set_voltage(adsp->cx_supply,384,0xffff);
        if (ret) goto disable_aggre2_clk;
        ret=regulator_set_voltage(adsp->px_supply,384,0xffff);
        if (ret) goto disable_aggre2_clk;
        dev_info(adsp->dev,"P451 ADSP RAILS lcx=384 lmx=384 rc=0\n");
    }
    ret = regulator_enable(adsp->cx_supply);''',"vote before enabling")
    part=once(part,'''disable_aggre2_clk:
\tclk_disable_unprepare(adsp->aggre2_clk);''',
        '''disable_aggre2_clk:
    if (adsp->a52_level_rails) {
        regulator_set_voltage(adsp->px_supply,0,0xffff);
        regulator_set_voltage(adsp->cx_supply,0,0xffff);
    }
\tclk_disable_unprepare(adsp->aggre2_clk);''',"error unvote")
    part=once(part,'''ret = qcom_q6v5_wait_for_start(&adsp->q6v5, msecs_to_jiffies(5000));''',
        '''dev_info(adsp->dev,"P451 ADSP PAS_AUTH rc=0 pas_id=%d fw=%s\n",
             adsp->pas_id,rproc->firmware);
    ret = qcom_q6v5_wait_for_start(&adsp->q6v5, msecs_to_jiffies(5000));''',"PAS auth marker")
    s=s[:start]+part+s[end:]
    start=s.index("static void qcom_pas_handover(")
    end=s.index("static int adsp_stop(",start)
    part=s[start:end]
    part=once(part,"regulator_disable(adsp->cx_supply);",
        '''regulator_disable(adsp->cx_supply);
    if (adsp->a52_level_rails) {
        regulator_set_voltage(adsp->px_supply,0,0xffff);
        regulator_set_voltage(adsp->cx_supply,0,0xffff);
        dev_info(adsp->dev,"P451 ADSP RAILS released handover\n");
    }''',"handover unvote")
    s=s[:start]+part+s[end:]
    start=s.index("static int adsp_probe(")
    end=s.index("static int adsp_remove(",start)
    part=s[start:end]
    part=once(part,"rproc->auto_boot = desc->auto_boot;",
        '''rproc->auto_boot = desc->auto_boot;
    if (desc->a52_level_rails)
        dev_info(&pdev->dev,"P451 ADSP PROBE manual_start=1 auto_boot=0 PAS=1\n");''',"autoboot assertion log")
    s=s[:start]+part+s[end:]
    return s
def check(root):
    s=(root/SRC).read_text()
    for t in (TAG,'"qcom,sm6350-adsp-pas"',"a52_level_rails = true",
              ".auto_boot = false","P451 ADSP RAILS lcx=384",
              "regulator_set_voltage(adsp->cx_supply,384,0xffff)",
              "regulator_set_voltage(adsp->px_supply,384,0xffff)",
              "P451 ADSP PROBE manual_start=1",
              "P451 ADSP PAS_AUTH"):
        if t not in s:raise RuntimeError("P451 PAS missing "+t)
    print("P451 PAS SOURCE PASS: manual-start-only SM6350 PAS=1 / lcx+lmx 384/0xffff, explicit cleanup")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/SRC
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
