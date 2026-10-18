#!/usr/bin/env python3
"""446AC: strictly pin & stage TouchGrass QCOM GPI DMAengine provider.

Source commit 6bf351bd, 3 blob SHAs separately verified against GitHub.
Only the GPI DMA driver is staged; no other vendor drivers or core DMA files.
"""
from pathlib import Path
import argparse,hashlib,urllib.request
TAG="A52_PHASE446AC_TG_GPI_DMA_V1"
PIN="6bf351bd"
BASE=f"https://raw.githubusercontent.com/micr0softstore/samsung_android_kernel_a52xq/{PIN}/"
SOURCES={
 "drivers/dma/qcom/gpi.c":"5947176879efda2eab6da547c33a4ad8a18b827e",
 "drivers/dma/qcom/msm_gpi_mmio.h":"74a390b21dab05df5a8da7ef2a9b650139479c03",
 "include/linux/msm_gpi.h":"11f9cf7bd2a20770d032847511744609dafbcc12",
}
# TG GPI uses downstream IPC log strings solely for debug telemetry.
# ACK 5.10 has no ipc_logging.h. Keep the exact pinned GPI driver byte-for-byte
# and provide a no-op log transport; ordinary dev_err/pr_err remain functional.
IPC_SHIM = """/* A52_PHASE446AC_GPI_IPC_SHIM_V1 - logging only, not GPI transport */
#ifndef _LINUX_IPC_LOGGING_H
#define _LINUX_IPC_LOGGING_H
#include <linux/types.h>
static inline void *ipc_log_context_create(int pages,const char *name,int flags)
{
    return NULL;
}
static inline void ipc_log_string(void *ctx,const char *fmt,...) { }
#endif
"""
def adapt_gpi_510(src):
    # 5.10 renamed devm_ioremap_nocache; devm_ioremap preserves TG's
    # NULL-on-failure behavior. Exact upstream source kept for audit.
    old=b"devm_ioremap_nocache("
    if src.count(old)!=1:raise RuntimeError("P446AC GPI mapping anchor drift")
    return src.replace(old,b"devm_ioremap(",1)
def blob(s):
    return hashlib.sha1(b"blob "+str(len(s)).encode()+b"\0"+s).hexdigest()
def one(s,a,b,label):
    if s.count(a)!=1:raise RuntimeError("P446AC "+label+" matches="+str(s.count(a)))
    return s.replace(a,b,1)
def check(root):
    for name,sha in SOURCES.items():
        f=root/name
        if name.endswith("/gpi.c"):
            upstream=f.with_name("gpi.tg-original.c")
            if not upstream.is_file() or blob(upstream.read_bytes())!=sha:
                raise RuntimeError("P446AC original TG gpi.c blob drift")
            if not f.is_file() or f.read_bytes()!=adapt_gpi_510(upstream.read_bytes()):
                raise RuntimeError("P446AC GPI 5.10 MMIO API port drift")
        elif not f.is_file() or blob(f.read_bytes())!=sha:
            raise RuntimeError("P446AC GPI pinned blob missing or changed: "+name)
    if (root/"include/linux/ipc_logging.h").read_text()!=IPC_SHIM:
        raise RuntimeError("P446AC IPC-only compatibility shim missing/drifted")
    mk=(root/"drivers/dma/qcom/Makefile").read_text()
    cfg=(root/"drivers/dma/qcom/Kconfig").read_text()
    if TAG not in mk or "obj-$(CONFIG_QCOM_GPI_DMA) += gpi.o" not in mk:
        raise RuntimeError("P446AC GPI Kbuild path absent")
    if TAG not in cfg or "config QCOM_GPI_DMA" not in cfg:
        raise RuntimeError("P446AC GPI Kconfig path absent")
    print("P446AC GPI PIN PASS TG=6bf351bd GPI DMA, MMIO and msm_gpi.h blob verified")
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    root=a.root
    if not a.check_only:
        staged=[]
        for name,sha in SOURCES.items():
            f=root/name
            if f.exists():raise RuntimeError("P446AC GPI refuses to overwrite "+name)
            with urllib.request.urlopen(BASE+name,timeout=90) as conn:
                b=conn.read()
            if blob(b)!=sha:raise RuntimeError("P446AC upstream SHA drift "+name)
            staged.append((f,b))
        mk=root/"drivers/dma/qcom/Makefile"
        cfg=root/"drivers/dma/qcom/Kconfig"
        m=mk.read_text();c=cfg.read_text()
        if TAG in m or TAG in c or "config QCOM_GPI_DMA" in c:
            raise RuntimeError("P446AC duplicate provider")
        m=m.rstrip()+"\n# "+TAG+"\nobj-$(CONFIG_QCOM_GPI_DMA) += gpi.o\n"
        c=c.rstrip()+"""
# A52_PHASE446AC_TG_GPI_DMA_V1: exact TG GPI DMA provider, built into kernel
config QCOM_GPI_DMA
    tristate "Qualcomm GPI DMA engine (TouchGrass 6bf351bd)"
    depends on ARCH_QCOM || COMPILE_TEST
    depends on OF && DMA_ENGINE
    select DMA_VIRTUAL_CHANNELS
    help
      GSI transport for the QUPv3 Serial Engine with FIFO disabled.
"""
        for f,b in staged:
            f.parent.mkdir(parents=True,exist_ok=True)
            if f.name=="gpi.c":
                f.with_name("gpi.tg-original.c").write_bytes(b)
                b=adapt_gpi_510(b)
            f.write_bytes(b)
        shim=root/"include/linux/ipc_logging.h"
        if shim.exists():raise RuntimeError("P446AC refuses to overwrite existing IPC implementation")
        shim.write_text(IPC_SHIM)
        mk.write_text(m);cfg.write_text(c)
    check(root)
if __name__=="__main__":main()
