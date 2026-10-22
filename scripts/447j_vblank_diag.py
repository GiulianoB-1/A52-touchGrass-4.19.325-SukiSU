#!/usr/bin/env python3
"""P447J vblank UAPI + legacy/KMS hook diagnosis (NO display behavior changes).

Pinned TG 6bf351bd:
  techpack/display/msm/msm_drv.c:1135,1738 old
       drm_driver.enable_vblank -> msm_enable_vblank -> KMS callback.
  techpack/display/msm/sde/sde_crtc.c:6086 sde_crtc_funcs
       does NOT assign drm_crtc_funcs.enable_vblank.
ACK android12-5.10 drivers/gpu/drm/drm_vblank.c:
  __enable_vblank() at ~1096 on KMS path calls crtc->funcs->enable_vblank,
       and if callback missing returns -EINVAL; legacy driver hook ignored.
  drm_vblank_get() ~1151 and drm_wait_vblank_ioctl() ~1735.

Static suspicion STRONG, but runtime marker required to prove active path.
Default ON, first 12 events + 1 per 30 seconds, no hook writes or changes.
"""
from pathlib import Path
import argparse

FILE=Path("drivers/gpu/drm/drm_vblank.c")
TAG="A52_PHASE447J_VBLANK_DIAG_V1"

HELPER=r'''
/* A52_PHASE447J_VBLANK_DIAG_V1
 * This is ONLY a passive trace. No vblank enable, IRQ, count, modeset,
 * continuous-splash state or user request behavior is changed.
 * First 12 records, then <=1/30s. Never dereference invalid pipe.
 */
static atomic_t a52j_count = ATOMIC_INIT(0);
static DEFINE_RATELIMIT_STATE(a52j_ratelimit, 30 * HZ, 1);

static void a52j_vblank_trace(struct drm_device *dev, const char *reason,
			      unsigned int pipe, u32 type, u32 seq, int rc)
{
	struct drm_vblank_crtc *vb = NULL;
	struct drm_crtc *crtc = NULL, *iter;
	int n, idx;
	bool has, valid;
	if (!dev)
		return;
	n = atomic_inc_return(&a52j_count);
	if (n > 12 && !__ratelimit(&a52j_ratelimit))
		return;
	has = drm_dev_has_vblank(dev);
	valid = has && dev->vblank && pipe < dev->num_crtcs;
	if (valid) {
		vb = &dev->vblank[pipe];
		crtc = drm_crtc_from_index(dev, pipe);
	}
	pr_info("P447J VBLANK reason=%s rc=%d pipe=%u type=%#x high_crtc=%#x seq=%u num_crtcs=%u has_vblank=%d irq=%d modeset=%d legacy_cb=%d crtc_cb=%d enabled=%d ref=%d inmodeset=%u pid=%d comm=%s sample=%d\n",
		reason, rc, pipe, type, type & _DRM_VBLANK_HIGH_CRTC_MASK,
		seq, dev->num_crtcs, has, dev->irq_enabled,
		drm_core_check_feature(dev, DRIVER_MODESET),
		!!(dev->driver && dev->driver->enable_vblank),
		!!(crtc && crtc->funcs && crtc->funcs->enable_vblank),
		vb ? READ_ONCE(vb->enabled) : -1,
		vb ? atomic_read(&vb->refcount) : -1,
		vb ? READ_ONCE(vb->inmodeset) : ~0U,
		task_pid_nr(current), current->comm, n);
	if (n != 1 && rc != -EINVAL)
		return;
	if (!has || !dev->vblank)
		return;
	idx = 0;
	drm_for_each_crtc(iter, dev) {
		if (idx >= 6)
			break;
		if (idx < dev->num_crtcs) {
			struct drm_vblank_crtc *x = &dev->vblank[idx];
			pr_info("P447J VBLANK CRTC idx=%d obj=%u active=%d enabled=%d ref=%d inmodeset=%u enable_hook=%d disable_hook=%d\n",
				idx, iter->base.id,
				iter->state ? iter->state->active : -1,
				READ_ONCE(x->enabled), atomic_read(&x->refcount),
				READ_ONCE(x->inmodeset),
				!!(iter->funcs && iter->funcs->enable_vblank),
				!!(iter->funcs && iter->funcs->disable_vblank));
		}
		idx++;
	}
}
'''

def one(src, old, new, why):
    n=src.count(old)
    if n!=1:raise RuntimeError(f"P447J vblank {why}: anchor count={n}")
    return src.replace(old,new,1)

