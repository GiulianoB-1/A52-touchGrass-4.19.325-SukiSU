#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def sha(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def choose(root: Path, rels: list[str], name: str) -> Path | None:
    for rel in rels:
        p = root / rel
        if p.is_file():
            return p
    hits = sorted((p for p in root.rglob(name) if p.is_file()), key=lambda p: (len(p.parts), str(p)))
    return hits[0] if hits else None


def rel(root: Path, p: Path | None) -> str | None:
    if p is None:
        return None
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


PROBE = r'''
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#define __user
#include "{header}"
#define S(x) printf("SIZE %-32s %zu\\n", #x, sizeof(struct x))
#define O(x,f) printf("OFF  %-32s %-18s %zu\\n", #x, #f, offsetof(struct x, f))
#define I(x) printf("IOC  %-32s 0x%lx\\n", #x, (unsigned long)(x))
int main(void) {{
 S(kgsl_device_getproperty); O(kgsl_device_getproperty,type); O(kgsl_device_getproperty,value); O(kgsl_device_getproperty,sizebytes);
 S(kgsl_drawctxt_create); O(kgsl_drawctxt_create,flags); O(kgsl_drawctxt_create,drawctxt_id);
 S(kgsl_map_user_mem); O(kgsl_map_user_mem,fd); O(kgsl_map_user_mem,gpuaddr); O(kgsl_map_user_mem,len); O(kgsl_map_user_mem,offset); O(kgsl_map_user_mem,hostptr); O(kgsl_map_user_mem,memtype); O(kgsl_map_user_mem,flags);
 S(kgsl_gpumem_alloc_id); O(kgsl_gpumem_alloc_id,id); O(kgsl_gpumem_alloc_id,flags); O(kgsl_gpumem_alloc_id,size); O(kgsl_gpumem_alloc_id,mmapsize); O(kgsl_gpumem_alloc_id,gpuaddr);
 S(kgsl_gpuobj_alloc); O(kgsl_gpuobj_alloc,size); O(kgsl_gpuobj_alloc,flags); O(kgsl_gpuobj_alloc,va_len); O(kgsl_gpuobj_alloc,mmapsize); O(kgsl_gpuobj_alloc,id); O(kgsl_gpuobj_alloc,metadata_len); O(kgsl_gpuobj_alloc,metadata);
 S(kgsl_gpuobj_info); O(kgsl_gpuobj_info,gpuaddr); O(kgsl_gpuobj_info,flags); O(kgsl_gpuobj_info,size); O(kgsl_gpuobj_info,va_len); O(kgsl_gpuobj_info,va_addr); O(kgsl_gpuobj_info,id);
 S(kgsl_gpuobj_import); O(kgsl_gpuobj_import,priv); O(kgsl_gpuobj_import,priv_len); O(kgsl_gpuobj_import,flags); O(kgsl_gpuobj_import,type); O(kgsl_gpuobj_import,id);
 S(kgsl_gpu_command); O(kgsl_gpu_command,flags); O(kgsl_gpu_command,cmdlist); O(kgsl_gpu_command,cmdsize); O(kgsl_gpu_command,numcmds); O(kgsl_gpu_command,objlist); O(kgsl_gpu_command,objsize); O(kgsl_gpu_command,numobjs); O(kgsl_gpu_command,synclist); O(kgsl_gpu_command,syncsize); O(kgsl_gpu_command,numsyncs); O(kgsl_gpu_command,context_id); O(kgsl_gpu_command,timestamp);
 I(IOCTL_KGSL_DEVICE_GETPROPERTY); I(IOCTL_KGSL_DRAWCTXT_CREATE); I(IOCTL_KGSL_MAP_USER_MEM); I(IOCTL_KGSL_GPUMEM_ALLOC_ID); I(IOCTL_KGSL_GPUMEM_GET_INFO); I(IOCTL_KGSL_GPUOBJ_ALLOC); I(IOCTL_KGSL_GPUOBJ_INFO); I(IOCTL_KGSL_GPUOBJ_IMPORT); I(IOCTL_KGSL_GPUOBJ_SYNC); I(IOCTL_KGSL_GPU_COMMAND);
 return 0;
}}
'''


