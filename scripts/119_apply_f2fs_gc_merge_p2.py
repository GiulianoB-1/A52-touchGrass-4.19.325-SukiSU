#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()

def read(path):
    return (root / path).read_text()

def write(path, data):
    (root / path).write_text(data)

def replace_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)

marker = "A52 F2FS P2: gc_merge"

# 1) Mount flag.
p = "fs/f2fs/f2fs.h"
s = read(p)
if marker not in s:
    s = replace_once(
        s,
        "#define F2FS_MOUNT_NORECOVERY\t\t0x04000000\n",
        "#define F2FS_MOUNT_NORECOVERY\t\t0x04000000\n"
        "#define F2FS_MOUNT_GC_MERGE\t\t0x08000000 /* A52 F2FS P2: gc_merge */\n",
        "f2fs.h mount flag",
    )
write(p, s)

# 2) Foreground-GC wait queue.
p = "fs/f2fs/gc.h"
s = read(p)
if marker not in s:
    s = replace_once(
        s,
        "\t/* for changing gc mode */\n\tunsigned int gc_wake;\n",
        "\t/* for changing gc mode */\n\tunsigned int gc_wake;\n\n"
        "\t/* A52 F2FS P2: gc_merge foreground callers wait here. */\n"
        "\twait_queue_head_t fggc_wq;\n",
        "gc.h fggc queue",
    )
write(p, s)

# 3) GC thread support. This is adapted to the Samsung 4.19 f2fs_gc()
# signature: f2fs_gc(sbi, sync, background, segno).
p = "fs/f2fs/gc.c"
s = read(p)
if marker not in s:
    s = replace_once(
        s,
        "\twait_queue_head_t *wq = &sbi->gc_thread->gc_wait_queue_head;\n\tunsigned int wait_ms;\n",
        "\twait_queue_head_t *wq = &sbi->gc_thread->gc_wait_queue_head;\n"
        "\twait_queue_head_t *fggc_wq = &sbi->gc_thread->fggc_wq;\n"
        "\tunsigned int wait_ms;\n",
        "gc.c waitqueue declaration",
    )
    s = replace_once(
        s,
        "\t\tbool sync_mode;\n\n\t\twait_event_interruptible_timeout(*wq,\n"
        "\t\t\t\tkthread_should_stop() || freezing(current) ||\n"
        "\t\t\t\tgc_th->gc_wake,\n"
        "\t\t\t\tmsecs_to_jiffies(wait_ms));\n",
        "\t\tbool sync_mode, foreground = false;\n\n"
        "\t\twait_event_interruptible_timeout(*wq,\n"
        "\t\t\t\tkthread_should_stop() || freezing(current) ||\n"
        "\t\t\t\twaitqueue_active(fggc_wq) ||\n"
        "\t\t\t\tgc_th->gc_wake,\n"
        "\t\t\t\tmsecs_to_jiffies(wait_ms));\n\n"
        "\t\t/* A52 F2FS P2: gc_merge, upstream 5911d2d1d1a3. */\n"
        "\t\tif (test_opt(sbi, GC_MERGE) && waitqueue_active(fggc_wq))\n"
        "\t\t\tforeground = true;\n",
        "gc.c wait event",
    )
    s = replace_once(
        s,
        "\t\tif (!down_write_trylock(&sbi->gc_lock)) {\n"
        "\t\t\tstat_other_skip_bggc_count(sbi);\n"
        "\t\t\tgoto next;\n"
        "\t\t}\n",
        "\t\tif (foreground) {\n"
        "\t\t\tdown_write(&sbi->gc_lock);\n"
        "\t\t\tgoto do_gc;\n"
        "\t\t} else if (!down_write_trylock(&sbi->gc_lock)) {\n"
        "\t\t\tstat_other_skip_bggc_count(sbi);\n"
        "\t\t\tgoto next;\n"
        "\t\t}\n",
        "gc.c gc_lock",
    )
    old = """do_gc:
		stat_inc_bggc_count(sbi->stat_info);

		sync_mode = F2FS_OPTION(sbi).bggc_mode == BGGC_MODE_SYNC;

		/* if return value is not zero, no victim was selected */
		if (f2fs_gc(sbi, sync_mode, true, NULL_SEGNO))
			wait_ms = gc_th->no_gc_sleep_time;

		trace_f2fs_background_gc(sbi->sb, wait_ms,
"""
    new = """do_gc:
		if (!foreground)
			stat_inc_bggc_count(sbi->stat_info);

		sync_mode = F2FS_OPTION(sbi).bggc_mode == BGGC_MODE_SYNC;
		if (foreground)
			sync_mode = false;

		/*
		 * A52 F2FS P2: foreground work is executed by the GC thread.
		 * Fold in upstream 1adaa71ea9bf so foreground GC does not
		 * perturb the normal background-GC sleep interval.
		 */
		if (f2fs_gc(sbi, sync_mode, !foreground, NULL_SEGNO)) {
			if (!foreground)
				wait_ms = gc_th->no_gc_sleep_time;
		}

		if (foreground)
			wake_up_all(&gc_th->fggc_wq);

		trace_f2fs_background_gc(sbi->sb, wait_ms,
"""
    s = replace_once(s, old, new, "gc.c do_gc")
    s = replace_once(
        s,
        "\tsbi->gc_thread = gc_th;\n"
        "\tinit_waitqueue_head(&sbi->gc_thread->gc_wait_queue_head);\n",
        "\tsbi->gc_thread = gc_th;\n"
        "\tinit_waitqueue_head(&sbi->gc_thread->gc_wait_queue_head);\n"
        "\tinit_waitqueue_head(&sbi->gc_thread->fggc_wq);\n",
        "gc.c start queue",
    )
    s = replace_once(
        s,
        "\tkthread_stop(gc_th->f2fs_gc_task);\n"
        "\tkvfree(gc_th);\n",
        "\tkthread_stop(gc_th->f2fs_gc_task);\n"
        "\twake_up_all(&gc_th->fggc_wq);\n"
        "\tkvfree(gc_th);\n",
        "gc.c stop queue",
    )
