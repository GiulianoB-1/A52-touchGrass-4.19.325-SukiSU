#!/usr/bin/env python3
"""Phase446S GPU CX slumber diagnostics, with no power behavior changes.

Pinned TouchGrass gmu_disable_gdsc() keeps its original 5000 ms timeout,
regulator disable, and poll behavior. Inspect regulator votes under rdev lock;
measure all direct kgsl device->mutex lock/unlock pairs in the KGSL subtree.
"""
import argparse,re
from pathlib import Path

TAG="A52_PHASE446S_GMU_SLUMBER_DIAG_V1"
MUTEX_TAG="A52_PHASE446S_KGSL_MUTEX_100MS_V1"
VOTES_TAG="A52_PHASE446S_GPU_CX_VOTES_V1"
CORE=Path("drivers/regulator/core.c")
GMU=Path("drivers/gpu/msm/kgsl_gmu.c")
DEVHDR=Path("drivers/gpu/msm/kgsl_device.h")
API=Path("include/linux/a52_gpu_cx_trace.h")

HEADER=r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A52_GPU_CX_TRACE_H
#define A52_GPU_CX_TRACE_H
/* A52_PHASE446S_GPU_CX_VOTES_V1: diagnostic, no regulator writes */
struct regulator;
void a52_gpu_cx_dump_votes(struct regulator *reg, const char *phase);
#endif
'''

VOTES=r'''
/* A52_PHASE446S_GPU_CX_VOTES_V1
 * This is the only layer that may inspect regulator_dev internals.
 * Use the native regulator lock; do not call from within regulator_disable().
 */
void a52_gpu_cx_dump_votes(struct regulator *reg, const char *phase)
{
	struct regulator_dev *rdev;
	struct regulator *consumer;
	int active = 0;

	if (IS_ERR_OR_NULL(reg))
		return;
	rdev = reg->rdev;
	regulator_lock(rdev);
	pr_warn("P446S GPU_CX_VOTES phase=%s name=%s rdev_use_count=%d caller_enable_count=%d\n",
		phase, rdev_get_name(rdev), rdev->use_count, reg->enabled);
	list_for_each_entry(consumer, &rdev->consumer_list, list) {
		if (consumer->enabled <= 0)
			continue;
		active++;
		pr_warn("P446S GPU_CX_CONSUMER phase=%s device=%s supply=%s enable_count=%d\n",
			phase, consumer->dev ? dev_name(consumer->dev) : "(no-device)",
			consumer->supply_name ? consumer->supply_name : "(no-supply)",
			consumer->enabled);
	}
	if (!active)
		pr_warn("P446S GPU_CX_CONSUMER phase=%s enabled=NONE\n", phase);
	regulator_unlock(rdev);
}
EXPORT_SYMBOL_GPL(a52_gpu_cx_dump_votes);
'''

GPU_FUNC=r'''static int gmu_disable_gdsc(struct kgsl_device *device)
{
	struct gmu_device *gmu = KGSL_GMU_DEVICE(device);
	u64 start_ns = ktime_get_boottime_ns();
	u64 poll_start_ns = 0;
	u32 before = 0, after = 0;
	bool pre_valid = false, post_valid = false;
	int ret;
	unsigned long t;

	if (IS_ERR_OR_NULL(gmu->cx_gdsc)) {
		dev_warn(&gmu->pdev->dev,
			 "P446S GMU_CX SKIP invalid_regulator=%ld\n",
			 PTR_ERR_OR_ZERO(gmu->cx_gdsc));
		return 0;
	}

	/* A52_PHASE446S_GMU_SLUMBER_DIAG_V1
	 * Read CX GDSCR only when the existing GMU register mapping is valid.
	 * Do not wake the domain just to read a register.
	 */
	if (gmu_core_is_register_offset(device, A6XX_GPU_CC_CX_GDSCR) &&
	    gmu_core_dev_cx_is_on(device)) {
		gmu_core_regread(device, A6XX_GPU_CC_CX_GDSCR, &before);
		pre_valid = true;
	}
	a52_gpu_cx_dump_votes(gmu->cx_gdsc, "before_disable");
	dev_warn(&gmu->pdev->dev,
		 "P446S GMU_CX BEGIN pid=%d comm=%s gdscr_pre=%08x valid=%u timeout_ms=%u\n",
		 task_pid_nr(current), current->comm, before, pre_valid,
		 CX_GDSC_TIMEOUT);

	ret = regulator_disable(gmu->cx_gdsc);
	a52_gpu_cx_dump_votes(gmu->cx_gdsc, "after_disable");
	if (gmu_core_is_register_offset(device, A6XX_GPU_CC_CX_GDSCR) &&
	    gmu_core_dev_cx_is_on(device)) {
		gmu_core_regread(device, A6XX_GPU_CC_CX_GDSCR, &after);
		post_valid = true;
	}
	dev_warn(&gmu->pdev->dev,
		 "P446S GMU_CX AFTER_DISABLE rc=%d gdscr_pre=%08x pre_valid=%u gdscr_post=%08x post_valid=%u elapsed_ms=%llu\n",
		 ret, before, pre_valid, after, post_valid,
		 (unsigned long long)((ktime_get_boottime_ns() - start_ns) / 1000000));
	if (ret) {
		dev_err(&gmu->pdev->dev,
			"Failed to disable GMU CX gdsc, error %d\n", ret);
		return ret;
	}

	/* Unchanged 5 SECOND POLL. No timeout shortening, no GDSC writes. */
	poll_start_ns = ktime_get_boottime_ns();
	t = jiffies + msecs_to_jiffies(CX_GDSC_TIMEOUT);
	do {
		if (!gmu_core_dev_cx_is_on(device)) {
			dev_warn(&gmu->pdev->dev,
				 "P446S GMU_CX POLL rc=0 poll_ms=%llu gdscr_post=%08x post_valid=%u\n",
				 (unsigned long long)((ktime_get_boottime_ns() - poll_start_ns) / 1000000),
				 after, post_valid);
			return 0;
		}
		usleep_range(10, 100);
	} while (!(time_after(jiffies, t)));

	if (!gmu_core_dev_cx_is_on(device)) {
		dev_warn(&gmu->pdev->dev,
			 "P446S GMU_CX POLL rc=0 boundary=1 poll_ms=%llu\n",
			 (unsigned long long)((ktime_get_boottime_ns() - poll_start_ns) / 1000000));
		return 0;
	}
	if (gmu_core_is_register_offset(device, A6XX_GPU_CC_CX_GDSCR) &&
	    gmu_core_dev_cx_is_on(device)) {
		gmu_core_regread(device, A6XX_GPU_CC_CX_GDSCR, &after);
		post_valid = true;
	}
	dev_err(&gmu->pdev->dev,
		"P446S GMU_CX POLL rc=-110 poll_ms=%llu timeout_ms=%u gdscr_pre=%08x gdscr_post=%08x post_valid=%u\n",
		(unsigned long long)((ktime_get_boottime_ns() - poll_start_ns) / 1000000),
		CX_GDSC_TIMEOUT, before, after, post_valid);
	dev_err(&gmu->pdev->dev, "GMU CX gdsc off timeout\n");
	return -ETIMEDOUT;
}'''

MUTEX_HELPER=r'''
/* A52_PHASE446S_KGSL_MUTEX_100MS_V1: no lock ordering or semantics change. */
static inline void a52_kgsl_lock_trace(struct kgsl_device *device,
					const char *func, int line)
{
	mutex_lock(&device->mutex);
	device->a52_mutex_since_ns = ktime_get_boottime_ns();
	device->a52_mutex_func = func;
	device->a52_mutex_line = line;
	device->a52_mutex_pid = task_pid_nr(current);
}

