#!/usr/bin/env python3
"""Phase446Q: source audit and bounded graphics buffer pipeline witnesses.

The only functional fix in 446Q is the previously-tested Phase148 ION exporter
fallback applied separately before this script. This script is READ-ONLY as
regards the PRIME import contract, FB/atomic checks, and SMMU behavior.
No importer fallback or synthetic secure flags are permitted.
"""
from pathlib import Path
import argparse
import json
import re

MARK="A52_PHASE446Q_ION_PRIME_TRACE_V1"
BASE=Path("drivers/a52_display/msm")
ION=Path("drivers/staging/android/ion/ion_dma_buf.c")
def swap(text,old,new,what):
    n=text.count(old)
    if n!=1:raise RuntimeError(f"446Q {what}: anchor count {n}")
    return text.replace(old,new,1)

def function(text,signature,transform):
    a=text.find(signature)
    if a<0 or text.find(signature,a+1)>=0:raise RuntimeError("446Q function anchor: "+signature)
    b=text.find("\n}\n",a)
    if b<0:raise RuntimeError("446Q function close: "+signature)
    return text[:a]+transform(text[a:b+3])+text[b+3:]

def patch_ion(s):
    if MARK in s:return s
    if "A52_ION_DMABUF_FLAGS_FALLBACK" not in s:
        raise RuntimeError("446Q missing Phase148 exporter fallback")
    def change(f):
        x="\tstruct ion_heap *heap = buffer->heap;\n"
        y=x+'''    /* A52_PHASE446Q_ION_PRIME_TRACE_V1: one-shot exporter audit. */
    {
        static atomic_t logged = ATOMIC_INIT(0);
        if (atomic_cmpxchg(&logged, 0, 1) == 0)
            pr_warn("P446Q ION_GET_FLAGS exp=%s heap=%s heap_cb=%ps stored=%lx\\n",
                dmabuf->exp_name ? dmabuf->exp_name : "<null>",
                heap->name ? heap->name : "<null>",
                heap->buf_ops.get_flags, buffer->flags);
    }
'''
        return swap(f,x,y,"ION optional-heap callback")
    return function(s,"static int ion_dma_buf_get_flags(struct dma_buf *dmabuf,",change)

def patch_prime(s):
    if MARK in s:return s
    if 'dma_buf_get_flags failure, err=%d' not in s:
        raise RuntimeError("446Q PRIME import anchor missing")
    start="struct drm_gem_object *msm_gem_prime_import(struct drm_device *dev,"
    def change(f):
        f=swap(f,'\tint ret;\n','\tint ret;\n\tbool trace_first;\n',"prime local")
        a='\tif (!dma_buf || !dev->dev_private)\n\t\treturn ERR_PTR(-EINVAL);\n'
        b=a+'''    {
        static atomic_t first = ATOMIC_INIT(0);
        trace_first = atomic_cmpxchg(&first, 0, 1) == 0;
    }
    if (trace_first)
        pr_warn("P446Q PRIME_BEGIN exp=%s size=%zu ops_get_flags=%ps\\n",
            dma_buf->exp_name ? dma_buf->exp_name : "<null>",
            dma_buf->size, dma_buf->ops->get_flags);
'''
        f=swap(f,a,b,"prime begin")
        a='\tret = dma_buf_get_flags(dma_buf, &flags);\n'
        b=a+'''    if (trace_first)
        pr_warn("P446Q PRIME_FLAGS rc=%d value=0x%lx exp=%s\\n",
            ret, flags, dma_buf->exp_name ? dma_buf->exp_name : "<null>");
'''
        f=swap(f,a,b,"flags exact result")
        a='\tobj->import_attach = attach;\n\n\treturn obj;'
        b='''    obj->import_attach = attach;
    if (trace_first)
        pr_warn("P446Q PRIME_OK exp=%s size=%zu secure=%u cached=%u\\n",
            dma_buf->exp_name ? dma_buf->exp_name : "<null>",
            dma_buf->size, !!(flags & ION_FLAG_SECURE),
            !!(flags & ION_FLAG_CACHED));
    return obj;'''
        f=swap(f,a,b,"success")
        a='fail_put:\n\tdma_buf_put(dma_buf);\n\n\treturn ERR_PTR(ret);'
        b='''fail_put:
    if (trace_first)
        pr_warn("P446Q PRIME_FAIL ret=%d flags=0x%lx\\n",ret,flags);
    dma_buf_put(dma_buf);
    return ERR_PTR(ret);'''
        f=swap(f,a,b,"failure")
        return f
    return function(s,start,change)+f"\n/* {MARK}: PRIME staged. */\n"

