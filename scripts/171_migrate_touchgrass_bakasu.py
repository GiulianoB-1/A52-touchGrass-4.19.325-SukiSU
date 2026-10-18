#!/usr/bin/env python3
"""P171: replace P170's working TG4.19 ReSukiSU with pinned BakaSU.

Only invoked *after* the unmodified P170 source preparation and verified build.
Keep manual hooks, kernel SuSFS 1.4.2 core-only, no module auto-unmount,
AutoFDO and Clang22 settings exactly as in the proven TG baseline.
"""
from pathlib import Path
import argparse
import subprocess
import shutil
import sys

PIN = "b2c595ddfa64b685e5a144ad37b7c95f912fb92a"
UPSTREAM = "https://github.com/Baka-SU/BakaSU.git"
SUSFS_API = "1.4.2"

def run(*cmd):
    print("+", *map(str, cmd), flush=True)
    subprocess.run(list(map(str,cmd)), check=True)

def edit_once(path, old, new, description):
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError("%s: expected one exact anchor, got %d: %s" % (description, text.count(old), path))
    path.write_text(text.replace(old,new,1))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel",type=Path,required=True)
    ap.add_argument("--repo",type=Path,required=True)
    args=ap.parse_args()
    root=args.kernel.resolve()
    repo=args.repo.resolve()
    ksu=root/"KernelSU"
    assert (root/"Makefile").is_file()
    assert (root/"fs/susfs.c").is_file(), "P170 SuSFS 1.4.2 source missing"
    assert (root/"include/linux/susfs.h").is_file()
    assert ksu.is_dir() and (ksu/".git").exists(), "P170 source must have installed ReSukiSU"
    assert (root/"drivers/kernelsu").is_symlink()
    assert (root/"drivers/kernelsu").readlink().as_posix()=="../KernelSU/kernel"
    defconfig=root/"arch/arm64/configs/a52xq_defconfig"
    config=defconfig.read_text()
    for needle in (
        "CONFIG_KSU=y", "CONFIG_KSU_MANUAL_HOOK=y",
        "CONFIG_KSU_SUSFS=y",
        "CONFIG_KSU_MANUAL_HOOK_AUTO_SETUID_HOOK=y",
        "CONFIG_KSU_MANUAL_HOOK_AUTO_INITRC_HOOK=y",
        "CONFIG_KSU_MANUAL_HOOK_AUTO_INPUT_HOOK=y"):
        assert needle in config.splitlines(), "Baseline configuration changed: "+needle
    for n in ("SUS_PATH","SUS_MOUNT","SUS_KSTAT","SPOOF_UNAME","ENABLE_LOG",
              "HIDE_KSU_SUSFS_SYMBOLS","SPOOF_CMDLINE_OR_BOOTCONFIG",
              "OPEN_REDIRECT","SUS_MAP"):
        assert "# CONFIG_KSU_SUSFS_"+n+" is not set" in config, "SUSFS feature expected disabled: "+n

    # ReSukiSU gets installed by the original P170 builder. Replace *only*
    # that isolated source checkout; keep original kernel hooks and SuSFS core.
    shutil.rmtree(ksu)
    run("git","init","-q",ksu)
    run("git","-C",ksu,"remote","add","origin",UPSTREAM)
    run("git","-C",ksu,"fetch","--quiet","--depth=1","--filter=blob:none","origin",PIN)
    run("git","-C",ksu,"checkout","--quiet","--detach","FETCH_HEAD")
    actual=subprocess.check_output(["git","-C",str(ksu),"rev-parse","HEAD"],text=True).strip()
    assert actual==PIN, "BakaSU identity mismatch"
    assert (ksu/"kernel/Kbuild").is_file(), "Pinned BakaSU Kbuild missing"

    # Reuse the exact core-only Linux 4.19 / SuSFS 1.4.2 adaptation already
    # proven on P170. This code verifies exact upstream anchor shapes, failing
    # closed on drift. Normalize the one new upstream work_pending guard first.
    um=ksu/"kernel/feature/kernel_umount.c"
    edit_once(um,
        "    if (!work_pending(&susfs_extra_works))\n        schedule_work(&susfs_extra_works);\n",
        "    schedule_work(&susfs_extra_works);\n",
        "BakaSU updated SuSFS extra work")
    compat_py = (repo/"scripts/56_patch_resukisu_susfs_manual_core.py").read_text()
    begin="compatibility = r\"\"\"\npython3 - \"$KERNEL_DIR\" \"$RESUKISU_DIR\" <<'MANUALSUSFSCOMPATPY'\n"
    end="\nMANUALSUSFSCOMPATPY\n\"\"\""
    assert compat_py.count(begin)==1 and compat_py.count(end)==1
    compat=compat_py.split(begin,1)[1].split(end,1)[0]
    run(sys.executable,"-c",compat,root,ksu)

    # BakaSU's new manager mark code may query newer SuSFS task state even
    # in the old core-only mode. The manual hook path doesn't need this.
    dispatch=ksu/"kernel/supercall/dispatch.c"
    edit_once(dispatch,
        "#elif defined(CONFIG_KSU_SUSFS)\n        if (susfs_is_current_proc_umounted()) {",
        "#elif defined(CONFIG_KSU_SUSFS) && !defined(CONFIG_KSU_MANUAL_HOOK)\n        if (susfs_is_current_proc_umounted()) {",
        "manager mark manual-hook branch")
    # The inaccessible SuSFS 1.5.x branch can remain in #if 0 at compile
    # time, but use a strict reference count to detect unwanted new callers.
    all_api = "\n".join(p.read_text(errors="replace") for p in (ksu/"kernel").rglob("*.c"))
    assert all_api.count("susfs_is_current_proc_umounted")==1, "unexpected modern SUSFS runtime caller"

    # Preserve P170 root/mount safety: do not let manager turn kernel
    # module-unmount back on. Allowlist default is independently disabled.
    edit_once(ksu/"kernel/policy/allowlist.c",
        "default_non_root_profile.umount_modules = true;",
        "default_non_root_profile.umount_modules = false;",
        "no auto-unmount by default")
    um_text=um.read_text()
    assert "static bool ksu_kernel_umount_enabled = true;" in um_text
    um_text=um_text.replace("static bool ksu_kernel_umount_enabled = true;",
        "static bool ksu_kernel_umount_enabled = false;",1)
    edit="    ksu_kernel_umount_enabled = enable;\n"
    assert um_text.count(edit)==1
    um_text=um_text.replace(edit,
        "    /* P171 safety: manager cannot enable module auto-unmount. */\n"
        "    ksu_kernel_umount_enabled = false;\n",1)
    um.write_text(um_text)

    # Verify all upstream structure, config and safety invariants again.
    assert (root/"drivers/kernelsu").resolve()==(ksu/"kernel").resolve()
    assert "obj-$(CONFIG_KSU) += kernelsu/" in (root/"drivers/Makefile").read_text()
    assert 'source "drivers/kernelsu/Kconfig"' in (root/"drivers/Kconfig").read_text()
    assert "CONFIG_KSU_MANUAL_HOOK=y" in config
    assert "CONFIG_KSU_SUSFS=y" in config
    assert "ksu_kernel_umount_enabled = enable;" not in um.read_text()
    assert "ksu_kernel_umount_enabled = false;" in um.read_text()
    assert "default_non_root_profile.umount_modules = false;" in (ksu/"kernel/policy/allowlist.c").read_text()
    run("git","-C",ksu,"diff","--check")
    run("git","-C",root,"diff","--check")
    print("P171 PIN="+PIN)
    print("P171 target=TG-4.19.206 P170-PROFILED-v2")
    print("P171 hook=manual, susfs=1.4.2-core-only, auto-unmount=disabled")
    print("P171 replacement source and safety audit PASS")

if __name__=="__main__":
    main()
