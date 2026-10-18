#!/usr/bin/env python3
"""446AC HWC request ring: full DRM atomic properties around first NullCommit.

Bounded static ring. Records 16 pre + offending + 16 post commits. Read only:
cat /sys/kernel/debug/a52_446ac_hwc_requests
No modification to DRM state, fences, modesets or SurfaceFlinger.
"""
from pathlib import Path
import argparse
PATH=Path("drivers/gpu/drm/drm_atomic_uapi.c")
TAG="A52_PHASE446AC_HWC_ATOMIC_RING_V1"
def one(s,a,b,label):
    n=s.count(a)
    if n!=1:raise RuntimeError("P446AC RING "+label+" anchor="+str(n))
    return s.replace(a,b,1)
C=r'''
/* A52_PHASE446AC_HWC_ATOMIC_RING_V1
 * One-shot, non-destructive HWC atomic request ring.
 * Save full object + property name/ID/value for 16 prior + first blank + 16
 * subsequent atomic requests. Read frozen ring from debugfs as root.
 */
#define A52AC_RING_SLOTS 64
#define A52AC_MAX_PROPS 96
struct a52ac_prop {
	u32 object_id,object_type,prop_id;
	u64 value;
	char name[40];
};
struct a52ac_request {
	u32 seq,flags,object_count,pid;
	u32 num_props,dropped_props;
	u64 boottime_ns;
	char comm[TASK_COMM_LEN];
	struct a52ac_prop props[A52AC_MAX_PROPS];
};
static struct a52ac_request a52ac_ring[A52AC_RING_SLOTS];
static DEFINE_SPINLOCK(a52ac_ring_lock);
static u32 a52ac_trigger_seq;
static bool a52ac_seen_fb;
static bool a52ac_frozen;
static bool a52ac_hwc(void)
{
	return strnstr(current->comm,"composer",TASK_COMM_LEN) ||
	       strnstr(current->comm,"HwBinder",TASK_COMM_LEN) ||
	       strnstr(current->comm,"HWC",TASK_COMM_LEN);
}
static void a52ac_ring_start(u32 seq,const struct drm_mode_atomic *arg)
{
	struct a52ac_request *r;
	unsigned long irqflags;
	if (!a52ac_hwc() || (arg->flags & DRM_MODE_ATOMIC_TEST_ONLY))
		return;
	spin_lock_irqsave(&a52ac_ring_lock,irqflags);
	if (a52ac_frozen) goto out;
	r=&a52ac_ring[seq%A52AC_RING_SLOTS];
	memset(r,0,sizeof(*r));
	r->seq=seq;
	r->boottime_ns=ktime_get_boottime_ns();
	r->flags=arg->flags;
	r->object_count=arg->count_objs;
	r->pid=task_pid_nr(current);
	strscpy(r->comm,current->comm,sizeof(r->comm));
out:
	spin_unlock_irqrestore(&a52ac_ring_lock,irqflags);
}
static void a52ac_ring_prop(u32 seq,struct drm_mode_object *obj,
			    struct drm_property *prop,u64 value)
{
	struct a52ac_request *r;
	struct a52ac_prop *p;
	unsigned long irqflags;
	bool triggered=false;
	if (!a52ac_hwc())return;
	spin_lock_irqsave(&a52ac_ring_lock,irqflags);
	if (a52ac_frozen)goto out;
	r=&a52ac_ring[seq%A52AC_RING_SLOTS];
	if (r->seq!=seq)goto out;
	if (r->num_props<A52AC_MAX_PROPS) {
		p=&r->props[r->num_props++];
		p->object_id=obj->id;
		p->object_type=obj->type;
		p->prop_id=prop->base.id;
		p->value=value;
		strscpy(p->name,prop->name,sizeof(p->name));
	} else {
		r->dropped_props++;
	}
	if (obj->type==DRM_MODE_OBJECT_PLANE && obj->id==91 &&
	    !strcmp(prop->name,"FB_ID")) {
		if (value)a52ac_seen_fb=true;
		else if (a52ac_seen_fb && !a52ac_trigger_seq) {
			a52ac_trigger_seq=seq;
			triggered=true;
		}
	}
out:
	spin_unlock_irqrestore(&a52ac_ring_lock,irqflags);
	if (triggered)
		pr_info("P446AC HWC_RING TRIGGER seq=%u time_ns=%llu plane91_FB_ID=0 pre=16 post=16\n",
			seq,(unsigned long long)ktime_get_boottime_ns());
}
static void a52ac_ring_end(u32 seq)
{
	unsigned long irqflags;
	bool frozen=false;
	spin_lock_irqsave(&a52ac_ring_lock,irqflags);
	if (!a52ac_frozen && a52ac_trigger_seq &&
	    seq>=a52ac_trigger_seq+16) {
		a52ac_frozen=true;
		frozen=true;
	}
	spin_unlock_irqrestore(&a52ac_ring_lock,irqflags);
	if (frozen)
		pr_info("P446AC HWC_RING READY trigger=%u through=%u debugfs=/sys/kernel/debug/a52_446ac_hwc_requests\n",
			a52ac_trigger_seq,seq);
}
static int a52ac_ring_show(struct seq_file *m,void *v)
{
	u32 seq,i,start,end,trigger;
	if (!READ_ONCE(a52ac_frozen)) {
		seq_puts(m,"P446AC HWC ring not frozen: waiting for first valid->FB_ID=0 transition and 16 later commits\n");
		return 0;
	}
	trigger=READ_ONCE(a52ac_trigger_seq);
	start=trigger>16 ? trigger-16 : 1;
	end=trigger+16;
	seq_printf(m,"P446AC HWC_RING trigger=%u range=%u..%u slots=%u maxprops=%u\n",
		   trigger,start,end,A52AC_RING_SLOTS,A52AC_MAX_PROPS);
	for (seq=start;seq<=end;seq++) {
		const struct a52ac_request *r=&a52ac_ring[seq%A52AC_RING_SLOTS];
		if (r->seq!=seq) {
			seq_printf(m,"MISSING seq=%u\n",seq);
			continue;
		}
		seq_printf(m,"REQUEST seq=%u ts_ns=%llu pid=%u comm=%s flags=%#x objects=%u props=%u dropped=%u\n",
		   seq,(unsigned long long)r->boottime_ns,r->pid,r->comm,
		   r->flags,r->object_count,r->num_props,r->dropped_props);
		for (i=0;i<r->num_props;i++) {
			const struct a52ac_prop *p=&r->props[i];
			seq_printf(m,"  PROP obj=%u type=%#x prop=%s id=%u val=%llu hex=%#llx\n",
			    p->object_id,p->object_type,p->name,p->prop_id,
			    (unsigned long long)p->value,(unsigned long long)p->value);
		}
	}
	return 0;
}
static int a52ac_ring_open(struct inode *inode,struct file *file)
{
	return single_open(file,a52ac_ring_show,NULL);
}
static const struct file_operations a52ac_ring_fops = {
	.owner=THIS_MODULE,
	.open=a52ac_ring_open,
	.read=seq_read,
	.llseek=seq_lseek,
	.release=single_release,
};
static int __init a52ac_ring_init(void)
{
	debugfs_create_file("a52_446ac_hwc_requests",0400,NULL,NULL,
			    &a52ac_ring_fops);
	return 0;
}
late_initcall(a52ac_ring_init);
'''
def patch(s):
    if TAG in s:return s
    if "P446AB HWC_COMMIT" not in s or "P446AA DRM_INPUT" not in s:
        raise RuntimeError("P446AC RING requires 446AB source")
    s="#include <linux/ktime.h>\n#include <linux/debugfs.h>\n#include <linux/seq_file.h>\n"+s
    marker="/* A52_PHASE446AA_SE8_DRM_REQUEST_TRACE_V1; first 192 ioctls, no state modification */"
    s=one(s,marker,C+"\n"+marker,"ring declarations")
    s=one(s,"\t\ta52ab_count_hwc_ioctl(arg);",
       "\t\ta52ab_count_hwc_ioctl(arg);\n"
       "\t\ta52ac_ring_start(a52aa_seq,arg);",
       "create one ring entry on initial ioctl attempt")
    # Existing 446AA trace always logs a plane 91 property, not all properties.
    # Add ring recording immediately before every atomic property set.
    needle="\t\t\tret = drm_atomic_set_property(state, file_priv,"
    s=one(s,needle,
          "\t\t\ta52ac_ring_prop(a52aa_seq,obj,prop,prop_value);\n"+needle,
          "capture ALL property types and values")
    needle="out:\n\tcomplete_signaling(dev, state, fence_state, num_fences, !ret);"
    s=one(s,needle,
          "out:\n\ta52ac_ring_end(a52aa_seq);\n"
          "\tcomplete_signaling(dev, state, fence_state, num_fences, !ret);",
          "freeze after 16 post-trigger fully parsed requests")
    return s
def check(root):
    s=(root/PATH).read_text()
    for term in (TAG,"P446AC HWC_RING TRIGGER","P446AC HWC_RING READY",
                 "a52ac_ring_prop(a52aa_seq,obj,prop,prop_value)",
                 "a52_446ac_hwc_requests","A52AC_MAX_PROPS 96"):
        if term not in s:raise RuntimeError("P446AC RING missing "+term)
    print("P446AC HWC_RING PASS: 16 pre + 1 trigger + 16 post, all DRM object/property names and values, read-only debugfs")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    f=a.root/PATH
    if not a.check_only:f.write_text(patch(f.read_text()))
    check(a.root)
if __name__=="__main__":main()