def patch_drv(s):
    if MARK in s:return s
    wrapper=r'''
/* A52_PHASE446Q_ION_PRIME_TRACE_V1: single PRIME fd->handle boundary */
static int a52_p446q_prime_fd_to_handle(struct drm_device *dev,
        struct drm_file *file_priv, int prime_fd, uint32_t *handle)
{
    static atomic_t count = ATOMIC_INIT(0);
    int rc = drm_gem_prime_fd_to_handle(dev, file_priv, prime_fd, handle);
    int n = atomic_inc_return(&count);
    if (n <= 12)
        pr_warn("P446Q FD_HANDLE n=%d rc=%d handle=%u\\n",
            n, rc, (rc || !handle) ? 0 : *handle);
    return rc;
}
'''
    s=swap(s,"static struct drm_driver msm_driver = {",wrapper+
        "\nstatic struct drm_driver msm_driver = {","DRM wrapper")
    return swap(s,"\t.prime_fd_to_handle = drm_gem_prime_fd_to_handle,",
        "\t.prime_fd_to_handle = a52_p446q_prime_fd_to_handle,","DRM callback")

def patch_fb(s):
    if MARK in s:return s
    def change(f):
        a="\tint ret, i, n = drm_format_num_planes(mode_cmd->pixel_format);\n"
        b=a+"\tstatic atomic_t a52_fb_count = ATOMIC_INIT(0);\n"+
        "\tint a52_fb_n = atomic_inc_return(&a52_fb_count);\n"
        f=swap(f,a,b,"FB count")
        a="\treturn fb;\n"
        b='''    if (a52_fb_n <= 12)
        pr_warn("P446Q FB_CREATE n=%d rc=0 fb=%u dims=%ux%u fmt=%08x\\n",
            a52_fb_n,fb->base.id,mode_cmd->width,mode_cmd->height,
            mode_cmd->pixel_format);
    return fb;
'''
        f=swap(f,a,b,"FB success")
        a="\treturn ERR_PTR(ret);\n"
        b='''    if (a52_fb_n <= 12)
        pr_warn("P446Q FB_CREATE n=%d rc=%d dims=%ux%u fmt=%08x planes=%d\\n",
            a52_fb_n,ret,mode_cmd->width,mode_cmd->height,
            mode_cmd->pixel_format,n);
    return ERR_PTR(ret);
'''
        return swap(f,a,b,"FB failure")
    return function(s,"struct drm_framebuffer *msm_framebuffer_create(struct drm_device *dev,",change)+f"\n/* {MARK}: FB stage. */\n"

