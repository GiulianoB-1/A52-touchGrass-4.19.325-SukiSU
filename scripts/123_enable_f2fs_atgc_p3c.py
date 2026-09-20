#!/usr/bin/env python3
from pathlib import Path
import subprocess, sys

kernel = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()
out = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/f2fs-atgc-p3c").resolve()
out.mkdir(parents=True, exist_ok=True)

p = kernel / "fs" / "f2fs" / "super.c"
s = p.read_text()

old = """	if (!remount)
		F2FS_OPTION(sbi).ckpt_ioprio = DEFAULT_ISSUE_CHECKPOINT_IOPRIO;
"""
new = """	if (!remount) {
		F2FS_OPTION(sbi).ckpt_ioprio = DEFAULT_ISSUE_CHECKPOINT_IOPRIO;
		/*
		 * P3C: enable ATGC only during the initial mount so
		 * init_atgc_management() sees the option before GC manager setup.
		 * Dynamic remount switching remains prohibited by upstream logic.
		 */
		set_opt(sbi, ATGC);
	}
"""
if old not in s:
    raise RuntimeError("default_options initial-mount anchor changed")
s = s.replace(old, new, 1)
p.write_text(s)

subprocess.run(["git","diff","--check"],cwd=kernel,check=True)

superc = p.read_text()
for token in [
    "P3C: enable ATGC only during the initial mount",
    "set_opt(sbi, ATGC);",
    "switch atgc option is not allowed",
]:
    if token not in superc:
        raise RuntimeError(f"missing P3C token: {token}")

gc = (kernel/"fs"/"f2fs"/"gc.c").read_text()
for token in [
    "if (test_opt(sbi, ATGC) &&",
    "SIT_I(sbi)->elapsed_time >= DEF_GC_THREAD_AGE_THRESHOLD",
    "am->atgc_enabled = true;",
]:
    if token not in gc:
        raise RuntimeError(f"ATGC init guard missing: {token}")

(out/"report.txt").write_text(
    "F2FS P3C ATGC boot-time activation\n"
    "base=P3B boot-validated\n"
    "ATGC default=on at initial mount only\n"
    "ATGC dynamic remount switching=unchanged/prohibited\n"
    "ATGC age guard=preserved\n"
)
with (out/"atgc-p3c.diff").open("wb") as f:
    subprocess.run(["git","diff","--","fs/f2fs/super.c"],cwd=kernel,stdout=f,check=True)

print("P3C ATGC initial-mount activation applied")
