#!/usr/bin/env python3
"""Phase446AB: probe SE8 FIFO-only/GSI capability, trace composer property epochs.

Read-only driver witnesses. First 20 non-TEST_ONLY HWC commits, then first
20 after an OFF -> ON of CRTC107/connector32. Never modify a DRM property,
panel, bus vote, touch input or boot flow. Fails closed on source drift.
"""
import argparse
from pathlib import Path
I2C=Path("drivers/i2c/busses/i2c-qcom-geni.c")
DRM=Path("drivers/gpu/drm/drm_atomic_uapi.c")
OBJ=Path("drivers/gpu/drm/drm_mode_object.c")
TAG="A52_PHASE446AB_FIFO_AND_COMPOSER_PROPERTY_EPOCHS_V1"
def once(s,a,b,label):
    n=s.count(a)
    if n!=1: raise RuntimeError(f"P446AB {label} anchor count={n}")
    return s.replace(a,b,1)

def i2c(s):
    if TAG in s:return s
    if "P446AA SE8 BEFORE" not in s or "P446Z I2C BUS_VOTE" not in s:
        raise RuntimeError("P446AB needs Phase446AA and Z source")
    helper=r'''
/* A52_PHASE446AB_FIFO_AND_COMPOSER_PROPERTY_EPOCHS_V1
 * SE8-only read-only capability snapshots. Nonzero FIFO_DISABLE_RO
 * indicates no FIFO interface, requiring TG's GPI/GSI path.
 */
static void a52ab_se8_capability(struct geni_i2c_dev *gi2c, const char *where)
{
	static atomic_t first_abort = ATOMIC_INIT(0);
	void __iomem *base=gi2c->se.base;
	u32 param, fifo, dma, depth;
	if (!gi2c->se.a52_legacy_ahb)
		return;
	if (where[0]=='T' && atomic_xchg(&first_abort,1))
		return;
	fifo=readl_relaxed(base+0x64); /* GENI_IF_FIFO_DISABLE_RO */
	dma=readl_relaxed(base+SE_GENI_DMA_MODE_EN); /* 0x258 */
	param=readl_relaxed(base+SE_HW_PARAM_0); /* 0xe24 */
	depth=(param & TX_FIFO_DEPTH_MSK)>>TX_FIFO_DEPTH_SHFT;
	dev_info(gi2c->se.dev,
		"P446AB SE8_CAP stage=%s fifo_disable_ro=%#x dma_mode_en=%#x tx_fifo_depth=%u hw_param0=%#x gsi_only=%d\n",
		where,fifo,dma,depth,param,!!fifo);
}
'''
    s=once(s,"static void geni_i2c_abort_xfer(struct geni_i2c_dev *gi2c)",
        helper+"\nstatic void geni_i2c_abort_xfer(struct geni_i2c_dev *gi2c)",
        "SE8 capability helper")
    s=once(s,"\tspin_lock_irqsave(&gi2c->lock, flags);\n\tgeni_i2c_err(gi2c, GENI_TIMEOUT);",
        '\ta52ab_se8_capability(gi2c,"TIMEOUT_BEFORE_ABORT");\n'
        "\tspin_lock_irqsave(&gi2c->lock, flags);\n\tgeni_i2c_err(gi2c, GENI_TIMEOUT);",
        "snapshot while clocks still enabled")
    s=once(s,"\tproto = geni_se_read_proto(&gi2c->se);",
        '\tproto = geni_se_read_proto(&gi2c->se);\n'
        '\ta52ab_se8_capability(gi2c,"PROBE_FW_READ");',
        "capability after FW read")
    return s