def patch_kms(s):
    if MARK in s:return s
    def change(f):
        a='\tint ret;\n'
        b=a+'\tconst char *a52_stage = "entry";\n'
        f=swap(f,a,b,"KMS local")
        f=swap(f,"\tret = drm_atomic_helper_check(dev, state);",
            '\ta52_stage = "helper";\n\tret = drm_atomic_helper_check(dev, state);',"KMS helper")
        f=swap(f,"\tret = sde_kms_check_secure_transition(kms, state);",
            '\ta52_stage = "secure";\n\tret = sde_kms_check_secure_transition(kms, state);',"KMS secure")
        f=swap(f,'\t\tret = -EBUSY;\n\t\tgoto end;',
            '\t\ta52_stage = "suspended";\n\t\tret = -EBUSY;\n\t\tgoto end;',"KMS suspend")
        a='end:\n\tSDE_ATRACE_END("atomic_check");'
        b='''end:
    if (ret) {
        static atomic_t fail_count = ATOMIC_INIT(0);
        unsigned int n=(unsigned int)atomic_inc_return(&fail_count);
        if (n<=12)
            pr_warn("P446Q ATOMIC_CHECK fail_n=%u stage=%s rc=%d\\n",
                n,a52_stage,ret);
    }
    SDE_ATRACE_END("atomic_check");'''
        return swap(f,a,b,"KMS return")
    return function(s,"static int sde_kms_atomic_check(struct msm_kms *kms,",change)+f"\n/* {MARK}: atomic stage. */\n"

def patch_plane(s):
    if MARK in s:return s
    def change(f):
        a='exit:\n\treturn ret;\n'
        b='''exit:
    if (ret) {
        static atomic_t errors = ATOMIC_INIT(0);
        int n=atomic_inc_return(&errors);
        if (n<=12)
            pr_warn("P446Q PLANE_CHECK n=%d plane=%u fb=%u rc=%d\\n",
                n,plane ? plane->base.id : 0,
                state && state->fb ? state->fb->base.id : 0,ret);
    }
    return ret;
'''
        return swap(f,a,b,"plane exit")
    return function(s,"static int sde_plane_atomic_check(struct drm_plane *plane,",change)+f"\n/* {MARK}: plane stage. */\n"

def patch_smmu(s):
    if MARK in s:return s
    def change(f):
        a="\treturn (ret == len) ? 0 : -EINVAL;\n"
        b='''    if (ret != len) {
        static atomic_t errors = ATOMIC_INIT(0);
        int n=atomic_inc_return(&errors);
        if (n<=12)
            pr_warn("P446Q SMMU_MAP_FAIL n=%d iova=%llx mapped=%zu expected=%u sgt_nents=%u prot=%x\\n",
                n,(unsigned long long)iova,ret,len,
                sgt ? sgt->nents : 0,prot);
    }
    return (ret == len) ? 0 : -EINVAL;
'''
        return swap(f,a,b,"SMMU map return")
    return function(s,"static int msm_smmu_map(struct msm_mmu *mmu,",change)+f"\n/* {MARK}: SMMU stage. */\n"

FILES={
    ION:patch_ion,
    BASE/"msm_gem_prime.c":patch_prime,
    BASE/"msm_drv.c":patch_drv,
    BASE/"msm_fb.c":patch_fb,
    BASE/"sde/sde_kms.c":patch_kms,
    BASE/"sde/sde_plane.c":patch_plane,
    BASE/"msm_smmu.c":patch_smmu,
}

def check(root):
    for rel in FILES:
        text=(root/rel).read_text()
        if rel==ION:
            for marker in ("A52_ION_DMABUF_FLAGS_FALLBACK",
                "*flags = buffer->flags;","P446Q ION_GET_FLAGS"):
                if marker not in text:raise RuntimeError("ION contract missing "+marker)
        else:
            if MARK not in text:raise RuntimeError("Phase446Q marker missing "+str(rel))
    a=(root/BASE/"msm_gem_prime.c").read_text()
    if "P446Q PRIME_FLAGS" not in a or "P446Q PRIME_OK" not in a:
        raise RuntimeError("PRIME flags and success witnesses missing")
    if "flags = 0" in a.replace("unsigned long flags = 0;",""):
        raise RuntimeError("unexpected importer-side zero-flags fallback")
    print("P446Q graphics witnesses PASS: exporter flags, prime, fd->handle, FB, atomic, plane, SMMU; no importer-side fallback")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    if not a.check_only:
        updates=[(a.root/rel,patch((a.root/rel).read_text())) for rel,patch in FILES.items()]
        for path,text in updates:path.write_text(text)
    check(a.root)

if __name__=="__main__":main()
