#!/usr/bin/env python3
"""P446AA read-only diagnostics: GENI SE8 live-clock/IRQ and HWC raw DRM atomic properties.

Touch, KSU, display power/plane policy, handoff and USB paths are unchanged.
Only log; guard first four IRQs and first 192 DRM atomic ioctls against flooding.
Apply AFTER Phase446Z script, before compilation. Fail closed on anchor drift.
"""
from pathlib import Path
import argparse
TAG = "A52_PHASE446AA_SE8_DRM_REQUEST_TRACE_V1"
I2C = Path("drivers/i2c/busses/i2c-qcom-geni.c")
DRM = Path("drivers/gpu/drm/drm_atomic_uapi.c")

def replace_one(s, old, new, label):
    n=s.count(old)
    if n!=1:
        raise RuntimeError("P446AA "+label+" anchor count "+str(n))
    return s.replace(old,new,1)

def patch_i2c(s):
    if TAG in s:
        return s
    if "P446Z I2C FIRST_BEFORE" not in s:
        raise RuntimeError("P446AA requires completed P446Z I2C patch")
    anchor = '\tatomic_inc(&gi2c->a52z_irq_count);\n'
    s=replace_one(s,anchor,
         anchor+
         '\tif (gi2c->se.a52_legacy_ahb &&\n'
         '\t    atomic_read(&gi2c->a52z_irq_count) <= 4)\n'
         '\t\tdev_info(gi2c->se.dev,\n'
         '\t\t\t"P446AA SE8 IRQ count=%d raw=%#x enable=%#x status=%#x cmd=%#x\\n",\n'
         '\t\t\tatomic_read(&gi2c->a52z_irq_count),\n'
         '\t\t\treadl_relaxed(base+0x610), readl_relaxed(base+0x614),\n'
         '\t\t\treadl_relaxed(base+0x40), readl_relaxed(base+0x600));\n',
         "IRQ raw snapshot")
    anchor='\tgi2c->err = 0;\n\treinit_completion(&gi2c->done);'
    s=replace_one(s,anchor,
         '\tif (gi2c->se.a52_legacy_ahb && !atomic_read(&gi2c->a52y_first_xfer))\n'
         '\t\tdev_info(gi2c->se.dev,\n'
         '\t\t\t"P446AA SE8 BEFORE se_clk_hz=%lu mclk_cfg=%#x clk_ro=%#x clk_sel=%#x output=%#x irq_en=%#x ios=%#x\\n",\n'
         '\t\t\tclk_get_rate(gi2c->se.clk),\n'
                  '\t\t\treadl_relaxed(gi2c->se.base+0x48),\n'
         '\t\t\treadl_relaxed(gi2c->se.base+0x60),\n'
         '\t\t\treadl_relaxed(gi2c->se.base+0x7c),\n'
         '\t\t\treadl_relaxed(gi2c->se.base+0x24),\n'
         '\t\t\treadl_relaxed(gi2c->se.base+0x614),\n'
         '\t\t\treadl_relaxed(gi2c->se.base+SE_GENI_IOS));\n'
         +anchor,"first command clock state")
    return "/* "+TAG+" */\n"+s

def patch_drm(s):
    if TAG in s:
        return s
    a="int drm_mode_atomic_ioctl(struct drm_device *dev,"
    if s.count(a)!=1:
        raise RuntimeError("P446AA DRM atomic ioctl declaration drift")
    # Linux CCF/DRM headers allow this additional compile-time-only debugging.
    s=s.replace(a,
       '/* '+TAG+'; first 192 ioctls, no state modification */\n'
       'static atomic_t a52aa_req_no = ATOMIC_INIT(0);\n'
       +a,1)
    anchor="struct drm_mode_atomic *arg = data;"
    s=replace_one(s,anchor,
       anchor+"\n\tunsigned int a52aa_seq=0;",
       "ioctl local sequence")
    # Count all DRM atomic calls (not just those that change planes).
    anchor="retry:\n\tcopied_objs = 0;"
    s=replace_one(s,anchor,
       "retry:\n"
       "\tif (!a52aa_seq) {\n"
       "\t\ta52aa_seq=atomic_inc_return(&a52aa_req_no);\n"
       "\t\tif (a52aa_seq<=192)\n"
       "\t\t\tpr_info(\"P446AA DRM_IOCTL seq=%u pid=%d comm=%s flags=%#x objects=%u\\n\",\n"
       "\t\t\t\ta52aa_seq,task_pid_nr(current),current->comm,arg->flags,arg->count_objs);\n"
       "\t}\n"
       "\tcopied_objs = 0;",
       "ioctl count")
    # ACK 5.10 layouts legitimately split this call across two lines.
    # Preserve the original call verbatim and log immediately before it.
    import re
    pat=r'(?m)^(?P<indent>[ \t]*)ret\s*=\s*drm_atomic_set_property\s*\(\s*state\s*,\s*file_priv\s*,\s*obj\s*,'
    matches=list(re.finditer(pat,s))
    if len(matches)!=1:
        raise RuntimeError("P446AA expected exactly one DRM property set, found "+str(len(matches)))
    hit=matches[0]
    i=hit.start()
    indent=hit.group("indent")
    trace=(
        indent+"if (a52aa_seq && a52aa_seq<=192 &&\n"
        +indent+"\tobj->type==DRM_MODE_OBJECT_PLANE &&\n"
        +indent+"\t(!strcmp(prop->name,\"FB_ID\") ||\n"
        +indent+"\t !strcmp(prop->name,\"CRTC_ID\")))\n"
        +indent+"\tpr_info(\"P446AA DRM_INPUT seq=%u plane=%u prop=%s val=%llu\\n\",\n"
        +indent+"\t\ta52aa_seq,obj->id,prop->name,\n"
        +indent+"\t\t(unsigned long long)prop_value);\n"
    )
    s=s[:i]+trace+s[i:]
    # Headers included directly here because this remains a removable debug patch.
    a="/* "+TAG+"; first 192 ioctls, no state modification */"
    s=replace_one(s,a,
       "#include <linux/atomic.h>\n#include <linux/sched.h>\n"
       +a,"debug header")
    return s

def check(root):
    src=(root/I2C).read_text()
    drm=(root/DRM).read_text()
    for w in (TAG,"P446AA SE8 IRQ","P446AA SE8 BEFORE","P446Z I2C BUS_VOTE"):
        if w not in src:raise RuntimeError("P446AA SE8 missing "+w)
    for w in (TAG,"P446AA DRM_IOCTL","P446AA DRM_INPUT","a52aa_seq<=192"):
        if w not in drm:raise RuntimeError("P446AA DRM missing "+w)
    print("P446AA audit PASS: read-only SE8 clock/IRQ and HWC raw atomic FB_ID/CRTC_ID witnesses")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:
        files=[(I2C,patch_i2c),(DRM,patch_drm)]
        results=[(a.root/f,fn((a.root/f).read_text())) for f,fn in files]
        for f,t in results:f.write_text(t)
    check(a.root)
if __name__=="__main__":main()