write(p, s)

# 4) Route foreground GC requests through the GC thread.
# The 2026 upstream deadlock fix 8b4468ec023d is folded in here by
# submitting the Samsung tree's cached DATA write bio before waiting.
# This old tree has no f2fs_submit_all_merged_ipu_writes() helper/cache.
p = "fs/f2fs/segment.c"
s = read(p)
if marker not in s:
    old = """	if (has_not_enough_free_secs(sbi, 0, 0)) {
		down_write(&sbi->gc_lock);
		f2fs_gc(sbi, false, false, NULL_SEGNO);
	}
"""
    new = """	if (has_not_enough_free_secs(sbi, 0, 0)) {
		if (test_opt(sbi, GC_MERGE) && sbi->gc_thread &&
					sbi->gc_thread->f2fs_gc_task) {
			DEFINE_WAIT(wait);

			/*
			 * A52 F2FS P2: gc_merge.
			 * Submit the cached DATA bio before sleeping for foreground
			 * GC. This folds in upstream 8b4468ec023d, adapted to this
			 * older tree which has no separate merged-IPU bio cache.
			 * Keep this inside GC_MERGE so nogc_merge retains the exact
			 * boot-validated P1 foreground-GC behavior.
			 */
			f2fs_submit_merged_write(sbi, DATA);
			prepare_to_wait(&sbi->gc_thread->fggc_wq, &wait,
					TASK_UNINTERRUPTIBLE);
			wake_up(&sbi->gc_thread->gc_wait_queue_head);
			io_schedule();
			finish_wait(&sbi->gc_thread->fggc_wq, &wait);
		} else {
			down_write(&sbi->gc_lock);
			f2fs_gc(sbi, false, false, NULL_SEGNO);
		}
	}
"""
    s = replace_once(s, old, new, "segment.c balance_fs")
write(p, s)

