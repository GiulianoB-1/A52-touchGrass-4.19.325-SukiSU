#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys
import time

if len(sys.argv) != 2:
    raise SystemExit("usage: 88_apply_f2fs_segment_age_accounting.py <kernel-dir>")

root = Path(sys.argv[1]).resolve()
path = root / "fs/f2fs/segment.c"

if not path.is_file():
    raise SystemExit(f"missing {path}")

text = path.read_text()

upstream_commit = "6f3a01ae9b72d5b7c57632632b8c8088db11e7ab"
helper = """static void update_segment_mtime(struct f2fs_sb_info *sbi, block_t blkaddr)
{
	unsigned int segno = GET_SEGNO(sbi, blkaddr);
	struct seg_entry *se = get_seg_entry(sbi, segno);
	unsigned long long mtime = get_mtime(sbi, false);

	if (!se->mtime)
		se->mtime = mtime;
	else
		se->mtime = div_u64(se->mtime * se->valid_blocks + mtime,
						se->valid_blocks + 1);

	if (mtime > SIT_I(sbi)->max_mtime)
		SIT_I(sbi)->max_mtime = mtime;
}

"""

marker = "static void update_sit_entry(struct f2fs_sb_info *sbi, block_t blkaddr, int del)\n"
old_accounting = """	se->valid_blocks = new_vblocks;
	se->mtime = get_mtime(sbi, false);
	if (se->mtime > SIT_I(sbi)->max_mtime)
		SIT_I(sbi)->max_mtime = se->mtime;
"""
new_accounting = """	update_segment_mtime(sbi, blkaddr);

	se->valid_blocks = new_vblocks;
"""

already = helper in text and new_accounting in text and old_accounting not in text

if not already:
    if helper in text:
        raise SystemExit("partial Phase88 state: helper exists but accounting replacement is incomplete")
    if text.count(marker) != 1:
        raise SystemExit(f"expected one update_sit_entry marker, found {text.count(marker)}")
    if text.count(old_accounting) != 1:
        raise SystemExit(
            f"expected one Samsung last-write mtime accounting block, found {text.count(old_accounting)}"
        )

    text = text.replace(marker, helper + marker, 1)
    text = text.replace(old_accounting, new_accounting, 1)
    path.write_text(text)

# Structural verification
final = path.read_text()
checks = [
    "static void update_segment_mtime(struct f2fs_sb_info *sbi, block_t blkaddr)",
    "se->mtime = div_u64(se->mtime * se->valid_blocks + mtime,",
    "se->valid_blocks + 1);",
    "update_segment_mtime(sbi, blkaddr);",
]
for needle in checks:
    if needle not in final:
        raise SystemExit(f"missing Phase88 postcondition: {needle}")

if old_accounting in final:
    raise SystemExit("old last-write-wins segment mtime accounting is still present")

# Ensure this phase is intentionally narrow.
for forbidden in (
    "struct atgc_management",
    "CURSEG_ALL_DATA_ATGC",
    "GC_IDLE_AT",
    "AT_SSR",
    "Opt_atgc",
):
    if forbidden in final:
        raise SystemExit(f"Phase88 foundation unexpectedly contains later ATGC symbol: {forbidden}")

# Phase101 may archive the complete prepared tree immediately after Phase88.
# Newer Git can leave a detached auto-maintenance/repack process finishing a
# temporary pack after the foreground fetch has returned.  Archiving while a
# tmp_pack_* file is still changing makes GNU tar exit 1 and, more importantly,
# risks caching a non-self-consistent .git object database.  Disable future
# automatic maintenance for this generated workspace and wait for any existing
# temporary pack writer to finish.  Do not delete or ignore temporary packs.
git_dir = root / ".git"
if git_dir.is_dir():
    subprocess.run(["git", "-C", str(root), "config", "gc.auto", "0"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "maintenance.auto", "false"],
        check=True,
    )

    pack_dir = git_dir / "objects" / "pack"
    deadline = time.monotonic() + 120
    while True:
        temporary_packs = sorted(pack_dir.glob("tmp_pack_*")) if pack_dir.is_dir() else []
        if not temporary_packs:
            break
        if time.monotonic() >= deadline:
            names = ", ".join(p.name for p in temporary_packs)
            raise SystemExit(f"git temporary pack files did not quiesce before cache snapshot: {names}")
        time.sleep(1)

    subprocess.run(
        ["git", "-C", str(root), "fsck", "--no-dangling"],
        check=True,
        stdout=subprocess.DEVNULL,
    )

report = root.parent.parent / "artifacts" / "phase88-f2fs-segment-age-accounting.txt"
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text(
    "phase=88-f2fs-5.10-atgc-foundation1\n"
    "base=phase87-fuse-passthrough\n"
    f"upstream_commit={upstream_commit}\n"
    "scope=fs/f2fs/segment.c-only\n"
    "behavior=weighted-average-segment-update-time\n"
    "on_disk_format=unchanged\n"
    "mount_options=unchanged\n"
    "atgc=not-yet-enabled\n"
    "encryption=untouched\n"
    "compression=untouched\n"
    f"already_applied={'yes' if already else 'no'}\n"
)
print(report.read_text(), end="")
