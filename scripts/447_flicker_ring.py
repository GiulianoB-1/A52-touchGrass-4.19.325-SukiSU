#!/usr/bin/env python3
"""P447: one-shot *dmesg* capture of 40 HWC atomic requests BEFORE the
first plane CRTC_ID=0 request, the trigger request, and 20 AFTER.

The preexisting P446AC debugfs ring is not the requested dump: it captures
16/16, triggers on plane91 FB_ID=0, and never emits its contents to dmesg.
Keep that old instrument untouched for manifest compatibility. This new
ring logs ALL observed objects, requested properties and rc/flags in a
single deferred printk dump, exactly once. No DRM behavioral changes.
"""
import argparse
from pathlib import Path

FILE=Path("drivers/gpu/drm/drm_atomic_uapi.c")
TAG="A52_PHASE447_FLICKER_RING_V1"
C=r'''
/* A52_PHASE447_FLICKER_RING_V1
 * HWC-only, non TEST_ONLY; last 40 + trigger + next 20 atomic requests.
 * Default ON; early param a52.flicker_ring=0 disables without rebuild.
 * The dump is queued to process context only AFTER all 20 post requests
 * completed: does not print hundreds of lines in an atomic ioctl context.
 * All objects and requested properties (not just plane91 / FB_ID) are
 * recorded, and every bounded overflow is explicitly marked (not hidden).
 * Legacy P446AC debugfs ring retained without changing its behavior.
 */
#define A52F_RING_SLOTS 64 /* 40 + 1 + 20, with three spare slots */
#define A52F_RING_PRE 40
#define A52F_RING_POST 20
#define A52F_RING_PROPS 512
#define A52F_RING_OBJECTS 128

struct a52f_prop {
	u32 obj_id, obj_type, prop_id;
	u64 value;
	char name[40];
};
struct a52f_object {
	u32 id, type, requested_props;
};
struct a52f_req {
	u32 seq, pid, flags, expected_objs;
	u32 nobj, nprop, dropped_obj, dropped_prop;
	u64 time_ns;
	int rc;
	bool done;
	char comm[TASK_COMM_LEN];
	struct a52f_object obj[A52F_RING_OBJECTS];
	struct a52f_prop prop[A52F_RING_PROPS];
};

static struct a52f_req a52f_ring[A52F_RING_SLOTS];
static DEFINE_SPINLOCK(a52f_lock);
static u32 a52f_count, a52f_trigger;
static bool a52f_frozen;
static bool a52f_enabled=true;
static void a52f_dump_fn(struct work_struct *work);
static DECLARE_WORK(a52f_dump_work,a52f_dump_fn);

static int __init a52f_ring_param(char *s)
{
	if (!s || kstrtobool(s,&a52f_enabled))
		return -EINVAL;
	return 0;
}
early_param("a52.flicker_ring",a52f_ring_param);

static u32 a52f_begin(struct drm_mode_atomic *arg)
{
	struct a52f_req *r;
	unsigned long flags;
	u32 seq=0;
	if (!READ_ONCE(a52f_enabled) ||
	    !a52ab_is_composer() ||
	    (arg->flags & DRM_MODE_ATOMIC_TEST_ONLY))
		return 0;
	spin_lock_irqsave(&a52f_lock,flags);
	if (a52f_frozen ||
	    (a52f_trigger && a52f_count>=a52f_trigger+A52F_RING_POST))
		goto out;
	seq=++a52f_count;
	r=&a52f_ring[seq%A52F_RING_SLOTS];
	memset(r,0,sizeof(*r));
	r->seq=seq;
	r->time_ns=ktime_get_boottime_ns();
	r->pid=task_pid_nr(current);
	r->flags=arg->flags;
	r->expected_objs=arg->count_objs;
	strscpy(r->comm,current->comm,sizeof(r->comm));
out:
	spin_unlock_irqrestore(&a52f_lock,flags);
	return seq;
}

/* A drm atomic -EDEADLK retry must not append objects/properties twice. */
static void a52f_retry(u32 seq)
{
	struct a52f_req *r;
	unsigned long flags;
	if (!seq)return;
	spin_lock_irqsave(&a52f_lock,flags);
	r=&a52f_ring[seq%A52F_RING_SLOTS];
	if (!a52f_frozen && r->seq==seq) {
		r->nobj=0;
		r->nprop=0;
		r->dropped_obj=0;
		r->dropped_prop=0;
		r->done=false;
	}
	spin_unlock_irqrestore(&a52f_lock,flags);
}
static void a52f_object(u32 seq,const struct drm_mode_object *obj,u32 nprops)
{
	struct a52f_req *r;
	unsigned long flags;
	if (!seq)return;
	spin_lock_irqsave(&a52f_lock,flags);
	r=&a52f_ring[seq%A52F_RING_SLOTS];
	if (!a52f_frozen && r->seq==seq) {
		if (r->nobj<A52F_RING_OBJECTS) {
			struct a52f_object *o=&r->obj[r->nobj++];
			o->id=obj->id;
			o->type=obj->type;
			o->requested_props=nprops;
		} else {
			r->dropped_obj++;
		}
	}
	spin_unlock_irqrestore(&a52f_lock,flags);
}
static void a52f_property(u32 seq,struct drm_mode_object *obj,
			  struct drm_property *prop,u64 val)
{
	struct a52f_req *r;
	unsigned long flags;
	bool trigger=false;
	if (!seq)return;
	spin_lock_irqsave(&a52f_lock,flags);
	r=&a52f_ring[seq%A52F_RING_SLOTS];
	if (!a52f_frozen && r->seq==seq) {
		if (r->nprop<A52F_RING_PROPS) {
			struct a52f_prop *p=&r->prop[r->nprop++];
			p->obj_id=obj->id;
			p->obj_type=obj->type;
			p->prop_id=prop->base.id;
			p->value=val;
			strscpy(p->name,prop->name,sizeof(p->name));
		} else {
			r->dropped_prop++;
		}
		/* FIRST compositor plane CRTC_ID=0 REQUEST, regardless of
		 * plane number. Never substitute post-commit fb state or FB_ID.
		 */
		if (!a52f_trigger && obj->type==DRM_MODE_OBJECT_PLANE &&
		    !strcmp(prop->name,"CRTC_ID") && val==0) {
			a52f_trigger=seq;
			trigger=true;
		}
	}
	spin_unlock_irqrestore(&a52f_lock,flags);
	if (trigger)
		pr_info("P447 FLICKER_RING TRIGGER seq=%u plane=%u CRTC_ID=0 pre=%d post=%d time_ns=%llu\n",
			seq,obj->id,A52F_RING_PRE,A52F_RING_POST,
			(unsigned long long)ktime_get_boottime_ns());
}
static void a52f_end(u32 seq,int rc)
{
	unsigned long flags;
	bool ready=false,complete=true;
	u32 i;
	struct a52f_req *r;
	if (!seq)return;
	spin_lock_irqsave(&a52f_lock,flags);
	r=&a52f_ring[seq%A52F_RING_SLOTS];
	if (!a52f_frozen && r->seq==seq) {
		r->rc=rc;
		if (rc!=-EDEADLK)
			r->done=true;
	}
	if (!a52f_frozen && a52f_trigger &&
	    a52f_count>=a52f_trigger+A52F_RING_POST) {
		for (i=a52f_trigger;i<=a52f_trigger+A52F_RING_POST;i++) {
			const struct a52f_req *p=
				&a52f_ring[i%A52F_RING_SLOTS];
			if (p->seq!=i || !p->done) {
				complete=false;
				break;
			}
		}
		if (complete) {
			a52f_frozen=true;
			ready=true;
		}
	}
	spin_unlock_irqrestore(&a52f_lock,flags);
	if (ready)
		schedule_work(&a52f_dump_work);
}

/* Frozen ring is immutable; never hold the spinlock while printk() runs. */
static void a52f_dump_fn(struct work_struct *work)
{
	u32 seq,i,start,trigger=READ_ONCE(a52f_trigger);
	u32 end=trigger+A52F_RING_POST,printed=0,dropped=0;
	start=trigger>A52F_RING_PRE ? trigger-A52F_RING_PRE : 1;
	pr_info("P447 FLICKER_RING DUMP_BEGIN trigger=%u range=%u..%u wanted_pre=40 wanted_post=20 slots=%u prop_cap=%u once=1\n",
		trigger,start,end,A52F_RING_SLOTS,A52F_RING_PROPS);
	for (seq=start;seq<=end;seq++) {
		const struct a52f_req *r=&a52f_ring[seq%A52F_RING_SLOTS];
		if (r->seq!=seq) {
			pr_info("P447 FLICKER_RING MISSING seq=%u\n",seq);
			continue;
		}
		printed++;
		dropped+=r->dropped_prop+r->dropped_obj;
		pr_info("P447 FLICKER_RING REQ seq=%u ns=%llu pid=%u comm=%s flags=%#x rc=%d done=%d objects=%u/%u dropped_obj=%u props=%u dropped_prop=%u phase=%s\n",
			r->seq,(unsigned long long)r->time_ns,r->pid,r->comm,
			r->flags,r->rc,r->done,r->nobj,r->expected_objs,
			r->dropped_obj,r->nprop,r->dropped_prop,
			seq==trigger?"TRIGGER":seq<trigger?"PRE":"POST");
		for (i=0;i<r->nobj;i++) {
			const struct a52f_object *o=&r->obj[i];
			pr_info("P447 FLICKER_RING OBJ seq=%u index=%u id=%u type=%#x requested_props=%u\n",
				seq,i,o->id,o->type,o->requested_props);
		}
		for (i=0;i<r->nprop;i++) {
			const struct a52f_prop *p=&r->prop[i];
			pr_info("P447 FLICKER_RING PROP seq=%u index=%u obj=%u type=%#x name=%s prop_id=%u value=%llu hex=%#llx\n",
				seq,i,p->obj_id,p->obj_type,p->name,p->prop_id,
				(unsigned long long)p->value,
				(unsigned long long)p->value);
		}
	}
	pr_info("P447 FLICKER_RING DUMP_END trigger=%u requests=%u dropped=%u once=1 status=%s\n",
		trigger,printed,dropped,dropped?"TRUNCATED":"COMPLETE");
}
static int __init a52f_init(void)
{
	pr_info("P447 FLICKER_RING ARMED enabled=%d default_on=1 trigger=first_plane_CRTC_ID_0 pre=40 post=20 one_shot=1 dump=dmesg\n",
		a52f_enabled);
	return 0;
}
late_initcall(a52f_init);
'''

