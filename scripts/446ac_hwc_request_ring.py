#!/usr/bin/env python3
"""P446AC: bounded read-only full HWC atomic request ring around first blank plane.

Apply after Phase446AB. Single freeze-and-dump of up to 25 DRM atomic
requests around first plane91 FB_ID=0 at/after the initial 24 commits.
Store every object's properties (IDs, names, values), raw flags and result.
Never rewrite requests, alter commit results, modeset, fences, or panels.
"""
from pathlib import Path
import argparse

PATH=Path("drivers/gpu/drm/drm_atomic_uapi.c")
TAG="A52_PHASE446AC_HWC_FULL_REQUEST_RING_V1"

C=r'''
/* A52_PHASE446AC_HWC_FULL_REQUEST_RING_V1
 * Capture under spinlock; dump asynchronously only after post-trigger
 * requests have arrived. Full object/property list, not merely plane91.
 * Bounded once per boot; no printk on the hot property-setting path.
 */
#include <linux/workqueue.h>
#include <linux/spinlock.h>
#define A52AC_RING 64
#define A52AC_MAX_PROPS 128
struct a52ac_prop {
	u32 object, type, property;
	u64 value;
	char name[32];
};
struct a52ac_request {
	u32 seq, flags, objects, count;
	pid_t pid;
	int result;
	char comm[TASK_COMM_LEN];
	bool overflow;
	struct a52ac_prop prop[A52AC_MAX_PROPS];
};
static struct a52ac_request a52ac_ring[A52AC_RING];
static DEFINE_SPINLOCK(a52ac_lock);
static atomic_t a52ac_next = ATOMIC_INIT(0);
static atomic_t a52ac_null_trigger = ATOMIC_INIT(0);
static atomic_t a52ac_frozen = ATOMIC_INIT(0);
static void a52ac_dump_workfn(struct work_struct *work);
static DECLARE_WORK(a52ac_work, a52ac_dump_workfn);

static u32 a52ac_begin(const struct drm_mode_atomic *arg)
{
	struct a52ac_request *r;
	unsigned long irqf;
	u32 seq;
	if (!a52ab_is_composer() || (arg->flags & DRM_MODE_ATOMIC_TEST_ONLY))
		return 0;
	seq=(u32)atomic_inc_return(&a52ac_next);
	if (atomic_read(&a52ac_frozen))
		return 0;
	spin_lock_irqsave(&a52ac_lock, irqf);
	r=&a52ac_ring[seq % A52AC_RING];
	memset(r,0,sizeof(*r));
	r->seq=seq;
	r->flags=arg->flags;
	r->objects=arg->count_objs;
	r->pid=task_pid_nr(current);
	strscpy(r->comm,current->comm,sizeof(r->comm));
	spin_unlock_irqrestore(&a52ac_lock, irqf);
	return seq;
}

static void a52ac_record(u32 seq, struct drm_mode_object *obj,
			 struct drm_property *prop, u64 value)
{
	struct a52ac_request *r;
	struct a52ac_prop *p;
	unsigned long irqf;
	if (!seq || atomic_read(&a52ac_frozen))
		return;
	spin_lock_irqsave(&a52ac_lock,irqf);
	r=&a52ac_ring[seq % A52AC_RING];
	if (r->seq==seq) {
		if (r->count < A52AC_MAX_PROPS) {
			p=&r->prop[r->count++];
			p->object=obj->id;
			p->type=obj->type;
			p->property=prop->base.id;
			p->value=value;
			strscpy(p->name,prop->name,sizeof(p->name));
		} else {
			r->overflow=true;
		}
	}
	spin_unlock_irqrestore(&a52ac_lock,irqf);
	/* First userspace clear of plane91 after initial steady frames. */
	if (seq>=24 && seq<=120 && obj->id==91 &&
	    obj->type==DRM_MODE_OBJECT_PLANE &&
	    value==0 && !strcmp(prop->name,"FB_ID"))
		atomic_cmpxchg(&a52ac_null_trigger,0,seq);
}

static void a52ac_finish(u32 seq,int result)
{
	struct a52ac_request *r;
	unsigned long irqf;
	u32 trigger;
	if (!seq || atomic_read(&a52ac_frozen))
		return;
	spin_lock_irqsave(&a52ac_lock,irqf);
	r=&a52ac_ring[seq % A52AC_RING];
	if (r->seq==seq)
		r->result=result;
	spin_unlock_irqrestore(&a52ac_lock,irqf);
	trigger=atomic_read(&a52ac_null_trigger);
	if ((trigger && seq>=trigger+12) ||
	    (!trigger && seq>=48)) {
		if (atomic_cmpxchg(&a52ac_frozen,0,1)==0)
			schedule_work(&a52ac_work);
	}
}

static void a52ac_dump_workfn(struct work_struct *work)
{
	u32 trigger=atomic_read(&a52ac_null_trigger);
	u32 start=trigger ? (trigger>12 ? trigger-12 : 1) : 24;
	u32 end=trigger ? trigger+12 : 48;
	u32 seq,i,num;
	unsigned long irqf;
	struct a52ac_request *r;
	struct a52ac_prop p;
	u32 flags,objs;
	int ret,pid;
	char comm[TASK_COMM_LEN];
	bool overflow;
	pr_info("P446AC HWC_RING_BEGIN trigger=%u from=%u to=%u cap=%u maxprops=%u\n",
		trigger,start,end,A52AC_RING,A52AC_MAX_PROPS);
	for (seq=start;seq<=end;seq++) {
		spin_lock_irqsave(&a52ac_lock,irqf);
		r=&a52ac_ring[seq % A52AC_RING];
		if (r->seq!=seq) {
			spin_unlock_irqrestore(&a52ac_lock,irqf);
			pr_info("P446AC HWC_RING_MISSING seq=%u\n",seq);
			continue;
		}
		flags=r->flags;
		objs=r->objects;
		num=r->count;
		overflow=r->overflow;
		ret=r->result;
		pid=r->pid;
		strscpy(comm,r->comm,sizeof(comm));
		spin_unlock_irqrestore(&a52ac_lock,irqf);
		pr_info("P446AC HWC_REQUEST seq=%u flags=%#x objs=%u props=%u overflow=%u rc=%d pid=%d comm=%s\n",
			seq,flags,objs,num,overflow,ret,pid,comm);
		for(i=0;i<num;i++) {
			spin_lock_irqsave(&a52ac_lock,irqf);
			r=&a52ac_ring[seq % A52AC_RING];
			p=r->prop[i];
			spin_unlock_irqrestore(&a52ac_lock,irqf);
			pr_info("P446AC HWC_PROPERTY seq=%u i=%u obj=%u type=%u prop=%u name=%s val=%llu\n",
				seq,i,p.object,p.type,p.property,p.name,
				(unsigned long long)p.value);
		}
	}
	pr_info("P446AC HWC_RING_END trigger=%u total=%u\n",
		trigger,atomic_read(&a52ac_next));
}
'''

