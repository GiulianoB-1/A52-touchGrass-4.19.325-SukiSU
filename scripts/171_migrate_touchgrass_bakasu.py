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

PIN = "239e1e8871b8fcd51a6e5b3002e0ba522fdd99fb"
TAG = "v4.2.0-rc3"
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
    run("git","-C",ksu,"fetch","--quiet","--depth=1","origin",
        "refs/tags/"+TAG+":refs/tags/"+TAG)
    run("git","-C",ksu,"checkout","--quiet","--detach","refs/tags/"+TAG)
    assert subprocess.check_output(["git","-C",str(ksu),"describe","--exact-match","--tags"],text=True).strip()==TAG
    actual=subprocess.check_output(["git","-C",str(ksu),"rev-parse","HEAD"],text=True).strip()
    assert actual==PIN, "BakaSU identity mismatch"
    assert (ksu/"kernel/Kbuild").is_file(), "Pinned BakaSU Kbuild missing"
    # This BakaSU repository release predates its branding switch. Retain
    # the original code/version count but label this deliberate kernel fork.
    edit_once(ksu/"kernel/Kbuild", "REPO_NAME := ReSukiSU",
              "REPO_NAME := BakaSU", "BakaSU source branding at pinned tag")

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
    # BakaSU's manage-mark query has a SuSFS 1.5.x state accessor absent
    # from P170's core-only 1.4.2. Drop only that optional query path.
    # Our manual hooks have no task mark to expose; preserve default = 0.
    dispatch=ksu/"kernel/supercall/dispatch.c"
    d=dispatch.read_text()
    mark_start="#elif defined(CONFIG_KSU_SUSFS)\n        if (susfs_is_current_proc_umounted()) {"
    mark_end="#else\n        cmd.result = 0;\n#endif"
    assert d.count(mark_start)==1, "manage-mark SUSFS anchor changed"
    left=d.index(mark_start)
    right=d.index(mark_end,left)
    dispatch.write_text(d[:left]+d[right:])

    # New BakaSU has an optional zygote_next SuSFS 1.5.x helper. P170
    # runs the manual + core-only SuSFS 1.4.2 combination, so this helper
    # is neither supported nor needed. Keep the real manual setuid hooks
    # and all normal root/zygote handling intact.
    setuid=ksu/"kernel/hook/setuid_hook.c"
    z=setuid.read_text()
    z_begin="#ifdef CONFIG_KSU_SUSFS\nextern struct work_struct susfs_extra_works;\n"
    z_end="#endif\n\nint ksu_handle_setuid(uid_t new_uid, uid_t old_uid)"
    assert z.count(z_begin)==1 and z.count(z_end)==1, "BakaSU zygote_next helper shape changed"
    first=z.index(z_begin)
    last=z.index(z_end,first)
    z=z[:first]+z[last+len("#endif\n\n"):]
    z_call=("#ifdef CONFIG_KSU_SUSFS\n"
            "    // Only susfs will process zygote_next\n"
            "    // We don't care it in Tracepoint / Manual hook\n\n"
            "    if (is_zygote_next(current_cred())) {\n"
            "        handle_zygote_next_setresuid(new_uid);\n"
            "    }\n"
            "#endif\n")
    assert z.count(z_call)==1, "BakaSU optional zygote_next invocation changed"
    z=z.replace(z_call,"",1)
    setuid.write_text(z)
    assert "handle_zygote_next_setresuid" not in z
    assert "int ksu_handle_setuid(uid_t new_uid, uid_t old_uid)" in z
    assert "ksu_handle_umount(old_uid, new_uid);" in z

    run(sys.executable,"-c",compat,root,ksu)

    # BakaSU b2c595dd has a deliberate unconditional $(error) in its
    # tools/susfs_compat.mk. P170's ReSukiSU adaptation inserted the include
    # into manual-hook Kbuild; it is invalid for this pinned BakaSU version.
    # On our hybrid config the SuSFS *feature sub-options* are all disabled;
    # legacy 1.4.2 core is supplied by the TG tree and manually adapted above.
    # Do not change upstream's error guard or affect BakaSU's standalone
    # SuSFS inline-hook branch. Remove only the injected manual-hook include.
    kbuild = ksu/"kernel/Kbuild"
    ktext = kbuild.read_text()
    include_block = (
        "  ifdef CONFIG_KSU_SUSFS\n"
        "    include $(KSU_SRC)/tools/susfs_compat.mk\n"
        "  endif\n"
    )
    assert ktext.count(include_block)==1, "P171 unexpected manual-hook SuSFS include placement"
    assert ktext.count("else ifdef CONFIG_KSU_MANUAL_HOOK")==1
    assert "include $(KSU_SRC)/tools/manual_hook_check.mk" in ktext
    assert "else ifdef CONFIG_KSU_SUSFS" in ktext
    kbuild.write_text(ktext.replace(include_block, "", 1))
    assert "using Manual Hook" in kbuild.read_text()
    assert kbuild.read_text().count("include $(KSU_SRC)/tools/susfs_compat.mk")==1, (
        "upstream SuSFS-inline branch must stay intact"
    )

    # Port the three actual SuSFS process-state helpers used by BakaSU's
    # sucompat.h: per-task/thread flag 34. This follows the implementation
    # in the Linux 4.14+ SuSFS patch; never replace the hooks with no-ops.
    # ARM64 TG4.19 thread_info.flags is unsigned long (64 bits).
    import re
    ti = root / "arch/arm64/include/asm/thread_info.h"
    ti_text = ti.read_text()
    if not re.search(r"struct thread_info\s*\{\s*unsigned long\s+flags\s*;", ti_text):
        raise RuntimeError("P171 ARM64 thread_info.flags layout unexpected")
    candidates = list((root / "arch/arm64/include").rglob("*.h"))
    candidates += list((root / "include/linux").rglob("*.h"))
    candidates += list((ksu / "kernel").rglob("*.h"))
    occupied = []
    for item in candidates:
        body = item.read_text(errors="replace")
        if re.search(r"^\s*#\s*define\s+TIF_[A-Za-z0-9_]+\s+34(?:\s|$)", body, re.M):
            occupied.append(str(item))
    if occupied:
        raise RuntimeError("P171 ARM64 TIF bit34 collision: " + ", ".join(occupied))
    susfs_header = root / "include/linux/susfs.h"
    header = susfs_header.read_text()
    if "TIF_PROC_NO_SU" in header:
        raise RuntimeError("P171 pinned SuSFS header already has no_su state")
    include_anchor = "#include <linux/types.h>\n"
    assert header.count(include_anchor) == 1, "P171 pinned SuSFS include anchor mismatch"
    header = header.replace(include_anchor, include_anchor + "#include <linux/thread_info.h>\n", 1)
    code = """
/* P171: real SuSFS per-thread no-su state for BakaSU manual hooks.
 * Bit 34 is used by upstream's 4.14+ SuSFS implementation and is
 * separately collision-checked against this exact TG4.19 source.
 * It is inherited through task cloning and dies with the task.
 */
#define TIF_PROC_NO_SU 34

static inline bool susfs_is_current_proc_no_su(void)
{
    return !!test_thread_flag(TIF_PROC_NO_SU);
}

static inline void susfs_set_current_proc_no_su(void)
{
    set_thread_flag(TIF_PROC_NO_SU);
}

static inline void susfs_clear_current_proc_no_su(void)
{
    clear_thread_flag(TIF_PROC_NO_SU);
}
"""
    # Header functions require bool, thread-info accessors and TIF defines:
    # insert only after the legacy header's last common include.
    insert_after = "#include <linux/hashtable.h>\n"
    assert header.count(insert_after) == 1, "P171 pinned SuSFS include block mismatch"
    header = header.replace(insert_after, insert_after + code + "\n", 1)
    susfs_header.write_text(header)
    for fn in ("susfs_is_current_proc_no_su", "susfs_set_current_proc_no_su",
               "susfs_clear_current_proc_no_su"):
        assert "static inline " in header and fn+"(void)" in header, fn
    # Check the actual expected BakaSU mapping uses these helper names, rather
    # than relying on a lucky link or silently rewriting privilege semantics.
    sucompat_header = (ksu / "kernel/feature/sucompat.h").read_text()
    for fn in ("susfs_is_current_proc_no_su", "susfs_set_current_proc_no_su",
               "susfs_clear_current_proc_no_su"):
        assert fn in sucompat_header, "P171 expected BakaSU no-su macro missing: "+fn

    # CRASH FIX 2026-10-19: TG fs/open.c and fs/stat.c pass an
    # (address-of) const char __user*; BakaSU's CONFIG_KSU_SUSFS branch
    # instead treats it as struct filename** and dereferences userspace
    # under PAN (ksu_handle_faccessat+0x60 at 1.431s). Manual hooks MUST
    # take precedence over SUSFS in both definition and declaration.
    cfile = ksu/"kernel/feature/sucompat.c"
    hfile = ksu/"kernel/feature/sucompat.h"
    for src in (cfile, hfile):
        body=src.read_text()
        # .c implements two separately guarded functions; .h groups both
        # prototypes under a single #ifdef preceding faccessat.
        funcs = ("faccessat", "stat") if src == cfile else ("faccessat",)
        for func in funcs:
            old = ("#ifdef CONFIG_KSU_SUSFS\n"
                   "int ksu_handle_"+func+"(int *dfd, struct filename **filename,")
            new = ("#if defined(CONFIG_KSU_SUSFS) && !defined(CONFIG_KSU_MANUAL_HOOK)\n"
                   "int ksu_handle_"+func+"(int *dfd, struct filename **filename,")
            if body.count(old)!=1:
                raise RuntimeError("P171 non-GKI hook ABI guard changed: "+str(src)+" "+func)
            body=body.replace(old,new,1)
        if src == hfile:
            start = body.index("#if defined(CONFIG_KSU_SUSFS) && !defined(CONFIG_KSU_MANUAL_HOOK)\n"
                               "int ksu_handle_faccessat(")
            stop = body.index("#else\nint ksu_handle_faccessat(",start)
            assert ("int ksu_handle_stat(int *dfd, struct filename **filename,") in body[start:stop], (
                "P171 grouped stat prototype moved outside guarded branch")
        src.write_text(body)
        for func in ("faccessat","stat"):
            assert ("int ksu_handle_"+func+"(int *dfd, const char __user **filename_user,") in body, (
                "P171 missing user-pointer manual handler: "+func)
    for func in ("faccessat","stat"):
        assert ("#if defined(CONFIG_KSU_SUSFS) && !defined(CONFIG_KSU_MANUAL_HOOK)\n"
                "int ksu_handle_"+func) in cfile.read_text()
    print("P171 faccessat/stat manual-hook ABI overrides incompatible SUSFS structs: PASS")

    all_api = "\n".join(p.read_text(errors="replace") for p in (ksu/"kernel").rglob("*.c"))
    assert "susfs_is_current_proc_umounted" not in all_api, "modern SuSFS runtime caller survived"

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
