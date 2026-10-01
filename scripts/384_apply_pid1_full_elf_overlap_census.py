#!/usr/bin/env python3
from pathlib import Path
import sys

MARK="A52_PHASE384_PID1_FULL_ELF_OVERLAP_CENSUS_V1"

def one(path, old, new, label):
    text=path.read_text()
    if new in text:
        print(f"{path}: {label} already applied")
        return
    n=text.count(old)
    if n!=1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {n}")
    path.write_text(text.replace(old,new,1))
    print(f"{path}: applied {label}")

def main():
    if len(sys.argv)!=2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root=Path(sys.argv[1]).resolve()
    elf=root/"fs/binfmt_elf.c"
    if not elf.is_file():
        raise SystemExit(f"missing {elf}")

    old='''	/* Now we do a little grungy work by mmapping the ELF image into
	   the correct location in memory. */
	for(i = 0, elf_ppnt = elf_phdata;
'''
    new=r'''	/*
	 * A52_PHASE384_PID1_FULL_ELF_OVERLAP_CENSUS_V1
	 *
	 * Before mapping PID1, dump every PT_LOAD using both legacy 4 KiB
	 * map geometry and the native ELF_MIN_ALIGN geometry.  This is read-only:
	 * no mapping behavior is changed.  The final summary is synchronously
	 * flushed through the Phase383 Samsung-debug + ramoops transport.
	 */
	if (task_pid_nr(current) == 1) {
		unsigned long p384_prev_start = 0;
		unsigned long p384_prev_end = 0;
		unsigned int p384_prev_flags = 0;
		int p384_prev_idx = -1;
		int p384_loads = 0;
		int p384_overlaps = 0;
		int p384_wx = 0;
		int p384_j;

		for (p384_j = 0; p384_j < loc->elf_ex.e_phnum; p384_j++) {
			struct elf_phdr *p = &elf_phdata[p384_j];
			unsigned long s4, e4, sn, en;
			unsigned long off4, offn;
			unsigned long size4, sizen;

			if (p->p_type != PT_LOAD)
				continue;

			off4 = (unsigned long)p->p_vaddr & (SZ_4K - 1);
			s4 = (unsigned long)p->p_vaddr & ~(SZ_4K - 1);
			size4 = ALIGN((unsigned long)p->p_filesz + off4, SZ_4K);
			e4 = s4 + size4;

			offn = ELF_PAGEOFFSET((unsigned long)p->p_vaddr);
			sn = ELF_PAGESTART((unsigned long)p->p_vaddr);
			sizen = ELF_PAGEALIGN((unsigned long)p->p_filesz + offn);
			en = sn + sizen;

			a52_p383_record("P384 L0 i=%d fl=%x al=%llx", p384_j,
				(unsigned int)p->p_flags,
				(unsigned long long)p->p_align);
			a52_p383_record("P384 L1 i=%d v=%llx off=%llx", p384_j,
				(unsigned long long)p->p_vaddr,
				(unsigned long long)p->p_offset);
			a52_p383_record("P384 L2 i=%d fs=%llx ms=%llx", p384_j,
				(unsigned long long)p->p_filesz,
				(unsigned long long)p->p_memsz);
			a52_p383_record("P384 R4 i=%d %lx-%lx sz=%lx", p384_j,
				s4, e4, size4);
			a52_p383_record("P384 RN i=%d %lx-%lx sz=%lx", p384_j,
				sn, en, sizen);

			if (p384_prev_idx >= 0 && p384_prev_end > sn) {
				unsigned long ov_start = max(p384_prev_start, sn);
				unsigned long ov_end = min(p384_prev_end, en);
				unsigned int merged = p384_prev_flags | p->p_flags;
				int wx = !!((merged & PF_W) && (merged & PF_X));

				p384_overlaps++;
				p384_wx += wx;
				a52_p383_record("P384 OV a=%d b=%d bytes=%lx", p384_prev_idx,
					p384_j, ov_end - ov_start);
				a52_p383_record("P384 OP a=%x b=%x u=%x wx=%d",
					p384_prev_flags, (unsigned int)p->p_flags,
					merged, wx);
			}

			p384_prev_start = sn;
			p384_prev_end = en;
			p384_prev_flags = p->p_flags;
			p384_prev_idx = p384_j;
			p384_loads++;
		}

		a52_p383_record_critical("P384 DONE loads=%d ov=%d wx=%d page=%lu",
			p384_loads, p384_overlaps, p384_wx,
			(unsigned long)ELF_MIN_ALIGN);
	}

	/* Now we do a little grungy work by mmapping the ELF image into
	   the correct location in memory. */
	for(i = 0, elf_ppnt = elf_phdata;
'''
    one(elf,old,new,"full PID1 PT_LOAD overlap census")

    txt=elf.read_text()
    for tok in (MARK,'P384 DONE loads=%d ov=%d wx=%d page=%lu','P384 OP a=%x b=%x u=%x wx=%d'):
        if tok not in txt:
            raise SystemExit("Phase384 postcondition missing: "+tok)
    print("A52 Phase384 full PID1 ELF overlap census: PASS")

if __name__=="__main__":
    main()
