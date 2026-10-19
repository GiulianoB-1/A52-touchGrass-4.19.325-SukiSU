#!/usr/bin/env python3
"""P171 stop-before-compile ABI contract for TG 4.19 + BakaSU manual hooks.

A prior flash panicked at 1.431s because ksu_handle_faccessat() dereferenced
an arm64 user address as struct filename * under PAN.
This audit checks the exact source callsites, declarations, source pin,
and preprocessor selection prior to compiling a flashable kernel.
"""
from pathlib import Path
import argparse
import re
import subprocess
import sys

PIN = "239e1e8871b8fcd51a6e5b3002e0ba522fdd99fb"
TAG = "v4.2.0-rc3"

def require(cond,why):
    if not cond: raise SystemExit("P171 HOOK ABI FAIL: " + why)

def line(path,needle):
    L=path.read_text().splitlines()
    matches=[(i+1,s.strip()) for i,s in enumerate(L) if needle in s]
    require(bool(matches),f"{path}: missing {needle}")
    for n,s in matches[:7]:
        print(f"{path}:{n}: {s}")
    return L

def verify(src):
    ksu=src/"KernelSU"
    rev=subprocess.check_output(["git","-C",str(ksu),"rev-parse","HEAD"],text=True).strip()
    require(rev==PIN,"BakaSU revision "+rev+" not "+PIN)
    tag=subprocess.check_output(["git","-C",str(ksu),"describe","--exact-match","--tags"],text=True).strip()
    require(tag==TAG,"BakaSU tag "+tag+" not "+TAG)
    cfg=(src/"arch/arm64/configs/a52xq_defconfig").read_text().splitlines()
    for symbol in ("CONFIG_KSU=y","CONFIG_KSU_MANUAL_HOOK=y",
                   "CONFIG_KSU_SUSFS=y","CONFIG_KSU_MANUAL_HOOK_AUTO_SETUID_HOOK=y",
                   "CONFIG_KSU_MANUAL_HOOK_AUTO_INITRC_HOOK=y",
                   "CONFIG_KSU_MANUAL_HOOK_AUTO_INPUT_HOOK=y"):
        require(symbol in cfg, "wrong config "+symbol)
    cc=ksu/"kernel/feature/sucompat.c"
    hh=ksu/"kernel/feature/sucompat.h"
    guard="#if defined(CONFIG_KSU_SUSFS) && !defined(CONFIG_KSU_MANUAL_HOOK)\n"
    for func in ("faccessat","stat"):
        legacy=f"int ksu_handle_{func}(int *dfd, struct filename **filename,"
        manual=f"int ksu_handle_{func}(int *dfd, const char __user **filename_user,"
        for f in (cc,hh):
            txt=f.read_text()
            require(txt.count(guard+legacy)==1,
                    f"{f}: wrong struct-filename guard for {func}")
            require(txt.count(manual)==1,
                    f"{f}: manual user-pointer declaration missing for {func}")
            require(txt.index(guard+legacy)<txt.index(manual),
                    f"{f}: manual prototype not in other conditional branch")
            line(f,manual)

    for rel,decl,call in [
        ("fs/open.c",
         "extern int ksu_handle_faccessat(int *dfd, const char __user **filename_user,",
         "ksu_handle_faccessat(&dfd, &filename, &mode, NULL);"),
        ("fs/stat.c",
         "extern int ksu_handle_stat(int *dfd, const char __user **filename_user,",
         "ksu_handle_stat(&dfd, &filename, &flag);"),
        ("fs/exec.c",
         "extern int ksu_handle_execveat(int *fd, struct filename **filename_ptr,",
         "ksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0);"),
        ("fs/read_write.c",
         "extern int ksu_handle_sys_read(unsigned int fd, char __user **buf_ptr,",
         "ksu_handle_sys_read(fd, &buf, &count);"),
        ("drivers/input/input.c",
         "extern int ksu_handle_input_handle_event(unsigned int *type, unsigned int *code, int *value);",
         "ksu_handle_input_handle_event(&type, &code, &value);"),
    ]:
        p=src/rel
        s=p.read_text()
        require(decl in s, f"{rel}: expected original TG hook declaration changed")
        require(call in s, f"{rel}: expected original TG hook call changed")
        line(p,decl)
        line(p,call)

    actual_exec=ksu/"kernel/feature/sucompat.c"
    require("int ksu_handle_execveat(int *fd, struct filename **filename_ptr," in actual_exec.read_text(),
            "BakaSU execveat expects different filename pointer")
    require("ksu_strncpy_from_user_nofault(path, *filename_user, sizeof(path));" in cc.read_text(),
            "BakaSU manual hook does not safely copy user filenames")

    # Print all ksu_handle_* callsites in the known entrypoints for human review.
    print("\n=== ALL TG KSU hook callsites (source:line) ===")
    for rel in ("fs/open.c","fs/stat.c","fs/exec.c","fs/read_write.c",
                "drivers/input/input.c"):
        f=src/rel
        for i,s in enumerate(f.read_text().splitlines(),1):
            if re.search(r"\bksu_handle_[a-zA-Z0-9_]+\s*\(",s) and not re.search(
                r"\bextern\b",s):
                print(f"{rel}:{i}: {s.strip()}")
    print("P171 HOOK ABI PASS: raw user pointers kept as user pointers; "
          "manual faccessat/stat branch takes precedence over SUSFS.")
    print(f"PIN={PIN} TAG={TAG}")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("kernel",type=Path)
    a=ap.parse_args()
    verify(a.kernel.resolve())