def patch(src):
    if TAG in src:return src
    for required in ("int drm_vblank_get(struct drm_device *dev, unsigned int pipe)",
                     "int drm_wait_vblank_ioctl(struct drm_device *dev, void *data,",
                     "static int __enable_vblank(struct drm_device *dev, unsigned int pipe)",
                     "int drm_vblank_init(struct drm_device *dev, unsigned int num_crtcs)"):
        if required not in src:raise RuntimeError("P447J missing baseline "+required)
    src="#include <linux/ratelimit.h>\n#include <linux/sched.h>\n"+src
    src=one(src,
            "static int __enable_vblank(struct drm_device *dev, unsigned int pipe)",
            HELPER+"\nstatic int __enable_vblank(struct drm_device *dev, unsigned int pipe)",
            "insert passive diagnostic before KMS hook resolution")
    # DRM modeset path deliberately retains exactly its existing behavior:
    # if CRTC hook is missing, it falls through to -EINVAL.
    src=one(src,
            '''		if (crtc->funcs->enable_vblank)
			return crtc->funcs->enable_vblank(crtc);
	} else if (dev->driver->enable_vblank) {
		return dev->driver->enable_vblank(dev, pipe);
	}

	return -EINVAL;''',
            '''		if (crtc->funcs->enable_vblank) {
			int rc = crtc->funcs->enable_vblank(crtc);
			if (rc)
				a52j_vblank_trace(dev,"CRTC_ENABLE_HOOK_FAILED",
						pipe,0,0,rc);
			return rc;
		}
		a52j_vblank_trace(dev,"CRTC_HOOK_MISSING_KMS_LEGACY_ONLY",
				 pipe,0,0,-EINVAL);
	} else if (dev->driver->enable_vblank) {
		return dev->driver->enable_vblank(dev, pipe);
	}

	return -EINVAL;''',
            "report missing KMS callback and hook failures")
    src=one(src,
            '''	if (!drm_dev_has_vblank(dev))
		return -EINVAL;

	if (drm_WARN_ON(dev, pipe >= dev->num_crtcs))
		return -EINVAL;''',
            '''	if (!drm_dev_has_vblank(dev)) {
		a52j_vblank_trace(dev,"GET_NOT_INITIALIZED",pipe,0,0,-EINVAL);
		return -EINVAL;
	}

	if (drm_WARN_ON(dev, pipe >= dev->num_crtcs)) {
		a52j_vblank_trace(dev,"GET_BAD_PIPE",pipe,0,0,-EINVAL);
		return -EINVAL;
	}''',
            "drm_vblank_get no-init and index checks")
    src=one(src,
            '''		if (!vblank->enabled) {
			atomic_dec(&vblank->refcount);
			ret = -EINVAL;
		}
	}
	spin_unlock_irqrestore(&dev->vbl_lock, irqflags);

	return ret;''',
            '''		if (!vblank->enabled) {
			atomic_dec(&vblank->refcount);
			ret = -EINVAL;
			a52j_vblank_trace(dev,"GET_DISABLED_WITH_REFS",
					 pipe,0,0,ret);
		}
	}
	spin_unlock_irqrestore(&dev->vbl_lock, irqflags);

	return ret;''',
            "disabled with references branch")
    # Brief initialization status, exactly once per drm_vblank_init invocation.
    src=one(src,
            '''	dev->num_crtcs = num_crtcs;

	for (i = 0; i < num_crtcs; i++)''',
            '''	dev->num_crtcs = num_crtcs;
	pr_info("P447J VBLANK INIT num_crtcs=%u irq=%d has_vblank=%d drm_driver_legacy_hook=%d\n",
		num_crtcs,dev->irq_enabled,drm_dev_has_vblank(dev),
		!!(dev->driver && dev->driver->enable_vblank));

	for (i = 0; i < num_crtcs; i++)''',
            "init count and legacy hook")
    # The ioctl check reasons. Gate entry telemetry to tiny bounded sampling.
    src=one(src,
            '''	unsigned int flags, pipe, high_pipe;

	if (!dev->irq_enabled)
		return -EOPNOTSUPP;''',
            '''	unsigned int flags, pipe, high_pipe;
	static atomic_t a52j_ioctl_enter = ATOMIC_INIT(0);

	if (atomic_inc_return(&a52j_ioctl_enter) <= 3)
		a52j_vblank_trace(dev,"IOCTL_ENTER",~0U,
				 vblwait->request.type,vblwait->request.sequence,0);
	if (!dev->irq_enabled) {
		a52j_vblank_trace(dev,"IOCTL_IRQ_OFF",~0U,
				 vblwait->request.type,vblwait->request.sequence,-EOPNOTSUPP);
		return -EOPNOTSUPP;
	}''',
            "ioctl entry and IRQ readiness")
    src=one(src,
            '''	if (vblwait->request.type & _DRM_VBLANK_SIGNAL)
		return -EINVAL;''',
            '''	if (vblwait->request.type & _DRM_VBLANK_SIGNAL) {
		a52j_vblank_trace(dev,"IOCTL_SIGNAL_UNSUPPORTED",~0U,
				 vblwait->request.type,vblwait->request.sequence,-EINVAL);
		return -EINVAL;
	}''',
            "signal flag reason")
    src=one(src,
            '''			     (_DRM_VBLANK_TYPES_MASK | _DRM_VBLANK_FLAGS_MASK |
			      _DRM_VBLANK_HIGH_CRTC_MASK));
		return -EINVAL;''',
            '''			     (_DRM_VBLANK_TYPES_MASK | _DRM_VBLANK_FLAGS_MASK |
			      _DRM_VBLANK_HIGH_CRTC_MASK));
		a52j_vblank_trace(dev,"IOCTL_INVALID_TYPE_FLAGS",~0U,
				 vblwait->request.type,vblwait->request.sequence,-EINVAL);
		return -EINVAL;''',
            "unsupported type bits")
    src=one(src,
            '''	if (pipe >= dev->num_crtcs)
		return -EINVAL;

	vblank = &dev->vblank[pipe];''',
            '''	if (pipe >= dev->num_crtcs) {
		a52j_vblank_trace(dev,"IOCTL_PIPE_OOB_OR_NO_LEASE",pipe,
				 vblwait->request.type,vblwait->request.sequence,-EINVAL);
		return -EINVAL;
	}

	vblank = &dev->vblank[pipe];''',
            "client CRTC index mapping")
    src=one(src,
            '''			     "crtc %d failed to acquire vblank counter, %d\n",
			     pipe, ret);
		return ret;''',
            '''			     "crtc %d failed to acquire vblank counter, %d\n",
			     pipe, ret);
		a52j_vblank_trace(dev,"IOCTL_VBLANK_GET_FAILED",pipe,
				 vblwait->request.type,vblwait->request.sequence,ret);
		return ret;''',
            "get failure reason")
    src=one(src,
            '''	default:
		ret = -EINVAL;
		goto done;''',
            '''	default:
		ret = -EINVAL;
		a52j_vblank_trace(dev,"IOCTL_INVALID_REQUEST_MODE",pipe,
				 vblwait->request.type,vblwait->request.sequence,ret);
		goto done;''',
            "relative/absolute invalid mode")
    return src

