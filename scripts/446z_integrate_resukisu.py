#!/usr/bin/env python3
"""P446Z pinned ReSukiSU successor (BakaSU) built-in tracepoint integration.

Fetch upstream separately at the pinned sha before invoking this script:
git clone --filter=blob:none https://github.com/Baka-SU/BakaSU ROOT/KernelSU
git -C ROOT/KernelSU checkout --detach PIN

Mirrors the upstream kernel/setup.sh integration: drivers/kernelsu symlink,
drivers/Makefile and drivers/Kconfig entries. No manual VFS hooks, no SuSFS.
The complete integration is removable by excluding this script and its CI call.
"""
from pathlib import Path
import argparse
import subprocess

TAG="A52_PHASE446Z_RESUKISU_BUILTIN_TRACEPOINT_V1"
PIN="b2c595ddfa64b685e5a144ad37b7c95f912fb92a"
MAKE=Path("drivers/Makefile")
KCFG=Path("drivers/Kconfig")
INIT=Path("KernelSU/kernel/core/init.c")

def check(root):
    if (root/"KernelSU").is_symlink():
        raise RuntimeError("P446Z KSU refuses symlink at source root")
    got=subprocess.check_output(["git","-C",str(root/"KernelSU"),"rev-parse","HEAD"],text=True).strip()
    if got!=PIN:raise RuntimeError(f"P446Z KSU upstream SHA drift: {got}")
    symlink=root/"drivers/kernelsu"
    if not symlink.is_symlink() or symlink.readlink().as_posix()!="../KernelSU/kernel":
        raise RuntimeError("P446Z KSU upstream setup symlink missing")
    for rel,words in ((MAKE,("obj-$(CONFIG_KSU) += kernelsu/",TAG)),
                      (KCFG,('source "drivers/kernelsu/Kconfig"',TAG)),
                      (INIT,("P446Z KSU version=%s hooks=tracepoint rc=0","P446Z KSU version=%s hooks=tracepoint rc=-12"))):
        data=(root/rel).read_text()
        for w in words:
            if w not in data:raise RuntimeError(f"P446Z KSU missing {w} in {rel}")
    if not (root/"KernelSU/kernel/Kbuild").is_file():
        raise RuntimeError("P446Z KSU missing pinned upstream Kbuild")
    for rel in ("fs/exec.c","fs/open.c","fs/stat.c","fs/read_write.c"):
        t=(root/rel).read_text()
        for forbidden in ("ksu_handle_execveat(", "ksu_handle_faccessat(",
                          "ksu_handle_stat(", "ksu_handle_sys_read("):
            if forbidden in t:
                raise RuntimeError(f"P446Z KSU manual-hook collision in {rel}: {forbidden}")
    print("P446Z KSU AUDIT PASS: pinned BakaSU upstream, builtin tracepoint hooks, no legacy VFS hooks, setup.sh-equivalent tree wiring")

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--root",type=Path,required=True)
    a.add_argument("--check-only",action="store_true")
    z=a.parse_args()
    root=z.root.resolve()
    if not z.check_only:
        if (root/"drivers/kernelsu").exists() or (root/"drivers/kernelsu").is_symlink():
            raise RuntimeError("P446Z KSU refuses existing driver path or duplicate install")
        upstream=root/"KernelSU"
        if not upstream.is_dir():
            raise RuntimeError("P446Z KSU pinned upstream source not fetched")
        got=subprocess.check_output(["git","-C",str(upstream),"rev-parse","HEAD"],text=True).strip()
        if got!=PIN:raise RuntimeError("P446Z KSU SHA mismatch")
        for rel in ("fs/exec.c","fs/open.c","fs/stat.c","fs/read_write.c","drivers/input/input.c"):
            if any(m in (root/rel).read_text() for m in ("ksu_handle_execveat(","ksu_handle_faccessat(",
                       "ksu_handle_stat(","ksu_handle_sys_read(","ksu_handle_input_handle_event(")):
                raise RuntimeError("P446Z refuses conflict with prior manual KSU patches in "+rel)
        mk=(root/MAKE).read_text()
        kcfg=(root/KCFG).read_text()
        if "obj-$(CONFIG_KSU)" in mk or 'source "drivers/kernelsu/Kconfig"' in kcfg:
            raise RuntimeError("P446Z refuses duplicate KSU host entries")
        init=(root/INIT).read_text()
        anchor='    ksu_cred = prepare_creds();\n    if (!ksu_cred) {\n        pr_err("prepare cred failed!\\n");\n        return -ENOSYS;\n    }'
        if init.count(anchor)!=1:
            raise RuntimeError("P446Z pinned KSU init error branch changed")
        init=init.replace(anchor,
          '    ksu_cred = prepare_creds();\n    if (!ksu_cred) {\n'
          '        pr_err("P446Z KSU version=%s hooks=tracepoint rc=-12 prepare_creds\\n", KSU_VERSION_FULL);\n'
          '        pr_err("prepare cred failed!\\n");\n        return -ENOSYS;\n    }',1)
        anchor2='    return 0;\n}\n\nvoid __exit kernelsu_exit(void)'
        if init.count(anchor2)!=1:raise RuntimeError("P446Z KSU init success anchor changed")
        init=init.replace(anchor2,
          '    pr_info("P446Z KSU version=%s hooks=tracepoint rc=0 builtin=1\\n", KSU_VERSION_FULL);\n'
          '    return 0;\n}\n\nvoid __exit kernelsu_exit(void)',1)
        (root/INIT).write_text(init)
        (root/MAKE).write_text(mk.rstrip()+f"\n\n# {TAG}\nobj-$(CONFIG_KSU) += kernelsu/\n")
        last=kcfg.rfind("\nendmenu")
        if last<0:raise RuntimeError("P446Z KSU no drivers Kconfig menu")
        kcfg=kcfg[:last]+f'\n# {TAG}\nsource "drivers/kernelsu/Kconfig"\n'+kcfg[last:]
        (root/KCFG).write_text(kcfg)
        (root/"drivers/kernelsu").symlink_to("../KernelSU/kernel")
    check(root)
if __name__=="__main__":main()