DRM_HELPERS=r'''
/* A52_PHASE446AB_FIFO_AND_COMPOSER_PROPERTY_EPOCHS_V1
 * Read-only, keyed to the actual HWC thread. First 20 non-TEST_ONLY
 * atomic ioctls; rearm only after composer sets ACTIVE=0 then ACTIVE=1
 * on CRTC107 or DPMS=OFF then ON on connector32. No commit suppression.
 */
static atomic_t a52ab_hwc_commits = ATOMIC_INIT(0);
static atomic_t a52ab_power_phase = ATOMIC_INIT(0); /* 0=warmup,1=armed,2=off,3=on */
static atomic_t a52ab_resume_at = ATOMIC_INIT(0);
static bool a52ab_is_composer(void)
{
	return strnstr(current->comm,"HwBinder",TASK_COMM_LEN) ||
	       strnstr(current->comm,"composer",TASK_COMM_LEN) ||
	       strnstr(current->comm,"Hwc",TASK_COMM_LEN) ||
	       strnstr(current->comm,"HWC",TASK_COMM_LEN);
}
static bool a52ab_window(void)
{
	unsigned int n=atomic_read(&a52ab_hwc_commits);
	unsigned int base=atomic_read(&a52ab_resume_at);
	return n<=20 || (atomic_read(&a52ab_power_phase)==3 &&
			    n>=base && n<base+20);
}
static const char *a52ab_epoch(void)
{
	return atomic_read(&a52ab_power_phase)==3 ? "POST_POWER_CYCLE" : "BOOT";
}
static bool a52ab_target(struct drm_mode_object *obj)
{
	return obj && ((obj->id==32 && obj->type==DRM_MODE_OBJECT_CONNECTOR) ||
		       (obj->id==107 && obj->type==DRM_MODE_OBJECT_CRTC) ||
		       (obj->id==91 && obj->type==DRM_MODE_OBJECT_PLANE));
}
static void a52ab_watch_power(struct drm_mode_object *obj,
			       struct drm_property *prop, u64 val)
{
	int phase=atomic_read(&a52ab_power_phase);
	bool on=false,off=false;
	if (obj->id==107 && obj->type==DRM_MODE_OBJECT_CRTC &&
	    !strcmp(prop->name,"ACTIVE")) {
		off=!val; on=!!val;
	} else if (obj->id==32 && obj->type==DRM_MODE_OBJECT_CONNECTOR &&
		   !strcmp(prop->name,"DPMS")) {
		off=(val==DRM_MODE_DPMS_OFF);
		on=(val==DRM_MODE_DPMS_ON);
	}
	if (phase==1 && off &&
	    atomic_cmpxchg(&a52ab_power_phase,1,2)==1)
		pr_info("P446AB POWER_CYCLE_OFF obj=%u prop=%s value=%llu hwc_commit=%d\n",
			obj->id,prop->name,(unsigned long long)val,
			atomic_read(&a52ab_hwc_commits));
	if (phase==2 && on &&
	    atomic_cmpxchg(&a52ab_power_phase,2,3)==2) {
		int base=atomic_read(&a52ab_hwc_commits);
		atomic_set(&a52ab_resume_at,base);
		pr_info("P446AB POST_POWER_CYCLE_BEGIN obj=%u prop=%s value=%llu base=%d next20=1\n",
			obj->id,prop->name,(unsigned long long)val,base);
	}
}
void a52ab_trace_set(struct drm_mode_object *obj,
		    struct drm_property *prop, u64 val)
{
	if (!a52ab_is_composer() || !a52ab_target(obj))
		return;
	a52ab_watch_power(obj,prop,val); /* watch even after first 20 */
	if (a52ab_window())
		pr_info("P446AB PROP_SET epoch=%s commit=%d obj=%u type=%u prop=%s id=%u value=%llu pid=%d comm=%s\n",
			a52ab_epoch(),atomic_read(&a52ab_hwc_commits),
			obj->id,obj->type,prop->name,prop->base.id,
			(unsigned long long)val,task_pid_nr(current),current->comm);
}
void a52ab_trace_get(struct drm_mode_object *obj,
		    struct drm_property *prop, u64 val)
{
	if (a52ab_is_composer() && a52ab_target(obj) && a52ab_window())
		pr_info("P446AB PROP_GET epoch=%s commit=%d obj=%u type=%u prop=%s id=%u value=%llu pid=%d comm=%s\n",
			a52ab_epoch(),atomic_read(&a52ab_hwc_commits),
			obj->id,obj->type,prop->name,prop->base.id,
			(unsigned long long)val,task_pid_nr(current),current->comm);
}
static void a52ab_count_hwc_ioctl(struct drm_mode_atomic *arg)
{
	int n;
	if (!a52ab_is_composer() || (arg->flags & DRM_MODE_ATOMIC_TEST_ONLY))
		return;
	n=atomic_inc_return(&a52ab_hwc_commits);
	if (n==21)
		atomic_cmpxchg(&a52ab_power_phase,0,1);
	if (a52ab_window())
		pr_info("P446AB HWC_COMMIT epoch=%s seq=%d flags=%#x objects=%u pid=%d comm=%s\n",
			a52ab_epoch(),n,arg->flags,arg->count_objs,
			task_pid_nr(current),current->comm);
}
'''
def drm(s):
    if TAG in s:return s
    if "P446AA DRM_INPUT" not in s:
        raise RuntimeError("P446AB needs Phase446AA source")
    s=once(s,"/* A52_PHASE446AA_SE8_DRM_REQUEST_TRACE_V1; first 192 ioctls, no state modification */",
           DRM_HELPERS+"\n/* A52_PHASE446AA_SE8_DRM_REQUEST_TRACE_V1; first 192 ioctls, no state modification */",
           "shared DRM epoch helpers")
    s=once(s,"\t\t\ta52aa_seq=atomic_inc_return(&a52aa_req_no);",
           "\t\t\ta52aa_seq=atomic_inc_return(&a52aa_req_no);\n"
           "\t\t\ta52ab_count_hwc_ioctl(arg);",
           "count atomic ioctl once not on retry")
    s=once(s,"\tif (!drm_property_change_valid_get(prop, prop_value, &ref))",
           "\ta52ab_trace_set(obj,prop,prop_value);\n"
           "\tif (!drm_property_change_valid_get(prop, prop_value, &ref))",
           "shared core atomic setter")
    return s

