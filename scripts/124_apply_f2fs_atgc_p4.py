#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys, re

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/f2fs-atgc-p4").resolve()
out.mkdir(parents=True, exist_ok=True)
root = kernel / "fs" / "f2fs"

def rd(f): return (root/f).read_text()
def wr(f,s): (root/f).write_text(s)
def rep(f,old,new,count=1):
    s=rd(f); n=s.count(old)
    if n < count:
        raise RuntimeError(f"{f}: missing/changed anchor ({n}): {old[:140]!r}")
    wr(f,s.replace(old,new,count))

def sub1(f,pattern,repl,flags=0,label="regex"):
    s=rd(f)
    s,n=re.subn(pattern,repl,s,count=1,flags=flags)
    if n != 1:
        raise RuntimeError(f"{f}: {label} expected 1 match, got {n}: {pattern[:140]!r}")
    wr(f,s)

# ---------------------------------------------------------------------------
# P4.1 - 6efc3a05e613: dynamically enable ATGC after elapsed_time reaches the
# configured age threshold. Samsung 4.19's get_atssr_segment() is void, so
# retain the older error model while preserving the upstream state transition.
# ---------------------------------------------------------------------------
s=rd("segment.c")
start=s.index("static void __f2fs_init_atgc_curseg")
end=s.index("static void __f2fs_save_inmem_curseg", start)
new_block=r'''static void __f2fs_init_atgc_curseg(struct f2fs_sb_info *sbi, bool force)
{
	struct curseg_info *curseg = CURSEG_I(sbi, CURSEG_ALL_DATA_ATGC);

	if (!sbi->am.atgc_enabled && !force)
		return;

	down_read(&SM_I(sbi)->curseg_lock);

	mutex_lock(&curseg->curseg_mutex);
	down_write(&SIT_I(sbi)->sentry_lock);

	get_atssr_segment(sbi, CURSEG_ALL_DATA_ATGC,
				CURSEG_COLD_DATA, SSR, 0);

	up_write(&SIT_I(sbi)->sentry_lock);
	mutex_unlock(&curseg->curseg_mutex);

	up_read(&SM_I(sbi)->curseg_lock);
}

void f2fs_init_inmem_curseg(struct f2fs_sb_info *sbi)
{
	__f2fs_init_atgc_curseg(sbi, false);
}

void f2fs_reinit_atgc_curseg(struct f2fs_sb_info *sbi)
{
	if (!test_opt(sbi, ATGC))
		return;
	if (sbi->am.atgc_enabled)
		return;
	if (le64_to_cpu(F2FS_CKPT(sbi)->elapsed_time) <
			sbi->am.age_threshold)
		return;

	__f2fs_init_atgc_curseg(sbi, true);
	sbi->am.atgc_enabled = true;
	f2fs_info(sbi, "reenabled age threshold GC");
}

'''
wr("segment.c", s[:start] + new_block + s[end:])

rep("f2fs.h",
    "void f2fs_init_inmem_curseg(struct f2fs_sb_info *sbi);\n"
    "void f2fs_save_inmem_curseg(struct f2fs_sb_info *sbi);",
    "void f2fs_init_inmem_curseg(struct f2fs_sb_info *sbi);\n"
    "void f2fs_reinit_atgc_curseg(struct f2fs_sb_info *sbi);\n"
    "void f2fs_save_inmem_curseg(struct f2fs_sb_info *sbi);")

sub1("checkpoint.c",
     r"f2fs_restore_inmem_curseg\(sbi\);\n(?P<indent>\s*)(?P<next>stop:|stat_inc_cp_count)",
     lambda m: "f2fs_restore_inmem_curseg(sbi);\n"
               "\tf2fs_reinit_atgc_curseg(sbi);\n"
               + m.group("indent") + m.group("next"),
     label="checkpoint ATGC reinit")

