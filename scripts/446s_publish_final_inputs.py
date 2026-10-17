#!/usr/bin/env python3
"""Phase446S reproducibility export: final, post-patch source and boot inputs.

The DTB is extracted from the FINAL Android boot header-v2 image, not
guessed from an intermediate arch/arm64/boot/dts path. A separate DTBO
is exported only if present; otherwise its absence is explicit.
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, shutil, subprocess, sys

def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):
            h.update(b)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--build",type=Path,required=True)
    p.add_argument("--boot",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--manifest",type=Path,required=True)
    x=p.parse_args()
    root=x.root.resolve()
    build=x.build.resolve()
    boot=x.boot.resolve()
    out=x.output.resolve()
    out.mkdir(parents=True,exist_ok=True)
    for name,source in (
        ("final.config",build/".config"),
        ("System.map",build/"System.map"),
        ("final-tree-manifest.json",x.manifest.resolve()),
    ):
        if not source.is_file(): raise RuntimeError("446S export missing "+str(source))
        shutil.copyfile(source,out/name)
    for name in ("modules.builtin","modules.order"):
        source=build/name
        if source.is_file():
            shutil.copyfile(source,out/name)
        else:
            (out/(name+".NOT_GENERATED.txt")).write_text(
                name+" not produced by this built-in kernel Image build\n")
    repack=Path(__file__).resolve().parent/"38_repack_a52_p1_boot.py"
    spec=importlib.util.spec_from_file_location("a52_boot_parser",repack)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed=module.parse_boot(boot.read_bytes())
    if not parsed["dtb"]:
        raise RuntimeError("446S boot v2 does not carry a DTB")
    dtc=shutil.which("dtc")
    if not dtc:
        for candidate in (build/"scripts/dtc/dtc",root/"scripts/dtc/dtc"):
            if candidate.is_file():
                dtc=str(candidate)
                break
    if not dtc:raise RuntimeError("446S final DTB export requires dtc")
    dtbs=[("boot-final.dtb",parsed["dtb"])]
    if parsed["recovery_dtbo"]:
        dtbs.append(("boot-recovery.dtbo",parsed["recovery_dtbo"]))
    else:
        (out/"DTBO-NOT-IN-BOOT.txt").write_text(
            "The final boot header v2 has recovery_dtbo_size=0. "
            "An independent Samsung dtbo partition is not provided by this "
            "GKI boot build. Do not label a source-tree DTBO as flashed.\n")
    for name,blob in dtbs:
        pth=out/name
        pth.write_bytes(blob)
        res=subprocess.run([dtc,"-f","-I","dtb","-O","dts",
                            "-o",str(pth.with_suffix(".dts")),str(pth)],
                            text=True,capture_output=True)
        if res.returncode:
            raise RuntimeError("dtc rejected "+name+": "+res.stderr[-800:])
    # Retain every candidate DTB/DTBO produced by this kernel compilation,
    # with explicit labeling as build outputs, NOT the bootloader DTBO partition.
    generated=out/"compiled-device-trees"
    dts_root=build/"arch/arm64/boot/dts"
    if dts_root.is_dir():
        for f in sorted(dts_root.rglob("*")):
            if not f.is_file() or f.suffix not in (".dtb",".dtbo"):
                continue
            dst=generated/f.relative_to(dts_root)
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(f,dst)
            res=subprocess.run([dtc,"-f","-I","dtb","-O","dts",
                "-o",str(dst.with_suffix(".dts")),str(dst)],
                text=True,capture_output=True)
            if res.returncode:
                raise RuntimeError("dtc failed compiled tree: "+str(f))
    # A complete final patched tree, not merely original Git head + patches.
    # Kernel build outputs reside in external O= workspace and are not archived.
    source_tar=out/"final-patched-gki-source.tar.zst"
    tar_cmd=["tar","-C",str(root),"--exclude=./.git",
             "--exclude=*.o","--exclude=*.cmd",
             "--exclude=*.ko","--exclude=*.a",
             "--use-compress-program=zstd -3 -T0",
             "-cf",str(source_tar),"."]
    subprocess.run(tar_cmd,check=True)
    files=[p for p in sorted(out.rglob("*")) if p.is_file()]
    hashes={str(p.relative_to(out)):{"bytes":p.stat().st_size,"sha256":sha(p)}
            for p in files}
    info={
        "phase":"446S", "schema":"a52-final-build-inputs-v1",
        "boot_image_sha256":sha(boot),
        "pinned_gki_git_head":subprocess.check_output(
            ["git","-C",str(root),"rev-parse","HEAD"],text=True).strip(),
        "boot_embedded_dtb_bytes":len(parsed["dtb"]),
        "boot_embedded_recovery_dtbo_bytes":len(parsed["recovery_dtbo"]),
        "files":hashes,
    }
    (out/"FINAL-BUILD-INPUTS.json").write_text(json.dumps(info,indent=2,sort_keys=True)+"\n")
    print("P446S BUILD_INPUTS PASS files=%d source_bytes=%d dtb_bytes=%d dtbo_bytes=%d"%(
        len(hashes),source_tar.stat().st_size,
        len(parsed["dtb"]),len(parsed["recovery_dtbo"])),flush=True)
    for f in sorted(out.iterdir()):
        print("P446S BUILD_INPUTS ARTIFACT",f.name,flush=True)

if __name__=="__main__":
    main()
