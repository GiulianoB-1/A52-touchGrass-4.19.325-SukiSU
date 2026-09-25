#!/usr/bin/env python3
from pathlib import Path
import sys

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {count}")
    path.write_text(text.replace(old, new, 1))
    print(f"{path}: applied {label}")

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    exec_c = root / "fs/exec.c"
    elf_c = root / "fs/binfmt_elf.c"
    ram_c = root / "fs/pstore/ram.c"
    for p in (exec_c, elf_c, ram_c):
        if not p.is_file():
            raise SystemExit(f"missing source file: {p}")

    # Phase 379 wants current-boot console from t=0. The recovery collector is
    # responsible for preserving the failed boot before its own ramoops init.
    replace_once(
        ram_c,
        "#define A52_RAMOOPS_BOOT_HOLD_NS\t(5ULL * NSEC_PER_SEC)\n",
        "#define A52_RAMOOPS_BOOT_HOLD_NS\t0ULL /* A52 P379: capture current 16K boot from t=0 */\n",
        "disable current-boot ramoops hold",
    )

    # Add a delayed OOPS-class kmsg snapshot. This stores the early boot printk
    # ring in the crash-record zone even when the continuous console later wraps.
    replace_once(
        ram_c,
        "#include <linux/of_address.h>\n#include <linux/ktime.h>\n",
        "#include <linux/of_address.h>\n#include <linux/ktime.h>\n"
        "#include <linux/kmsg_dump.h>\n#include <linux/workqueue.h>\n",
        "P379 snapshot includes",
    )

    marker = """static atomic_t a52_pmsg_zone_released = ATOMIC_INIT(0);

static bool a52_ramoops_boot_hold_active(void)
"""
    inject = """static atomic_t a52_pmsg_zone_released = ATOMIC_INIT(0);

/*
 * A52 P379: preserve an early 16K boot census in the dmesg crash zone.
 * The continuous console starts immediately in this diagnostic phase, while
 * this delayed snapshot captures the printk ring before later boot noise can
 * wrap it.
 */
static void a52_p379_early_snapshot_workfn(struct work_struct *work)
{
	pr_emerg("A52 P379 SNAPSHOT: persisting early 16K boot printk ring at ~12s\\n");
	kmsg_dump(KMSG_DUMP_OOPS);
}

static DECLARE_DELAYED_WORK(a52_p379_early_snapshot_work,
			    a52_p379_early_snapshot_workfn);

static bool a52_ramoops_boot_hold_active(void)
"""
    replace_once(ram_c, marker, inject, "P379 delayed kmsg snapshot")

    replace_once(
        ram_c,
        '''\tpr_info("attached 0x%lx@0x%llx, ecc: %d/%d\\n",
\t\tcxt->size, (unsigned long long)cxt->phys_addr,
\t\tcxt->ecc_info.ecc_size, cxt->ecc_info.block_size);

\treturn 0;
''',
        '''\tpr_info("attached 0x%lx@0x%llx, ecc: %d/%d\\n",
\t\tcxt->size, (unsigned long long)cxt->phys_addr,
\t\tcxt->ecc_info.ecc_size, cxt->ecc_info.block_size);

\tpr_emerg("A52 P379 GEOM: PAGE_SIZE=%lu PAGE_SHIFT=%d VA_BITS=%d\\n",
\t\t(unsigned long)PAGE_SIZE, PAGE_SHIFT, VA_BITS);
\tschedule_delayed_work(&a52_p379_early_snapshot_work,
\t\t\t      msecs_to_jiffies(12000));

\treturn 0;
''',
        "P379 geometry marker and snapshot scheduling",
    )

    # Trace every exec request and final kernel execve return. This catches
    # failures that happen before binfmt dispatch as well as normal successes.
    replace_once(
        exec_c,
        '''\tchar *pathbuf = NULL;
\tstruct linux_binprm *bprm;
\tstruct files_struct *displaced;
\tint retval;

\tif (IS_ERR(filename))
''',
        '''\tchar *pathbuf = NULL;
\tstruct linux_binprm *bprm;
\tstruct files_struct *displaced;
\tint retval;
\tconst char *a52_p379_name = "<file>";

\tif (!IS_ERR_OR_NULL(filename))
\t\ta52_p379_name = filename->name;

\tpr_info_once("A52 P379 EXEC_GEOM: PAGE_SIZE=%lu PAGE_SHIFT=%d\\n",
\t\t     (unsigned long)PAGE_SIZE, PAGE_SHIFT);
\tpr_info("A52 P379 EXEC_REQ: pid=%d comm=%s fd=%d flags=0x%x file=%s\\n",
\t\tcurrent->pid, current->comm, fd, flags, a52_p379_name);

\tif (IS_ERR(filename))
''',
        "P379 exec request trace",
    )

    replace_once(
        exec_c,
        '''\tif (displaced)
\t\tput_files_struct(displaced);
\treturn retval;

out:
''',
        '''\tif (displaced)
\t\tput_files_struct(displaced);
\tpr_info("A52 P379 EXEC_OK: pid=%d comm=%s file=%s rc=%d\\n",
\t\tcurrent->pid, current->comm, a52_p379_name, retval);
\treturn retval;

out:
''',
        "P379 exec success trace",
    )

    replace_once(
        exec_c,
        '''out_ret:
\tif (filename)
\t\tputname(filename);
\treturn retval;
}
''',
        '''out_ret:
\tpr_err("A52 P379 EXEC_FAIL: pid=%d comm=%s file=%s rc=%d\\n",
\t\tcurrent->pid, current->comm, a52_p379_name, retval);
\tif (filename && !IS_ERR(filename))
\t\tputname(filename);
\treturn retval;
}
''',
        "P379 exec failure trace",
    )

    # Trace only genuine ELF candidates in the ELF loader. This distinguishes
    # a kernel ELF-layout rejection from a program that execs successfully and
    # later dies inside the dynamic linker/userspace.
    replace_once(
        elf_c,
        '''\tstruct arch_elf_state arch_state = INIT_ARCH_ELF_STATE;
\tloff_t pos;

\tloc = kmalloc(sizeof(*loc), GFP_KERNEL);
''',
        '''\tstruct arch_elf_state arch_state = INIT_ARCH_ELF_STATE;
\tloff_t pos;
\tbool a52_p379_is_elf = !memcmp(bprm->buf, ELFMAG, SELFMAG);

\tif (a52_p379_is_elf)
\t\tpr_info("A52 P379 ELF_ENTER: pid=%d comm=%s file=%s PAGE_SIZE=%lu\\n",
\t\t\tcurrent->pid, current->comm,
\t\t\tbprm->filename ? bprm->filename : "<null>",
\t\t\t(unsigned long)PAGE_SIZE);

\tloc = kmalloc(sizeof(*loc), GFP_KERNEL);
''',
        "P379 ELF entry trace",
    )

    replace_once(
        elf_c,
        '''out_ret:
\treturn retval;

\t/* error cleanup */
''',
        '''out_ret:
\tif (a52_p379_is_elf) {
\t\tif (retval)
\t\t\tpr_err("A52 P379 ELF_FAIL: pid=%d comm=%s file=%s rc=%d\\n",
\t\t\t\tcurrent->pid, current->comm,
\t\t\t\tbprm->filename ? bprm->filename : "<null>", retval);
\t\telse
\t\t\tpr_info("A52 P379 ELF_OK: pid=%d comm=%s file=%s\\n",
\t\t\t\tcurrent->pid, current->comm,
\t\t\t\tbprm->filename ? bprm->filename : "<null>");
\t}
\treturn retval;

\t/* error cleanup */
''',
        "P379 ELF result trace",
    )

    for path in (exec_c, elf_c, ram_c):
        text = path.read_text()
        if "A52 P379" not in text:
            raise SystemExit(f"{path}: P379 marker missing")

    print("A52 Phase379 16K boot census diagnostics applied successfully")

if __name__ == "__main__":
    main()