def once(s,old,new,label):
    hits=s.count(old)
    if hits!=1:
        raise RuntimeError(f"P447 ring {label}: expected 1 anchor got {hits}")
    return s.replace(old,new,1)

def patch(s):
    if TAG in s:return s
    for item in ("A52_PHASE446AC_HWC_ATOMIC_RING_V1",
                 "P446AC HWC_RING READY","P446AA DRM_INPUT",
                 "P446AB HWC_COMMIT",
                 "a52ac_ring_start(a52aa_seq,arg);",
                 "a52ac_ring_prop(a52aa_seq,obj,prop,prop_value);",
                 "a52ac_ring_end(a52aa_seq);",
                 "unsigned int a52aa_seq=0;"):
        if item not in s:raise RuntimeError("P447 ring missing prior hook "+item)
    s="#include <linux/workqueue.h>\n#include <linux/init.h>\n"+s
    marker="/* A52_PHASE446AA_SE8_DRM_REQUEST_TRACE_V1; first 192 ioctls, no state modification */"
    s=once(s,marker,C+"\n"+marker,"C ring implementation")
    s=once(s,"unsigned int a52aa_seq=0;",
           "unsigned int a52aa_seq=0;\n\tu32 a52f_seq=0;",
           "ioctl local HWC sequence")
    s=once(s,"a52ac_ring_start(a52aa_seq,arg);",
           "a52ac_ring_start(a52aa_seq,arg);\n"
           "\t\t\ta52f_seq=a52f_begin(arg);",
           "single ring start per ioctl, not EDEADLK retry")
    s=once(s,"\tcopied_objs = 0;",
           "\ta52f_retry(a52f_seq);\n\tcopied_objs = 0;",
           "reset on retry to avoid duplicate data")
    # Record zero-property objects as well; hook once after user count_props
    s=once(s,"\t\tcopied_objs++;",
           "\t\ta52f_object(a52f_seq,obj,count_props);\n"
           "\t\tcopied_objs++;",
           "all DRM objects, including those without properties")
    s=once(s,"a52ac_ring_prop(a52aa_seq,obj,prop,prop_value);",
           "a52ac_ring_prop(a52aa_seq,obj,prop,prop_value);\n"
           "\t\t\ta52f_property(a52f_seq,obj,prop,prop_value);",
           "every object/property name, id and value")
    s=once(s,"\ta52ac_ring_end(a52aa_seq);",
           "\ta52ac_ring_end(a52aa_seq);\n"
           "\ta52f_end(a52f_seq,ret);",
           "one-shot dump when trigger+20 completed")
    return s