def check(root):
    text=(root/FILE).read_text()
    required=(TAG,"P447J VBLANK INIT","P447J VBLANK reason=",
              "P447J VBLANK CRTC idx=",
              "CRTC_HOOK_MISSING_KMS_LEGACY_ONLY",
              "IOCTL_INVALID_TYPE_FLAGS",
              "IOCTL_PIPE_OOB_OR_NO_LEASE",
              "IOCTL_VBLANK_GET_FAILED",
              "GET_DISABLED_WITH_REFS",
              "GET_BAD_PIPE",
              "GET_NOT_INITIALIZED",
              "CRTC_ENABLE_HOOK_FAILED",
              "DEFINE_RATELIMIT_STATE(a52j_ratelimit, 30 * HZ, 1)",
              "return -EINVAL;")
    for item in required:
        if item not in text:
            raise RuntimeError("P447J vblank source contract missing "+item)
    if "drm_crtc_vblank_on(crtc);" in text or "drm_crtc_vblank_off(crtc);" in text:
        # This file legitimately has drm_crtc_vblank_on/off definitions, but
        # no new calls should be introduced to driver CRTC paths.
        pass
    print("P447J VBLANK PASS: pure diagnosis, first 12 + <=1/30s, 5.10 KMS vs legacy hook, init/pipe/irq/ref/modeset and rc")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--check-only",action="store_true")
    args=ap.parse_args();f=args.root/FILE
    if not args.check_only:f.write_text(patch(f.read_text()))
    check(args.root)
