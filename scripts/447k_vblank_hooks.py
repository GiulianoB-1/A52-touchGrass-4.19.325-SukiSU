#!/usr/bin/env python3
"""447K: connect Qualcomm 4.19 legacy SDE vblank controls to DRM 5.10 KMS CRTC hooks.

Runtime proof from 447J:
  CRTC_HOOK_MISSING_KMS_LEGACY_ONLY rc=-22 pipe=0 modeset=1
  legacy_cb=1 crtc_cb=0

Display-only patch. Retains the existing msm_enable_vblank / msm_disable_vblank
worker and the 447J diagnostic; no vblank IRQ, splash or modeset changes.
"""
import argparse
from pathlib import Path

TAG = "A52_PHASE447K_KMS_CRTC_VBLANK_HOOK_V1"
DRV = Path("drivers/a52_display/msm/msm_drv.c")
HDR = Path("drivers/a52_display/msm/msm_drv.h")
CRTC = Path("drivers/a52_display/msm/sde/sde_crtc.c")
VBLANK = Path("drivers/gpu/drm/drm_vblank.c")

def once(src, old, new, label):
    n = src.count(old)
    if n != 1:
        raise RuntimeError("P447K " + label + ": expected 1 anchor, got " + str(n))
    return src.replace(old, new, 1)

def apply(root):
    paths = (root / DRV, root / HDR, root / CRTC)
    originals = [p.read_text() for p in paths]
    tagged = [TAG in s for s in originals]
    if any(tagged):
        if not all(tagged):
            raise RuntimeError("P447K partial patch: refusing mixed source state")
        return

    drv, hdr, crtc = originals
    if "static int msm_enable_vblank(struct drm_device *dev, unsigned int pipe)" not in drv:
        raise RuntimeError("P447K legacy enable callback absent")
    if "static void msm_disable_vblank(struct drm_device *dev, unsigned int pipe)" not in drv:
        raise RuntimeError("P447K legacy disable callback absent")
    if "return vblank_ctrl_queue_work(priv, pipe, true);" not in drv:
        raise RuntimeError("P447K TG enable worker contract absent")
    if "vblank_ctrl_queue_work(priv, pipe, false);" not in drv:
        raise RuntimeError("P447K TG disable worker contract absent")
    if ".enable_vblank" not in drv or ".disable_vblank" not in drv:
        raise RuntimeError("P447K legacy drm_driver hooks missing")

    helper = r'''/* A52_PHASE447K_KMS_CRTC_VBLANK_HOOK_V1
 * ACK 5.10 DRM_MODESET requires hooks on drm_crtc_funcs.
 * Reuse the proven TG queueing logic exactly. No direct IRQ manipulation.
 */
int a52k_msm_crtc_enable_vblank(struct drm_crtc *crtc)
{
	int rc;

	if (!crtc || !crtc->dev)
		return -EINVAL;

	rc = msm_enable_vblank(crtc->dev, drm_crtc_index(crtc));
	pr_info_ratelimited("P447K VBLANK enable pipe=%u crtc=%u rc=%d\n",
			    drm_crtc_index(crtc), crtc->base.id, rc);
	return rc;
}

void a52k_msm_crtc_disable_vblank(struct drm_crtc *crtc)
{
	if (!crtc || !crtc->dev)
		return;

	pr_info_ratelimited("P447K VBLANK disable pipe=%u crtc=%u\n",
			    drm_crtc_index(crtc), crtc->base.id);
	msm_disable_vblank(crtc->dev, drm_crtc_index(crtc));
}

'''
    drv = once(drv, "/*\n * DRM ioctls:\n */",
               helper + "/*\n * DRM ioctls:\n */", "insert wrappers")
    hdr = once(hdr, "struct msm_kms;\n",
               "/* " + TAG + " */\n"
               "int a52k_msm_crtc_enable_vblank(struct drm_crtc *crtc);\n"
               "void a52k_msm_crtc_disable_vblank(struct drm_crtc *crtc);\n"
               "struct msm_kms;\n", "header declarations")
    funcs = "static const struct drm_crtc_funcs sde_crtc_funcs = {\n"
    crtc = once(crtc, funcs, 
                "/* " + TAG + " */\n" + funcs +
                "\t.enable_vblank = a52k_msm_crtc_enable_vblank,\n"
                "\t.disable_vblank = a52k_msm_crtc_disable_vblank,\n",
                "install DRM 5.10 CRTC callbacks")
    for p, data in zip(paths, (drv, hdr, crtc)):
        p.write_text(data)
    print("P447K APPLY: DRM 5.10 KMS CRTC hooks -> original TG vblank worker")

def check(root):
    drv = (root / DRV).read_text()
    hdr = (root / HDR).read_text()
    crtc = (root / CRTC).read_text()
    drm = (root / VBLANK).read_text()
    for name, data in (("msm_drv.c", drv), ("msm_drv.h", hdr), ("sde_crtc.c", crtc)):
        if TAG not in data:
            raise RuntimeError("P447K missing tag in " + name)
    if "A52_PHASE447J_VBLANK_DIAG_V1" not in drm:
        raise RuntimeError("P447K 447J runtime diagnostic unexpectedly missing")
    for t in ("rc = msm_enable_vblank(crtc->dev, drm_crtc_index(crtc));",
              "msm_disable_vblank(crtc->dev, drm_crtc_index(crtc));",
              "return vblank_ctrl_queue_work(priv, pipe, true);",
              "vblank_ctrl_queue_work(priv, pipe, false);",
              ".enable_vblank      = msm_enable_vblank,",
              ".disable_vblank     = msm_disable_vblank,"):
        if t not in drv:
            raise RuntimeError("P447K legacy callback/worker contract absent: " + t)
    for t in ("int a52k_msm_crtc_enable_vblank(struct drm_crtc *crtc);",
              "void a52k_msm_crtc_disable_vblank(struct drm_crtc *crtc);"):
        if t not in hdr:
            raise RuntimeError("P447K declaration missing: " + t)
    start = crtc.index("static const struct drm_crtc_funcs sde_crtc_funcs = {")
    end = crtc.index("};", start)
    cb = crtc[start:end]
    for t in (".enable_vblank = a52k_msm_crtc_enable_vblank,",
              ".disable_vblank = a52k_msm_crtc_disable_vblank,"):
        if cb.count(t) != 1:
            raise RuntimeError("P447K CRTC hook missing or duplicate: " + t)
    base = root / "drivers/a52_display/msm"
    counters = {"drm_crtc_handle_vblank": [], "drm_handle_vblank": [],
                "drm_crtc_vblank_on": [], "drm_crtc_vblank_off": [],
                ".get_vblank_counter": []}
    for p in base.rglob("*.c"):
        s = p.read_text(errors="replace")
        for name in counters:
            if name in s:
                counters[name].append(str(p.relative_to(root)))
    for name, paths in counters.items():
        print("P447K AUDIT " + name + "=" + (",".join(paths[:8]) if paths else "NOT_FOUND"))
    print("P447K PASS: two 5.10 CRTC hooks; TG legacy callbacks and workqueue unchanged; P447J kept")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    if not args.check_only:
        apply(args.root)
    check(args.root)