# 5) Mount parser, show-options, remount thread lifecycle, initial mount.
p = "fs/f2fs/super.c"
s = read(p)
if marker not in s:
    s = replace_once(
        s,
        "\tOpt_checkpoint_ioprio,\n\tOpt_err,\n",
        "\tOpt_checkpoint_ioprio,\n"
        "\tOpt_gc_merge,\n"
        "\tOpt_nogc_merge,\n"
        "\tOpt_err,\n",
        "super.c enum",
    )
    s = replace_once(
        s,
        "\t{Opt_checkpoint_ioprio, \"checkpoint_ioprio=%u\"},\n"
        "\t{Opt_err, NULL},\n",
        "\t{Opt_checkpoint_ioprio, \"checkpoint_ioprio=%u\"},\n"
        "\t{Opt_gc_merge, \"gc_merge\"},\n"
        "\t{Opt_nogc_merge, \"nogc_merge\"},\n"
        "\t{Opt_err, NULL},\n",
        "super.c tokens",
    )
    s = replace_once(
        s,
        "\t\tcase Opt_checkpoint_ioprio:\n"
        "\t\t\tif (args->from && match_int(args, &arg))\n"
        "\t\t\t\treturn -EINVAL;\n"
        "\t\t\tif (arg < 0 || arg > 7) {\n"
        "\t\t\t\tf2fs_err(sbi, \"Invalid checkpoint task\"\n"
        "\t\t\t\t\t       \" IO priority (must be 0-7)\");\n"
        "\t\t\t\treturn -EINVAL;\n"
        "\t\t\t}\n"
        "\t\t\tF2FS_OPTION(sbi).ckpt_ioprio = (unsigned int)arg;\n"
        "\t\t\tbreak;\n"
        "\t\tdefault:\n",
        "\t\tcase Opt_checkpoint_ioprio:\n"
        "\t\t\tif (args->from && match_int(args, &arg))\n"
        "\t\t\t\treturn -EINVAL;\n"
        "\t\t\tif (arg < 0 || arg > 7) {\n"
        "\t\t\t\tf2fs_err(sbi, \"Invalid checkpoint task\"\n"
        "\t\t\t\t\t       \" IO priority (must be 0-7)\");\n"
        "\t\t\t\treturn -EINVAL;\n"
        "\t\t\t}\n"
        "\t\t\tF2FS_OPTION(sbi).ckpt_ioprio = (unsigned int)arg;\n"
        "\t\t\tbreak;\n"
        "\t\tcase Opt_gc_merge:\n"
        "\t\t\tset_opt(sbi, GC_MERGE); /* A52 F2FS P2: gc_merge */\n"
        "\t\t\tbreak;\n"
        "\t\tcase Opt_nogc_merge:\n"
        "\t\t\tclear_opt(sbi, GC_MERGE);\n"
        "\t\t\tbreak;\n"
        "\t\tdefault:\n",
        "super.c parser",
    )
    s = replace_once(
        s,
        "\telse if (F2FS_OPTION(sbi).bggc_mode == BGGC_MODE_OFF)\n"
        "\t\tseq_printf(seq, \",background_gc=%s\", \"off\");\n\n"
        "\tif (test_opt(sbi, DISABLE_ROLL_FORWARD))\n",
        "\telse if (F2FS_OPTION(sbi).bggc_mode == BGGC_MODE_OFF)\n"
        "\t\tseq_printf(seq, \",background_gc=%s\", \"off\");\n\n"
        "\tif (test_opt(sbi, GC_MERGE))\n"
        "\t\tseq_puts(seq, \",gc_merge\");\n"
        "\telse\n"
        "\t\tseq_puts(seq, \",nogc_merge\");\n\n"
        "\tif (test_opt(sbi, DISABLE_ROLL_FORWARD))\n",
        "super.c show options",
    )
    s = replace_once(
        s,
        "\tif ((*flags & SB_RDONLY) ||\n"
        "\t\t\tF2FS_OPTION(sbi).bggc_mode == BGGC_MODE_OFF) {\n",
        "\tif ((*flags & SB_RDONLY) ||\n"
        "\t\t\t(F2FS_OPTION(sbi).bggc_mode == BGGC_MODE_OFF &&\n"
        "\t\t\t !test_opt(sbi, GC_MERGE))) {\n",
        "super.c remount lifecycle",
    )
    s = replace_once(
        s,
        "\tif (F2FS_OPTION(sbi).bggc_mode != BGGC_MODE_OFF && !f2fs_readonly(sb)) {\n",
        "\tif ((F2FS_OPTION(sbi).bggc_mode != BGGC_MODE_OFF ||\n"
        "\t\ttest_opt(sbi, GC_MERGE)) && !f2fs_readonly(sb)) {\n",
        "super.c initial gc thread",
    )
write(p, s)

# Sanity checks.
checks = {
    "fs/f2fs/f2fs.h": ["F2FS_MOUNT_GC_MERGE"],
    "fs/f2fs/gc.h": ["fggc_wq"],
    "fs/f2fs/gc.c": ["waitqueue_active(fggc_wq)", "wake_up_all(&gc_th->fggc_wq)", "!foreground"],
    "fs/f2fs/segment.c": ["prepare_to_wait(&sbi->gc_thread->fggc_wq", "f2fs_submit_merged_write(sbi, DATA)"],
    "fs/f2fs/super.c": ["Opt_gc_merge", "\"gc_merge\"", "\"nogc_merge\"", "test_opt(sbi, GC_MERGE)"],
}
for path, needles in checks.items():
    data = read(path)
    for needle in needles:
        if needle not in data:
            raise SystemExit(f"{path}: verification failed, missing {needle}")

print("F2FS P2 gc_merge port applied and verified")