static inline void a52_kgsl_unlock_trace(struct kgsl_device *device,
					  const char *func, int line)
{
	u64 start = device->a52_mutex_since_ns;
	u64 elapsed = start ? ktime_get_boottime_ns() - start : 0;

	if (start && elapsed >= 100000000ULL)
		dev_warn(device->dev,
			 "P446S KGSL_MUTEX held_ms=%llu owner_pid=%d owner_path=%s:%d unlock_path=%s:%d state=%u\n",
			 (unsigned long long)(elapsed / 1000000),
			 device->a52_mutex_pid,
			 device->a52_mutex_func ? device->a52_mutex_func : "?",
			 device->a52_mutex_line, func, line, device->state);
	device->a52_mutex_since_ns = 0;
	mutex_unlock(&device->mutex);
}
#define A52_KGSL_LOCK(dev) a52_kgsl_lock_trace((dev), __func__, __LINE__)
#define A52_KGSL_UNLOCK(dev) a52_kgsl_unlock_trace((dev), __func__, __LINE__)
'''

def once(s,a,b,label):
    n=s.count(a)
    if n!=1:
        raise RuntimeError("446S GPU %s: expected one anchor, found %d"%(label,n))
    return s.replace(a,b,1)

def replace_func(s,sig,body):
    i=s.find(sig)
    if i<0 or s.find(sig,i+1)>=0:
        raise RuntimeError("446S GPU missing/duplicate gmu_disable_gdsc")
    st=s.find("{",i)
    depth=0
    for n in range(st,len(s)):
        if s[n]=="{":depth+=1
        elif s[n]=="}":
            depth-=1
            if depth==0:break
    else:raise RuntimeError("446S GPU unbalanced C braces")
    old=s[i:n+1]
    for anchor in ("regulator_disable(gmu->cx_gdsc)",
                   "gmu_core_dev_cx_is_on(device)",
                   "CX_GDSC_TIMEOUT","return -ETIMEDOUT"):
        if anchor not in old:
            raise RuntimeError("446S GPU original contract absent: "+anchor)
    return s[:i]+body+s[n+1:]

def patch_core(s):
    if VOTES_TAG in s:return s
    sig="int regulator_disable(struct regulator *regulator)"
    return once(s,sig,VOTES+"\n"+sig,"regulator core insertion")

def patch_gmu(s):
    if TAG in s:return s
    s=once(s,'#include "kgsl_gmu.h"',
            '#include "kgsl_gmu.h"\n#include "adreno_a6xx.h"\n'
            '#include <linux/ktime.h>\n#include <linux/a52_gpu_cx_trace.h>',
            "gmu include")
    return replace_func(s,"static int gmu_disable_gdsc(struct kgsl_device *device)",GPU_FUNC)

def patch_devhdr(s):
    if MUTEX_TAG in s:return s
    s=once(s,"#include <linux/sched/task.h>",
             "#include <linux/sched/task.h>\n#include <linux/ktime.h>","kgsl ktime include")
    s=once(s,"\tstruct mutex mutex;\n",
           "\tstruct mutex mutex;\n"
           "\tu64 a52_mutex_since_ns;\n"
           "\tconst char *a52_mutex_func;\n"
           "\tint a52_mutex_line;\n"
           "\tpid_t a52_mutex_pid;\n","mutex struct")
    s=once(s,"#define KGSL_MMU_DEVICE(_mmu)",
            MUTEX_HELPER+"\n#define KGSL_MMU_DEVICE(_mmu)","mutex wrapper")
    return s

def patch(root,repo_root):
    api=root/API
    if api.exists() and VOTES_TAG not in api.read_text():
        raise RuntimeError("446S refusing to overwrite regulator trace API")
    api.write_text(HEADER)
    for path,fn in ((CORE,patch_core),(GMU,patch_gmu),(DEVHDR,patch_devhdr)):
        f=root/path
        if not f.is_file():
            raise RuntimeError("446S GPU missing imported file "+str(f))
        f.write_text(fn(f.read_text()))
    # Instrument all direct mutex locking and unlocking in every imported
    # KGSL C file. The paired lock/unlock can live in different functions.
    count=0
    files=0
    for f in (root/"drivers/gpu/msm").glob("*.c"):
        src=f.read_text(errors="replace")
        if "A52_KGSL_LOCK(device)" in src:
            continue
        locks=src.count("mutex_lock(&device->mutex)")
        unlocks=src.count("mutex_unlock(&device->mutex)")
        if not locks and not unlocks:
            continue
        if '#include "kgsl_device.h"' not in src:
            pos=src.find("#include ")
            if pos<0:
                raise RuntimeError("446S KGSL no includes in "+str(f))
            src=src[:pos]+'#include "kgsl_device.h"\n'+src[pos:]
        src=src.replace("mutex_lock(&device->mutex)","A52_KGSL_LOCK(device)")
        src=src.replace("mutex_unlock(&device->mutex)","A52_KGSL_UNLOCK(device)")
        f.write_text(src)
        count+=locks
        files+=1
    if files==0 or count==0:
        raise RuntimeError("446S KGSL mutex hooks not installed")
    print("P446S KGSL_MUTEX patched_files=%d lock_sites=%d"%(files,count))

def check(root):
    for p,tokens in (
        (CORE,(VOTES_TAG,"P446S GPU_CX_VOTES","P446S GPU_CX_CONSUMER")),
        (GMU,(TAG,"P446S GMU_CX BEGIN","P446S GMU_CX POLL",
              "#define CX_GDSC_TIMEOUT\t5000")),
        (DEVHDR,(MUTEX_TAG,"P446S KGSL_MUTEX","A52_KGSL_LOCK")),
        (API,(VOTES_TAG,"a52_gpu_cx_dump_votes")),
    ):
        content=(root/p).read_text()
        for token in tokens:
            if token not in content:
                raise RuntimeError("446S GPU audit missing "+token+" in "+str(p))
    g=(root/GMU).read_text()
    if "msecs_to_jiffies(CX_GDSC_TIMEOUT)" not in g:
        raise RuntimeError("446S GPU poll timeout changed")
    for fragment in ("regulator_disable(gmu->cx_gdsc)",
                     "usleep_range(10, 100)","return -ETIMEDOUT"):
        if fragment not in g:
            raise RuntimeError("446S GPU legacy behavior lost: "+fragment)
    print("P446S GPU diagnostic audit PASS: 5000ms poll preserved, votes, CX GDSCR, mutex timing")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    opt=p.parse_args()
    if not opt.check_only:
        patch(opt.root,Path(__file__).resolve().parent.parent)
    check(opt.root)
if __name__=="__main__":
    main()