def probe(root: Path, header: Path, out_dir: Path, tag: str) -> dict:
    src = out_dir / f"kgsl-layout-{tag}.c"
    exe = out_dir / f"kgsl-layout-{tag}"
    txt = out_dir / f"kgsl-layout-{tag}.txt"
    err = out_dir / f"kgsl-layout-{tag}.stderr.txt"
    src.write_text(PROBE.format(header=str(header.resolve()).replace('\\', '\\\\')))
    includes = []
    for p in (
        root / "a52-compat/include/uapi",
        root / "a52-compat/include",
        root / "arch/arm64/include/uapi",
        root / "include/uapi",
        root / "arch/arm64/include",
        root / "include",
    ):
        if p.is_dir():
            includes += ["-I", str(p)]
    cmd = [os.environ.get("CC", "gcc"), "-std=gnu11", "-D__user=", "-D__force=", "-D__bitwise=", *includes, str(src), "-o", str(exe)]
    cp = subprocess.run(cmd, text=True, capture_output=True)
    err.write_text(cp.stderr)
    if cp.returncode:
        return {"ok": False, "command": cmd, "returncode": cp.returncode, "stderr": cp.stderr[-8000:]}
    rp = subprocess.run([str(exe)], text=True, capture_output=True)
    txt.write_text(rp.stdout)
    return {"ok": rp.returncode == 0, "output": rp.stdout, "stderr": rp.stderr}


def compat_lines(root: Path) -> list[str]:
    base = root / "drivers/gpu/msm"
    rows: list[str] = []
    if not base.is_dir():
        return rows
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.suffix not in {".c", ".h"}:
            continue
        try:
            lines = p.read_text(errors="replace").splitlines()
        except Exception:
            continue
        for n, line in enumerate(lines, 1):
            if "compat_ioctl" in line or "COMPAT" in line and "ioctl" in line.lower():
                rows.append(f"{p.relative_to(root)}:{n}: {line.strip()}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gki", type=Path, required=True)
    ap.add_argument("--touchgrass", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ns = ap.parse_args()
    gki, tg, out = ns.gki.resolve(), ns.touchgrass.resolve(), ns.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    files = {
        "msm_kgsl": (
            choose(gki, ["a52-compat/include/uapi/linux/msm_kgsl.h", "include/uapi/linux/msm_kgsl.h"], "msm_kgsl.h"),
            choose(tg, ["include/uapi/linux/msm_kgsl.h"], "msm_kgsl.h"),
        ),
        "msm_ion_uapi": (
            choose(gki, ["a52-compat/include/uapi/linux/msm_ion.h", "include/uapi/linux/msm_ion.h"], "msm_ion.h"),
            choose(tg, ["include/uapi/linux/msm_ion.h"], "msm_ion.h"),
        ),
        "msm_ion_kernel": (
            choose(gki, ["a52-compat/include/linux/msm_ion.h", "include/linux/msm_ion.h"], "msm_ion.h"),
            choose(tg, ["include/linux/msm_ion.h"], "msm_ion.h"),
        ),
        "ion_uapi": (
            choose(gki, ["a52-compat/include/uapi/linux/ion.h", "include/uapi/linux/ion.h"], "ion.h"),
            choose(tg, ["include/uapi/linux/ion.h"], "ion.h"),
        ),
        "dma_heap": (
            choose(gki, ["include/uapi/linux/dma-heap.h", "include/uapi/linux/dma_heap.h"], "dma-heap.h"),
            choose(tg, ["include/uapi/linux/dma-heap.h", "include/uapi/linux/dma_heap.h"], "dma-heap.h"),
        ),
    }

    report: dict[str, object] = {"files": {}}
    for name, (gp, tp) in files.items():
        gh, th = sha(gp), sha(tp)
        report["files"][name] = {
            "gki": rel(gki, gp), "touchgrass": rel(tg, tp),
            "gki_sha256": gh, "touchgrass_sha256": th,
            "byte_identical": bool(gh and th and gh == th),
        }

    kg, kt = files["msm_kgsl"]
    if kg and kt:
        report["kgsl_layout_gki"] = probe(gki, kg, out, "gki")
        report["kgsl_layout_touchgrass"] = probe(tg, kt, out, "touchgrass")
        go = report["kgsl_layout_gki"].get("output") if isinstance(report["kgsl_layout_gki"], dict) else None
        to = report["kgsl_layout_touchgrass"].get("output") if isinstance(report["kgsl_layout_touchgrass"], dict) else None
        report["kgsl_layout_identical"] = bool(go and to and go == to)

    gcompat = compat_lines(gki)
    tcompat = compat_lines(tg)
    (out / "compat-ioctl-gki.txt").write_text("\n".join(gcompat) + ("\n" if gcompat else ""))
    (out / "compat-ioctl-touchgrass.txt").write_text("\n".join(tcompat) + ("\n" if tcompat else ""))
    report["compat_ioctl_lines"] = {"gki": gcompat, "touchgrass": tcompat}

    (out / "graphics-uapi-audit.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "msm_kgsl_byte_identical": report["files"]["msm_kgsl"]["byte_identical"],
        "kgsl_layout_identical": report.get("kgsl_layout_identical"),
        "gki_kgsl": report["files"]["msm_kgsl"]["gki"],
        "tg_kgsl": report["files"]["msm_kgsl"]["touchgrass"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