def obj(s):
    if TAG in s:return s
    s=once(s,"/* helper for getconnector and getproperties ioctls */",
           "/* "+TAG+" */\n"
           "extern void a52ab_trace_get(struct drm_mode_object *obj,\n"
           "                           struct drm_property *prop, u64 val);\n"
           "extern void a52ab_trace_set(struct drm_mode_object *obj,\n"
           "                           struct drm_property *prop, u64 val);\n"
           "/* helper for getconnector and getproperties ioctls */",
           "DRM GET declaration")
    s=once(s,"\t\t\tif (ret)\n\t\t\t\treturn ret;\n\n\t\t\tif (put_user(prop->base.id, prop_ptr + count))",
           "\t\t\tif (ret)\n\t\t\t\treturn ret;\n"
           "\t\t\ta52ab_trace_get(obj,prop,val);\n\n"
           "\t\t\tif (put_user(prop->base.id, prop_ptr + count))",
           "all object property GET values")
    s=once(s,"\tproperty = drm_mode_obj_find_prop_id(arg_obj, arg->prop_id);\n\tif (!property)\n\t\tgoto out_unref;",
           "\tproperty = drm_mode_obj_find_prop_id(arg_obj, arg->prop_id);\n"
           "\tif (!property)\n\t\tgoto out_unref;\n"
           "\t/* Legacy object SETPROPERTY path (including connector DPMS). */\n"
           "\ta52ab_trace_set(arg_obj,property,arg->value);",
           "legacy object property SET")
    return s

def check(root):
    for p,markers in ((I2C,("P446AB SE8_CAP","fifo_disable_ro=","dma_mode_en=","tx_fifo_depth=",
                             "TIMEOUT_BEFORE_ABORT","P446AA SE8 BEFORE")),
                      (DRM,(TAG,"P446AB PROP_SET","P446AB PROP_GET",
                             "P446AB POST_POWER_CYCLE_BEGIN","P446AB HWC_COMMIT")),
                      (OBJ,(TAG,"a52ab_trace_get(obj,prop,val)","a52ab_trace_set(arg_obj,property,arg->value)"))):
        s=(root/p).read_text()
        for m in markers:
            if m not in s:raise RuntimeError("P446AB contract "+str(p)+": "+m)
    print("P446AB PASS: SE8 FIFO/GSI capability probe+timeout; composer full target GET/SET first20 and after first CRTC107/conn32 power-cycle")

def main():
    q=argparse.ArgumentParser()
    q.add_argument("--root",required=True,type=Path)
    q.add_argument("--check-only",action="store_true")
    a=q.parse_args()
    if not a.check_only:
        results=[]
        for p,fn in ((I2C,i2c),(DRM,drm),(OBJ,obj)):
            results.append((a.root/p,fn((a.root/p).read_text())))
        for p,t in results:p.write_text(t)
    check(a.root)
if __name__=="__main__":main()
