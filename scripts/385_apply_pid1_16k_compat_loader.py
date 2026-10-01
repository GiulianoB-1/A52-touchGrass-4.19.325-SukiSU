#!/usr/bin/env python3
from pathlib import Path
import sys

MARK = "A52_PHASE385_PID1_16K_COMPAT_LOADER_V1"

def replace_once(path, old, new, label):
    text = path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {n}")
    path.write_text(text.replace(old, new, 1))
    print(f"{path}: applied {label}")

def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root = Path(sys.argv[1]).resolve()
    elf = root / "fs/binfmt_elf.c"
    if not elf.is_file():
        raise SystemExit(f"missing {elf}")

    # Phase383 already adds the dual-recorder header. Add sizes for SZ_4K/SZ_16K.
    replace_once(
        elf,
        "#include <linux/security.h>\n",
        "#include <linux/security.h>\n#include <linux/sizes.h>\n",
        "sizes header",
    )

    helper_anchor = """#define BAD_ADDR(x) ((unsigned long)(x) >= TASK_SIZE)

static int set_brk(unsigned long start, unsigned long end, int prot)
"""
    helper = r'''#define BAD_ADDR(x) ((unsigned long)(x) >= TASK_SIZE)

/*
 * A52_PHASE385_PID1_16K_COMPAT_LOADER_V1
 *
 * The current UN1CA ramdisk /init is a static ET_EXEC whose four PT_LOADs
 * are 4 KiB aligned.  Native 16 KiB loading makes adjacent segments share
 * native pages, including one RX|RW boundary.  Android 16's userspace
 * compatibility loader solves this class by loading legacy 4 KiB ELFs into
 * anonymous memory and using RWX fallback when RX/RW separation is not
 * representable at the native page size.
 *
 * This kernel bridge is intentionally much narrower:
 *   - PAGE_SIZE must be exactly 16 KiB
 *   - current must be PID 1
 *   - filename must be exactly /init
 *   - ET_EXEC
 *   - all four observed PT_LOAD headers must match the Phase384 capture
 *
 * No other executable is affected.
 */
static bool a52_p385_pid1_target(struct linux_binprm *bprm,
				 const struct elfhdr *ehdr,
				 const struct elf_phdr *phdrs)
{
	static const struct {
		Elf64_Addr vaddr;
		Elf64_Off offset;
		Elf64_Xword filesz;
		Elf64_Xword memsz;
		Elf64_Xword align;
		Elf64_Word flags;
	} expected[] = {
		{ 0x200000, 0x000000, 0x056374, 0x056374, 0x1000, PF_R },
		{ 0x257000, 0x057000, 0x1122fc, 0x1122fc, 0x1000, PF_R | PF_X },
		{ 0x36a000, 0x16a000, 0x003ff8, 0x003ff8, 0x1000, PF_R | PF_W },
		{ 0x36f000, 0x16e000, 0x000fac, 0x01f248, 0x1000, PF_R | PF_W },
	};
	int i;
	int load = 0;

	if (PAGE_SIZE != SZ_16K)
		return false;
	if (task_pid_nr(current) != 1)
		return false;
	if (!bprm->filename || strcmp(bprm->filename, "/init"))
		return false;
	if (ehdr->e_type != ET_EXEC)
		return false;

	for (i = 0; i < ehdr->e_phnum; i++) {
		const struct elf_phdr *p = &phdrs[i];

		if (p->p_type != PT_LOAD)
			continue;
		if (load >= ARRAY_SIZE(expected))
			return false;
		if (p->p_vaddr != expected[load].vaddr ||
		    p->p_offset != expected[load].offset ||
		    p->p_filesz != expected[load].filesz ||
		    p->p_memsz != expected[load].memsz ||
		    p->p_align != expected[load].align ||
		    p->p_flags != expected[load].flags)
			return false;
		load++;
	}

	return load == ARRAY_SIZE(expected);
}

static int a52_p385_load_pid1_compat(struct linux_binprm *bprm,
				    const struct elfhdr *ehdr,
				    const struct elf_phdr *phdrs)
{
	unsigned long map_start = ~0UL;
	unsigned long map_end = 0;
	unsigned long map_len;
	unsigned long mapped;
	char *bounce = NULL;
	int i;
	int rc = 0;

	for (i = 0; i < ehdr->e_phnum; i++) {
		const struct elf_phdr *p = &phdrs[i];
		unsigned long end;

		if (p->p_type != PT_LOAD)
			continue;
		if (p->p_filesz > p->p_memsz)
			return -EINVAL;
		if (p->p_vaddr + p->p_memsz < p->p_vaddr)
			return -EOVERFLOW;

		end = (unsigned long)(p->p_vaddr + p->p_memsz);
		if ((unsigned long)p->p_vaddr < map_start)
			map_start = (unsigned long)p->p_vaddr;
		if (end > map_end)
			map_end = end;
	}

	if (map_start == ~0UL || map_end <= map_start)
		return -EINVAL;

	map_start = ELF_PAGESTART(map_start);
	map_end = ELF_PAGEALIGN(map_end);
	map_len = map_end - map_start;

	a52_p383_record("P385 MAP anon=%lx-%lx len=%lx", map_start, map_end, map_len);

	mapped = vm_mmap(NULL, map_start, map_len,
			 PROT_READ | PROT_WRITE | PROT_EXEC,
			 MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, 0);
	if (mapped != map_start) {
		rc = IS_ERR_VALUE(mapped) ? (int)(long)mapped : -EINVAL;
		if (!IS_ERR_VALUE(mapped))
			vm_munmap(mapped, map_len);
		a52_p383_record_critical("P385 MAPFAIL rc=%d got=%lx", rc, mapped);
		return rc;
	}

	bounce = kmalloc(PAGE_SIZE, GFP_KERNEL);
	if (!bounce) {
		rc = -ENOMEM;
		goto fail;
	}

	for (i = 0; i < ehdr->e_phnum; i++) {
		const struct elf_phdr *p = &phdrs[i];
		unsigned long dst;
		unsigned long left;
		loff_t pos;

		if (p->p_type != PT_LOAD)
			continue;

		dst = (unsigned long)p->p_vaddr;
		left = (unsigned long)p->p_filesz;
		pos = p->p_offset;

		while (left) {
			size_t want = min_t(size_t, left, PAGE_SIZE);
			ssize_t got = kernel_read(bprm->file, bounce, want, &pos);

			if (got < 0) {
				rc = (int)got;
				goto fail;
			}
			if (!got) {
				rc = -EIO;
				goto fail;
			}
			if (copy_to_user((void __user *)dst, bounce, got)) {
				rc = -EFAULT;
				goto fail;
			}

			dst += got;
			left -= got;
		}

		if (p->p_memsz > p->p_filesz) {
			unsigned long zstart =
				(unsigned long)(p->p_vaddr + p->p_filesz);
			unsigned long zlen =
				(unsigned long)(p->p_memsz - p->p_filesz);

			if (clear_user((void __user *)zstart, zlen)) {
				rc = -EFAULT;
				goto fail;
			}
		}

		a52_p383_record("P385 COPY i=%d v=%llx fs=%llx ms=%llx",
			i, (unsigned long long)p->p_vaddr,
			(unsigned long long)p->p_filesz,
			(unsigned long long)p->p_memsz);
	}

	kfree(bounce);
	a52_p383_record_critical("P385 READY RWX anon=%lx-%lx", map_start, map_end);
	return 0;

fail:
	kfree(bounce);
	vm_munmap(map_start, map_len);
	a52_p383_record_critical("P385 LOADFAIL rc=%d", rc);
	return rc;
}

static int set_brk(unsigned long start, unsigned long end, int prot)
'''
    replace_once(elf, helper_anchor, helper, "PID1 16K compat helper")

    # State flag in load_elf_binary.
    replace_once(
        elf,
        """	int executable_stack = EXSTACK_DEFAULT;
	struct pt_regs *regs = current_pt_regs();
""",
        """	int executable_stack = EXSTACK_DEFAULT;
	bool a52_p385_compat = false;
	struct pt_regs *regs = current_pt_regs();
""",
        "compat state flag",
    )

    # Arm compat after the new mm/stack exists but before PT_LOAD mapping.
    map_loop_anchor = """	current->mm->start_stack = bprm->p;

	/* A52 Phase383: persist the PID1 ELF geometry before the first map. */
"""
    map_loop_new = """	current->mm->start_stack = bprm->p;

	if (a52_p385_pid1_target(bprm, &loc->elf_ex, elf_phdata)) {
		retval = a52_p385_load_pid1_compat(bprm, &loc->elf_ex, elf_phdata);
		if (retval)
			goto out_free_dentry;
		a52_p385_compat = true;
		a52_p383_record("P385 ACTIVE pid=1 file=/init");
	}

	/* A52 Phase383: persist the PID1 ELF geometry before the first map. */
"""
    replace_once(elf, map_loop_anchor, map_loop_new, "arm compat before PT_LOAD loop")

    # Keep the normal accounting loop, but don't re-map PT_LOADs after the
    # anonymous compat image has been populated.
    normal_map = """		error = elf_map(bprm->file, load_bias + vaddr, elf_ppnt,
				elf_prot, elf_flags, total_size);
		if (BAD_ADDR(error)) {
"""
    compat_map = """		if (a52_p385_compat)
			error = ELF_PAGESTART(load_bias + vaddr);
		else
			error = elf_map(bprm->file, load_bias + vaddr, elf_ppnt,
					elf_prot, elf_flags, total_size);
		if (BAD_ADDR(error)) {
"""
    replace_once(elf, normal_map, compat_map, "skip file mmap in compat mode")

    # The compat image already includes anonymous zeroed BSS through the last
    # PT_LOAD.  Do not try to vm_brk over that existing mapping.
    brk_anchor = """	retval = set_brk(elf_bss, elf_brk, bss_prot);
	if (retval)
		goto out_free_dentry;
	if (likely(elf_bss != elf_brk) && unlikely(padzero(elf_bss))) {
		retval = -EFAULT; /* Nobody gets to see this, but.. */
		goto out_free_dentry;
	}
"""
    brk_new = """	if (a52_p385_compat) {
		current->mm->start_brk = current->mm->brk = ELF_PAGEALIGN(elf_brk);
		a52_p383_record("P385 BRK start=%lx", current->mm->brk);
	} else {
		retval = set_brk(elf_bss, elf_brk, bss_prot);
		if (retval)
			goto out_free_dentry;
		if (likely(elf_bss != elf_brk) && unlikely(padzero(elf_bss))) {
			retval = -EFAULT; /* Nobody gets to see this, but.. */
			goto out_free_dentry;
		}
	}
"""
    replace_once(elf, brk_anchor, brk_new, "compat BSS/brk handling")

    text = elf.read_text()
    required = [
        MARK,
        "P385 READY RWX",
        "a52_p385_pid1_target",
        "a52_p385_load_pid1_compat",
        "MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE",
        "if (a52_p385_compat)",
        'a52_p383_record("P385 ACTIVE pid=1 file=/init")',
    ]
    for token in required:
        if token not in text:
            raise SystemExit(f"Phase385 postcondition missing: {token}")

    print("A52 Phase385 PID1 native-16K compatibility loader applied successfully")

if __name__ == "__main__":
    main()