# ---------------------------------------------------------------------------
# P4.2 - 6f092b55e1ad: expose actual runtime enable state.
# Keep Samsung's sprintf-style sysfs API.
# ---------------------------------------------------------------------------
if "static ssize_t atgc_enabled_show" not in rd("sysfs.c"):
    rep("sysfs.c",
        "static ssize_t dirty_segments_show(struct f2fs_attr *a,\n",
        "static ssize_t atgc_enabled_show(struct f2fs_attr *a,\n"
        "\t\tstruct f2fs_sb_info *sbi, char *buf)\n"
        "{\n"
        "\treturn sprintf(buf, \"%d\\n\", sbi->am.atgc_enabled ? 1 : 0);\n"
        "}\n\n"
        "static ssize_t dirty_segments_show(struct f2fs_attr *a,\n")

if "F2FS_GENERAL_RO_ATTR(atgc_enabled);" not in rd("sysfs.c"):
    rep("sysfs.c",
        "F2FS_GENERAL_RO_ATTR(dirty_segments);",
        "F2FS_GENERAL_RO_ATTR(atgc_enabled);\n"
        "F2FS_GENERAL_RO_ATTR(dirty_segments);")

if "ATTR_LIST(atgc_enabled)" not in rd("sysfs.c"):
    rep("sysfs.c",
        "\tATTR_LIST(atgc_age_threshold),\n",
        "\tATTR_LIST(atgc_age_threshold),\n"
        "\tATTR_LIST(atgc_enabled),\n")

# ---------------------------------------------------------------------------
# P4.3 - ac2d750b2043: AT_SSR is for background non-urgent GC only.
# Samsung has GC_URGENT rather than upstream's later GC_URGENT_HIGH.
# ---------------------------------------------------------------------------
sub1("gc.c",
     r"int type = fio\.sbi->am\.atgc_enabled\s*\?\s*"
     r"CURSEG_ALL_DATA_ATGC\s*:\s*CURSEG_COLD_DATA;",
     "int type = fio.sbi->am.atgc_enabled && (gc_type == BG_GC) &&\n"
     "\t\t\t\t(fio.sbi->gc_mode != GC_URGENT) ?\n"
     "\t\t\t\tCURSEG_ALL_DATA_ATGC : CURSEG_COLD_DATA;",
     flags=re.S, label="move_data_block AT_SSR restriction")

# P4.4 - 8cb1f4080dd9, combined with ac2d750b:
# only route an existing data block to the ATGC curseg. This 4.19 tree has
# __is_valid_data_blkaddr() and FS_DATA_IO, but not later FI_OPU_WRITE state.
sub1("segment.c",
     r"if \(is_cold_data\(fio->page\)\) \{\s*"
     r"if \(fio->sbi->am\.atgc_enabled\)\s*"
     r"return CURSEG_ALL_DATA_ATGC;\s*"
     r"else\s*"
     r"return CURSEG_COLD_DATA;\s*"
     r"\}",
     "if (is_cold_data(fio->page)) {\n"
     "\t\t\tif (fio->sbi->am.atgc_enabled &&\n"
     "\t\t\t\t(fio->io_type == FS_DATA_IO) &&\n"
     "\t\t\t\t(fio->sbi->gc_mode != GC_URGENT) &&\n"
     "\t\t\t\t__is_valid_data_blkaddr(fio->old_blkaddr))\n"
     "\t\t\t\treturn CURSEG_ALL_DATA_ATGC;\n"
     "\t\t\telse\n"
     "\t\t\t\treturn CURSEG_COLD_DATA;\n"
     "\t\t}",
     flags=re.S, label="cold-data ATGC routing guard")

# ---------------------------------------------------------------------------
# P4.5 - 43563069e1c1: an in-memory curseg can be uninitialized.
# ---------------------------------------------------------------------------
sub1("segment.c",
     r"if \(flush\)\s*\n\s*write_sum_page\(sbi, curseg->sum_blk,\s*"
     r"GET_SUM_BLOCK\(sbi, curseg->segno\)\);",
     "if (flush && curseg->inited)\n"
     "\t\twrite_sum_page(sbi, curseg->sum_blk,\n"
     "\t\t\t\tGET_SUM_BLOCK(sbi, curseg->segno));",
     flags=re.S, label="change_curseg inited guard")

