#!/usr/bin/env python3
"""P458 DSP opt-in SCM and fail-closed legacy Venus/NPU freeze.

Keep P457 SMMU, ICE and nine SCM availability caller freezes byte-for-byte.
Do not touch PAS firmware or auto_boot. Preserve existing deferred devices
until the explicit 0200 sysfs SCM write; only ADSP can bind afterwards.
"""
import argparse
from pathlib import Path

MARK="A52_P458_LEGACY_VENUS_NPU_PROBE_FREEZE_V1"
SMEM_MARK="A52_P458_SMP2P_NPU_DEFER_FROZEN_V1"
SCM_MARK="A52_P458_SCM_RUNTIME_ONLY_V1"
SCM=Path("drivers/a52_447/a52_dsp_scm_runtime.c")
MK=Path("drivers/a52_447/Makefile")
SMP=Path("drivers/soc/qcom/smp2p.c")
PIL_CANDIDATES=(
    Path("drivers/a52_pil/subsys-pil-tz.c"),
    Path("drivers/soc/qcom/subsys-pil-tz.c"),
)
LOCAL=Path("patches/p458/a52_dsp_scm_runtime.c")

PIL_BEGIN="static int pil_tz_driver_probe(struct platform_device *pdev)\n{"
PIL_HOLD=r'''
    /* A52_P458_LEGACY_VENUS_NPU_PROBE_FREEZE_V1
     * SCM binding retries every deferred probe. Do NOT allow legacy
     * Venus/NPU SSR/PIL to register or load firmware on Lagoon.
     * Deliberate -EPROBE_DEFER leaves their original deferred slots
     * present both before and after the manual SCM registration.
     */
    if (of_machine_is_compatible("qcom,lagoon") &&
        (!strcmp(dev_name(&pdev->dev),"aab0000.qcom,venus") ||
         !strcmp(dev_name(&pdev->dev),"9800000.qcom,npu"))) {
        dev_info_once(&pdev->dev,
                      "P458 DSP FREEZE legacy_PIL=%s no_firmware=1\n",
                      dev_name(&pdev->dev));
        return -EPROBE_DEFER;
    }
'''
SMP_BEGIN="static int qcom_smp2p_probe(struct platform_device *pdev)\n{"
SMP_HOLD=r'''
    /* A52_P458_SMP2P_NPU_DEFER_FROZEN_V1: no NPU edge activation
     * on SCM bind; keep unrelated modem/ADSP SMP2P untouched.
     */
    if (of_machine_is_compatible("qcom,lagoon") &&
        !strcmp(dev_name(&pdev->dev),"soc:qcom,smp2p-npu")) {
        dev_info_once(&pdev->dev,
                      "P458 DSP FREEZE smp2p-npu no_remote=1\n");
        return -EPROBE_DEFER;
    }
'''

def pil_path(root):
    found=[root/p for p in PIL_CANDIDATES if (root/p).is_file()]
    if len(found)!=1:
        raise RuntimeError("P458 expected exactly one generated legacy PIL-TZ C source, found "+repr(found))
    return found[0]

def once(s,old,new,what):
    n=s.count(old)
    if n!=1:
        raise RuntimeError("P458 "+what+" anchor count="+str(n))
    return s.replace(old,new,1)

def patch(root):
    pil=pil_path(root)
    s=pil.read_text()
    if MARK not in s:
        s=once(s,PIL_BEGIN,PIL_BEGIN+PIL_HOLD,"PIL probe")
        pil.write_text(s)
    sp=root/SMP
    s=sp.read_text()
    if SMEM_MARK not in s:
        s=once(s,SMP_BEGIN,SMP_BEGIN+SMP_HOLD,"NPU SMP2P probe")
        sp.write_text(s)
    src=LOCAL.read_text()
    if src.count("#define "+SCM_MARK)!=1:
        raise RuntimeError("P458 SCM creator source marker drift")
    dst=root/SCM
    if dst.exists() and dst.read_text()!=src:
        raise RuntimeError("P458 SCM creator source changed unexpectedly")
    dst.write_text(src)
    mk=root/MK
    old=mk.read_text()
    line="obj-y += a52_dsp_scm_runtime.o\n"
    if line not in old:
        if old.count("a52_dsp_scm_runtime.o"):
            raise RuntimeError("P458 duplicate Kbuild path")
        mk.write_text(old+("\n" if not old.endswith("\n") else "")+line)

def check(root):
    pil=pil_path(root).read_text()
    smp=(root/SMP).read_text()
    scm=(root/SCM).read_text()
    mk=(root/MK).read_text()
    for marker,src in (
        (MARK,pil),(SMEM_MARK,smp),(SCM_MARK,scm),
        ('"aab0000.qcom,venus"',pil),('"9800000.qcom,npu"',pil),
        ('"soc:qcom,smp2p-npu"',smp),
        ("return -EPROBE_DEFER;",PIL_HOLD),("return -EPROBE_DEFER;",SMP_HOLD),
        ('__ATTR(scm_enable, 0200, NULL, scm_enable_store)',scm),
        ('late_initcall(a52_p458_scm_knob_init);',scm),
        ('platform_device_alloc("firmware:scm", PLATFORM_DEVID_NONE)',scm),
        ('pdev->dev.of_node = of_node_get(np);',scm),
        ('platform_device_add(pdev);',scm),
        ('P458 SCM RUNTIME_ENABLE rc=%d',scm),
        ('if (a52_scm_attempted)',scm),
        ('a52_scm_attempted = true;',scm),
    ):
        if marker not in src:
            raise RuntimeError("P458 mandatory dormant runtime marker missing: "+marker)
    if mk.count("obj-y += a52_dsp_scm_runtime.o")!=1:
        raise RuntimeError("P458 SCM sysfs creator must link once")
    if scm.count("platform_device_add(pdev)")!=1 or scm.count("platform_device_alloc(")!=1:
        raise RuntimeError("P458 multiple SCM creator callsites")
    # Inspect executable tokens, not C comments. The source explicitly
    # documents why of_platform_device_create*() must NOT be called; a
    # substring gate incorrectly rejected that safety comment in #40119825644.
    import re
    executable = re.sub(r"/\\*.*?\\*/|//[^\\n]*", "", scm, flags=re.S)
    forbidden = (
        r"\\bof_changeset_[A-Za-z0-9_]*\\s*\\(",
        r"\\bof_platform_device_create[A-Za-z0-9_]*\\s*\\(",
        r"\\bof_platform_populate\\s*\\(",
        r"\\bof_property_write_[A-Za-z0-9_]*\\s*\\(",
    )
    if any(re.search(expr, executable) for expr in forbidden):
        raise RuntimeError("P458 cannot turn on OF node globally")
    # Regression gate: a forbidden API name in a comment is benign, but
    # an actual call in C must fail the gate.
    comment_demo = "/* of_platform_device_create() must never run */"
    assert not re.search(forbidden[1], re.sub(r"/\\*.*?\\*/", "", comment_demo, flags=re.S))
    assert re.search(forbidden[1], "of_platform_device_create(node, NULL, NULL)")

    if scm.count("a52_p458_create_scm_once()")!=1:
        raise RuntimeError("P458 creation call must occur only from sysfs")
    if ("rproc_boot(" in scm or "request_firmware(" in scm or
        "subsys_get(" in scm):
        raise RuntimeError("P458 SCM control must not boot any remote")
    print("P458 DSP SOURCE PASS: one-shot root knob dormant, Venus/NPU probe-frozen, smp2p-npu deferred; no SMMU/ICE/PAS modifications")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:patch(a.root)
    check(a.root)

if __name__=="__main__":
    main()