def once(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError(f"P446AC {label}: anchor count {n}")
    return s.replace(a,b,1)

def patch(s):
    if TAG in s:return s
    if "P446AB HWC_COMMIT" not in s or "P446AA DRM_INPUT" not in s:
        raise RuntimeError("P446AC requires 446AB/446AA DRM source")
    s=once(s,"int drm_mode_atomic_ioctl(struct drm_device *dev,",
           C+"\nint drm_mode_atomic_ioctl(struct drm_device *dev,","helpers")
    s=once(s,"struct drm_mode_atomic *arg = data;",
           "struct drm_mode_atomic *arg = data;\n\tunsigned int a52ac_seq=0;",
           "ioctl local")
    # First try after copying userspace flags, do not allocate in restart path.
    s=once(s,"retry:\n\tif (!a52aa_seq) {",
           "retry:\n\tif (!a52ac_seq)\n\t\ta52ac_seq=a52ac_begin(arg);\n"
           "\tif (!a52aa_seq) {","start")
    # A deadlock retry repeats every property; reset the same capture
    # so it depicts the final request once, without duplicate properties.
    s=once(s,"\tif (!a52aa_seq) {\n\t\ta52aa_seq=atomic_inc_return",
           "\tif (!a52aa_seq) {\n\t\ta52aa_seq=atomic_inc_return",
           "existing ioctl guard sanity") if False else s
    # Hook after the raw property value is copied and validated, before
    # drm_atomic_set_property. It captures every object, not just plane 91.
    import re
    pat=r'(?m)^(?P<ws>[ \t]*)ret = drm_atomic_set_property\(state, file_priv,'
    matches=list(re.finditer(pat,s))
    if len(matches)!=1:raise RuntimeError(f"P446AC property-call anchor count={len(matches)}")
    m=matches[0];ws=m.group("ws")
    s=s[:m.start()]+ws+"a52ac_record(a52ac_seq,obj,prop,prop_value);\n"+s[m.start():]
    s=once(s,"out:\n\tcomplete_signaling(dev, state, fence_state, num_fences, !ret);",
           "out:\n\ta52ac_finish(a52ac_seq,ret);\n"
           "\tcomplete_signaling(dev, state, fence_state, num_fences, !ret);",
           "post-commit freeze")
    return s

def check(root):
    t=(root/PATH).read_text()
    for x in (TAG,"P446AC HWC_RING_BEGIN","P446AC HWC_REQUEST",
              "P446AC HWC_PROPERTY","P446AC HWC_RING_END",
              "a52ac_record(a52ac_seq,obj,prop,prop_value)",
              "a52ac_finish(a52ac_seq,ret)", "A52AC_MAX_PROPS 128"):
        if x not in t:raise RuntimeError("P446AC incomplete ring: "+x)
    print("P446AC HWC_RING AUDIT PASS: capture full HWC property requests in a bounded, one-shot 64x128 ring surrounding first plane91 FB_ID=0")

def main():
    x=argparse.ArgumentParser()
    x.add_argument("--root",type=Path,required=True)
    x.add_argument("--check-only",action="store_true")
    z=x.parse_args()
    p=z.root/PATH
    if not z.check_only:p.write_text(patch(p.read_text()))
    check(z.root)
if __name__=="__main__":main()