def check(root):
    src=(root/FILE).read_text()
    markers=(TAG,"P447 FLICKER_RING ARMED","P447 FLICKER_RING TRIGGER",
             "P447 FLICKER_RING DUMP_BEGIN","P447 FLICKER_RING REQ",
             "P447 FLICKER_RING OBJ","P447 FLICKER_RING PROP",
             "P447 FLICKER_RING DUMP_END","a52f_ring_param",
             "early_param(\"a52.flicker_ring\"","a52f_seq=a52f_begin(arg)",
             "a52f_object(a52f_seq,obj,count_props)",
             "a52f_property(a52f_seq,obj,prop,prop_value)",
             "a52f_end(a52f_seq,ret)", "A52F_RING_PRE 40",
             "A52F_RING_POST 20", "A52F_RING_SLOTS 64",
             "A52F_RING_PROPS 512","!strcmp(prop->name,\"CRTC_ID\") && val==0",
             "DECLARE_WORK(a52f_dump_work","schedule_work(&a52f_dump_work)",
             "P446AC HWC_RING READY")
    for m in markers:
        if m not in src:raise RuntimeError("P447 ring source missing "+m)
    if src.count("P447 FLICKER_RING DUMP_BEGIN")!=1:
        raise RuntimeError("P447 ring can dump more than once")
    print("P447 FLICKER_RING AUDIT PASS: default ON, 40 pre + trigger + 20 post, first plane CRTC_ID=0, all object/property values, one deferred dmesg dump; old 446AC retained")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    a=ap.parse_args()
    f=a.root/FILE
    if not a.check_only:
        f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":
    main()