# ---------------------------------------------------------------------------
# P4.6 - dccd324fa9bd: don't select empty sections as GC victims.
# Insert after CP-disabled validity filtering and before victim_secmap checks.
# ---------------------------------------------------------------------------
gc=rd("gc.c")
marker="\t\tif (gc_type == BG_GC && test_bit(secno, dirty_i->victim_secmap))"
if "\t\tif (!get_valid_blocks(sbi, segno, true))\n\t\t\tgoto next;\n" not in gc:
    if marker not in gc:
        raise RuntimeError("gc.c: victim_secmap insertion anchor changed")
    gc=gc.replace(marker,
        "\t\tif (!get_valid_blocks(sbi, segno, true))\n"
        "\t\t\tgoto next;\n\n"
        + marker, 1)
    wr("gc.c",gc)

# ---------------------------------------------------------------------------
# Structural validation: require P3C plus all P4 semantics, and explicitly
# reject accidental introduction of APIs absent from this Samsung generation.
# ---------------------------------------------------------------------------
checks={
    "f2fs.h":[
        "void f2fs_reinit_atgc_curseg(struct f2fs_sb_info *sbi);",
        "F2FS_MOUNT_ATGC",
    ],
    "checkpoint.c":[
        "f2fs_restore_inmem_curseg(sbi);",
        "f2fs_reinit_atgc_curseg(sbi);",
    ],
    "segment.c":[
        "__f2fs_init_atgc_curseg(struct f2fs_sb_info *sbi, bool force)",
        "reenabled age threshold GC",
        "fio->sbi->gc_mode != GC_URGENT",
        "__is_valid_data_blkaddr(fio->old_blkaddr)",
        "if (flush && curseg->inited)",
    ],
    "gc.c":[
        "(gc_type == BG_GC)",
        "fio.sbi->gc_mode != GC_URGENT",
        "if (!get_valid_blocks(sbi, segno, true))",
    ],
    "sysfs.c":[
        "static ssize_t atgc_enabled_show",
        "F2FS_GENERAL_RO_ATTR(atgc_enabled);",
        "ATTR_LIST(atgc_enabled)",
    ],
    "super.c":[
        "P3C: enable ATGC only during the initial mount",
        "set_opt(sbi, ATGC);",
    ],
}
for rel,needles in checks.items():
    data=rd(rel)
    for needle in needles:
        if needle not in data:
            raise RuntimeError(f"{rel}: missing P4 sanity token: {needle}")

all_src="\n".join(p.read_text(errors="ignore") for p in root.glob("*.[ch]"))
for bad in ["GC_URGENT_HIGH", "GC_URGENT_LOW", "GC_URGENT_MID", "FI_OPU_WRITE"]:
    if bad in all_src:
        raise RuntimeError(f"unexpected newer API leaked into Samsung P4: {bad}")

subprocess.run(["git","diff","--check"],cwd=kernel,check=True)

report = """F2FS P4 newer ATGC refinements
base=P3C runtime-validated Age-threshold + AT_SSR
dynamic_atgc_reenable=6efc3a05e613 adapted to Samsung void curseg init
atgc_enabled_sysfs=6f092b55e1ad adapted to sprintf sysfs
atssr_fg_urgent_guard=ac2d750b2043 adapted GC_URGENT
valid_old_blkaddr_guard=8cb1f4080dd9
uninitialized_curseg_guard=43563069e1c1
skip_empty_gc_sections=dccd324fa9bd
ATGC boot default=on (P3C preserved)
"""
(out/"report.txt").write_text(report)
with (out/"atgc-p4.diff").open("wb") as f:
    subprocess.run(["git","diff","--","fs/f2fs"],cwd=kernel,stdout=f,check=True)

print(report)
print("P4 ATGC refinement adaptation complete")
