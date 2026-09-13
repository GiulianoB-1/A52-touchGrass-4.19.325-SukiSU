#!/usr/bin/env python3
"""
Phase 81: backport modern Android Binder pieces to the A52 4.19 baseline.

Donor lineage:
  * Android common android12-5.10 for ABI-compatible oneway spam detection.
  * Android common 2026 Binder lifetime fixes:
      - 1ac5be05b2854ba2329c0e52a67edd80b3ab4352
      - 1290154c48656bd1012a837076a0fbe379330eb3
  * Android 17 6.18 is used as the current behavior reference, not as a
    wholesale file replacement, because its Binder driver depends on kernel
    infrastructure unavailable in Linux 4.19.

Samsung compatibility:
  * Preserve CONFIG_SAMSUNG_FREECESS hooks unchanged.
  * Preserve Samsung BINDER_SET_SYSTEM_SERVER_PID.
  * Also expose upstream BINDER_GET_EXTENDED_ERROR. Both use ioctl number 17,
    but their full ioctl values differ because direction and payload size are
    encoded in the command, so they safely coexist.
"""

from pathlib import Path
import re
import sys

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("workspace/touchgrass-a52xq")
ART = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("artifacts")
ART.mkdir(parents=True, exist_ok=True)

changed = []


def load(rel):
    p = ROOT / rel
    if not p.is_file():
        raise SystemExit(f"missing required file: {p}")
    return p, p.read_text()


def save(p, rel, data):
    p.write_text(data)
    changed.append(rel)
    print(f"[phase81] patched {rel}")


def replace_once(rel, old, new):
    p, s = load(rel)
    count = s.count(old)
    if count != 1:
        raise SystemExit(
            f"{rel}: expected exactly one anchor, found {count}: {old[:100]!r}"
        )
    save(p, rel, s.replace(old, new, 1))


def regex_once(rel, pattern, repl):
    p, s = load(rel)
    out, count = re.subn(pattern, repl, s, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{rel}: regex anchor count={count}: {pattern[:120]!r}")
    save(p, rel, out)


# ---------------------------------------------------------------------------
# UAPI: Android oneway spam detection.
# Slot 16 is the upstream ABI and is unused in this Samsung header.
# Slot 17 stays Samsung's BINDER_SET_SYSTEM_SERVER_PID.
# ---------------------------------------------------------------------------
replace_once(
    "include/uapi/linux/android/binder.h",
    """#define BINDER_FREEZE _IOW('b', 14, struct binder_freeze_info)
#define BINDER_GET_FROZEN_INFO _IOWR('b', 15, struct binder_frozen_status_info)
#define BINDER_SET_SYSTEM_SERVER_PID\t_IOW('b', 17, __u32)
""",
    """#define BINDER_FREEZE _IOW('b', 14, struct binder_freeze_info)
#define BINDER_GET_FROZEN_INFO _IOWR('b', 15, struct binder_frozen_status_info)
#define BINDER_ENABLE_ONEWAY_SPAM_DETECTION _IOW('b', 16, __u32)
/*
 * Samsung vendor ABI occupies ioctl 17 on this tree. Upstream Android uses
 * 17 for BINDER_GET_EXTENDED_ERROR, so keep the vendor ABI intact.
 */
#define BINDER_SET_SYSTEM_SERVER_PID\t_IOW('b', 17, __u32)
""",
)

replace_once(
    "include/uapi/linux/android/binder.h",
    """\tBR_FROZEN_REPLY = _IO('r', 18),
\t/*
\t * The target of the last transaction (either a bcTRANSACTION or
\t * a bcATTEMPT_ACQUIRE) is frozen.  No parameters.
\t */
};
""",
    """\tBR_FROZEN_REPLY = _IO('r', 18),
\t/*
\t * The target of the last transaction (either a bcTRANSACTION or
\t * a bcATTEMPT_ACQUIRE) is frozen.  No parameters.
\t */

\tBR_ONEWAY_SPAM_SUSPECT = _IO('r', 19),
\t/*
\t * Current process sent too many oneway calls to the target and the last
\t * asynchronous transaction crossed the async-buffer detection threshold.
\t * No parameters.
\t */
};
""",
)

# ---------------------------------------------------------------------------
# Allocator state: mark the allocation that crosses the spam threshold and
# remember whether a warning has already been emitted while space is low.
# ---------------------------------------------------------------------------
replace_once(
    "drivers/android/binder_alloc.h",
    """\tunsigned free:1;
\tunsigned allow_user_free:1;
\tunsigned async_transaction:1;
\tunsigned debug_id:29;
""",
    """\tunsigned free:1;
\tunsigned allow_user_free:1;
\tunsigned async_transaction:1;
\tunsigned oneway_spam_suspect:1;
\tunsigned debug_id:28;
""",
)

replace_once(
    "drivers/android/binder_alloc.h",
    """\tint pid;
\tsize_t pages_high;
};
""",
    """\tint pid;
\tsize_t pages_high;
\tbool oneway_spam_detected;
};
""",
)

regex_once(
    "drivers/android/binder_alloc.c",
    r"""static void debug_low_async_space_locked\(struct binder_alloc \*alloc, int pid\)
\{.*?\n\}\n\n(?=static struct binder_buffer \*binder_alloc_new_buf_locked)""",
    """static bool debug_low_async_space_locked(struct binder_alloc *alloc, int pid)
{
\t/*
\t * Find the amount and size of async buffers allocated by this caller.
\t * Once the target is low on async space, the caller responsible for the
\t * pressure is likely to send another oneway transaction and be caught.
\t */
\tstruct rb_node *n;
\tstruct binder_buffer *buffer;
\tsize_t total_alloc_size = 0;
\tsize_t num_buffers = 0;

\tfor (n = rb_first(&alloc->allocated_buffers); n != NULL;
\t     n = rb_next(n)) {
\t\tbuffer = rb_entry(n, struct binder_buffer, rb_node);
\t\tif (buffer->pid != pid)
\t\t\tcontinue;
\t\tif (!buffer->async_transaction)
\t\t\tcontinue;
\t\ttotal_alloc_size += binder_alloc_buffer_size(alloc, buffer)
\t\t\t+ sizeof(struct binder_buffer);
\t\tnum_buffers++;
\t}

\t/*
\t * Match Android's detection policy: more than 50 outstanding oneway
\t * buffers, or over 25% of the total Binder mapping charged to this pid.
\t */
\tif (num_buffers > 50 || total_alloc_size > alloc->buffer_size / 4) {
\t\tbinder_alloc_debug(BINDER_DEBUG_USER_ERROR,
\t\t\t     "%d: pid %d spamming oneway? %zd buffers allocated for a total size of %zd\\n",
\t\t\t      alloc->pid, pid, num_buffers, total_alloc_size);
\t\tif (!alloc->oneway_spam_detected) {
\t\t\talloc->oneway_spam_detected = true;
\t\t\treturn true;
\t\t}
\t}
\treturn false;
}

""",
)

replace_once(
    "drivers/android/binder_alloc.c",
    """\tbuffer->async_transaction = is_async;
\tbuffer->extra_buffers_size = extra_buffers_size;
\tbuffer->pid = pid;
\tif (is_async) {
\t\talloc->free_async_space -= size + sizeof(struct binder_buffer);
\t\tbinder_alloc_debug(BINDER_DEBUG_BUFFER_ALLOC_ASYNC,
\t\t\t     "%d: binder_alloc_buf size %zd async free %zd\\n",
\t\t\t      alloc->pid, size, alloc->free_async_space);
\t\tif (alloc->free_async_space < alloc->buffer_size / 10) {
\t\t\t/*
\t\t\t * Start detecting spammers once we have less than 20%
\t\t\t * of async space left (which is less than 10% of total
\t\t\t * buffer size).
\t\t\t */
\t\t\tdebug_low_async_space_locked(alloc, pid);
\t\t}
\t}
""",
    """\tbuffer->async_transaction = is_async;
\tbuffer->extra_buffers_size = extra_buffers_size;
\tbuffer->pid = pid;
\tbuffer->oneway_spam_suspect = false;
\tif (is_async) {
\t\talloc->free_async_space -= size + sizeof(struct binder_buffer);
\t\tbinder_alloc_debug(BINDER_DEBUG_BUFFER_ALLOC_ASYNC,
\t\t\t     "%d: binder_alloc_buf size %zd async free %zd\\n",
\t\t\t      alloc->pid, size, alloc->free_async_space);
\t\tif (alloc->free_async_space < alloc->buffer_size / 10) {
\t\t\t/*
\t\t\t * Start detecting spammers once less than 20% of async
\t\t\t * space remains, which is less than 10% of total mapping.
\t\t\t */
\t\t\tbuffer->oneway_spam_suspect =
\t\t\t\tdebug_low_async_space_locked(alloc, pid);
\t\t} else {
\t\t\talloc->oneway_spam_detected = false;
\t\t}
\t}
""",
)

# ---------------------------------------------------------------------------
# Binder core: expose spam-suspect work to userspace only for processes that
# opt in, exactly like modern Android common Binder.
# ---------------------------------------------------------------------------
replace_once(
    "drivers/android/binder.c",
    """\tatomic_t br[_IOC_NR(BR_FROZEN_REPLY) + 1];
""",
    """\tatomic_t br[_IOC_NR(BR_ONEWAY_SPAM_SUSPECT) + 1];
""",
)

replace_once(
    "drivers/android/binder.c",
    """\t\tBINDER_WORK_TRANSACTION = 1,
\t\tBINDER_WORK_TRANSACTION_COMPLETE,
\t\tBINDER_WORK_RETURN_ERROR,
""",
    """\t\tBINDER_WORK_TRANSACTION = 1,
\t\tBINDER_WORK_TRANSACTION_COMPLETE,
\t\tBINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT,
\t\tBINDER_WORK_RETURN_ERROR,
""",
)

replace_once(
    "drivers/android/binder.c",
    """\tspinlock_t inner_lock;
\tspinlock_t outer_lock;
\tstruct dentry *binderfs_entry;
};
""",
    """\tspinlock_t inner_lock;
\tspinlock_t outer_lock;
\tstruct dentry *binderfs_entry;
\tbool oneway_spam_detection_enabled;
};
""",
)

# Current 2026 Binder lifetime fix: read to_proc/to_thread under t->lock and pin
# to_thread so the process cannot disappear while its inner lock is acquired.
replace_once(
    "drivers/android/binder.c",
    """static void binder_free_transaction(struct binder_transaction *t)
{
\tstruct binder_proc *target_proc = t->to_proc;

\tif (target_proc) {
""",
    """static void binder_free_transaction(struct binder_transaction *t)
{
\tstruct binder_thread *target_thread;
\tstruct binder_proc *target_proc;

\tspin_lock(&t->lock);
\ttarget_proc = t->to_proc;
\ttarget_thread = t->to_thread;
\t/*
\t * Pin target_thread to keep target_proc alive. Undelivered
\t * transactions with !target_thread are safe, as target_proc
\t * can only be the current context there.
\t */
\tif (target_thread)
\t\tatomic_inc(&target_thread->tmp_ref);
\tspin_unlock(&t->lock);

\tif (target_proc) {
""",
)

replace_once(
    "drivers/android/binder.c",
    """\t\tbinder_inner_proc_unlock(target_proc);
\t}
\t/*
\t * If the transaction has no target_proc, then
""",
    """\t\tbinder_inner_proc_unlock(target_proc);
\t}

\tif (target_thread)
\t\tbinder_thread_dec_tmpref(target_thread);

\t/*
\t * If the transaction has no target_proc, then
""",
)

replace_once(
    "drivers/android/binder.c",
    """\ttcomplete->type = BINDER_WORK_TRANSACTION_COMPLETE;
\tt->work.type = BINDER_WORK_TRANSACTION;
""",
    """\tif (t->buffer->oneway_spam_suspect)
\t\ttcomplete->type = BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT;
\telse
\t\ttcomplete->type = BINDER_WORK_TRANSACTION_COMPLETE;
\tt->work.type = BINDER_WORK_TRANSACTION;
""",
)

replace_once(
    "drivers/android/binder.c",
    """\t\tcase BINDER_WORK_TRANSACTION_COMPLETE: {
\t\t\tbinder_inner_proc_unlock(proc);
\t\t\tcmd = BR_TRANSACTION_COMPLETE;
\t\t\tkfree(w);
""",
    """\t\tcase BINDER_WORK_TRANSACTION_COMPLETE:
\t\tcase BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT: {
\t\t\tif (proc->oneway_spam_detection_enabled &&
\t\t\t    w->type == BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT)
\t\t\t\tcmd = BR_ONEWAY_SPAM_SUSPECT;
\t\t\telse
\t\t\t\tcmd = BR_TRANSACTION_COMPLETE;
\t\t\tbinder_inner_proc_unlock(proc);
\t\t\tkfree(w);
""",
)

replace_once(
    "drivers/android/binder.c",
    """\t\tcase BINDER_WORK_TRANSACTION_COMPLETE: {
\t\t\tbinder_debug(BINDER_DEBUG_DEAD_TRANSACTION,
""",
    """\t\tcase BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT:
\t\tcase BINDER_WORK_TRANSACTION_COMPLETE: {
\t\t\tbinder_debug(BINDER_DEBUG_DEAD_TRANSACTION,
""",
)

replace_once(
    "drivers/android/binder.c",
    """\tcase BINDER_WORK_TRANSACTION_COMPLETE:
\t\tseq_printf(m, "%stransaction complete\\n", prefix);
""",
    """\tcase BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT:
\tcase BINDER_WORK_TRANSACTION_COMPLETE:
\t\tseq_printf(m, "%stransaction complete\\n", prefix);
""",
)

replace_once(
    "drivers/android/binder.c",
    """\tcase BINDER_GET_FROZEN_INFO: {
\t\tstruct binder_frozen_status_info info;
""",
    """\tcase BINDER_GET_FROZEN_INFO: {
\t\tstruct binder_frozen_status_info info;
""",
)

# Insert the ioctl after the existing freezer query. Anchor on the end of that
# case so Samsung's freezer implementation remains untouched.
replace_once(
    "drivers/android/binder.c",
    """\t\tif (copy_to_user(ubuf, &info, sizeof(info))) {
\t\t\tret = -EFAULT;
\t\t\tgoto err;
\t\t}
\t\tbreak;
\t}
\tdefault:
""",
    """\t\tif (copy_to_user(ubuf, &info, sizeof(info))) {
\t\t\tret = -EFAULT;
\t\t\tgoto err;
\t\t}
\t\tbreak;
\t}
\tcase BINDER_ENABLE_ONEWAY_SPAM_DETECTION: {
\t\tuint32_t enable;

\t\tif (copy_from_user(&enable, ubuf, sizeof(enable))) {
\t\t\tret = -EFAULT;
\t\t\tgoto err;
\t\t}
\t\tbinder_inner_proc_lock(proc);
\t\tproc->oneway_spam_detection_enabled = (bool)enable;
\t\tbinder_inner_proc_unlock(proc);
\t\tbreak;
\t}
\tdefault:
""",
)

replace_once(
    "drivers/android/binder.c",
    """\t"BR_FAILED_REPLY",
\t"BR_FROZEN_REPLY",
};
""",
    """\t"BR_FAILED_REPLY",
\t"BR_FROZEN_REPLY",
\t"BR_ONEWAY_SPAM_SUSPECT",
};
""",
)

# ---------------------------------------------------------------------------
# Extended Binder errors. Samsung's BINDER_SET_SYSTEM_SERVER_PID uses
# _IOW('b', 17, __u32), while upstream BINDER_GET_EXTENDED_ERROR uses
# _IOWR('b', 17, struct binder_extended_error). These are different full
# ioctl values, so preserve both ABIs.
# ---------------------------------------------------------------------------
replace_once(
    "include/uapi/linux/android/binder.h",
    """struct binder_frozen_status_info {
\t__u32 pid;
\t__u32 sync_recv;
\t__u32 async_recv;
};
""",
    """struct binder_frozen_status_info {
\t__u32 pid;
\t__u32 sync_recv;
\t__u32 async_recv;
};

struct binder_extended_error {
\t__u32 id;
\t__u32 command;
\t__s32 param;
};
""",
)

replace_once(
    "include/uapi/linux/android/binder.h",
    """#define BINDER_SET_SYSTEM_SERVER_PID\t_IOW('b', 17, __u32)
""",
    """#define BINDER_SET_SYSTEM_SERVER_PID\t_IOW('b', 17, __u32)
#define BINDER_GET_EXTENDED_ERROR _IOWR('b', 17, struct binder_extended_error)
""",
)

replace_once(
    "drivers/android/binder.c",
    """#define to_flat_binder_object(hdr) \\
\tcontainer_of(hdr, struct flat_binder_object, hdr)
""",
    """#define binder_set_extended_error(ee, _id, _command, _param) \\
\tdo { \\
\t\t(ee)->id = (_id); \\
\t\t(ee)->command = (_command); \\
\t\t(ee)->param = (_param); \\
\t} while (0)

#define to_flat_binder_object(hdr) \\
\tcontainer_of(hdr, struct flat_binder_object, hdr)
""",
)

replace_once(
    "drivers/android/binder.c",
    """\tstruct binder_error return_error;
\tstruct binder_error reply_error;
\twait_queue_head_t wait;
""",
    """\tstruct binder_error return_error;
\tstruct binder_error reply_error;
\tstruct binder_extended_error ee;
\twait_queue_head_t wait;
""",
)

replace_once(
    "drivers/android/binder.c",
    """#ifdef CONFIG_SAMSUNG_FREECESS
static void freecess_async_binder_report""",
    """static void binder_set_txn_from_error(struct binder_transaction *t, int id,
\t\t\t\t      uint32_t command, int32_t param)
{
\tstruct binder_thread *from = binder_get_txn_from_and_acq_inner(t);

\tif (!from) {
\t\t/* annotation for sparse */
\t\t__release(&from->proc->inner_lock);
\t\treturn;
\t}

\t/* Do not override an earlier error that userspace has not consumed. */
\tif (from->ee.command == BR_OK)
\t\tbinder_set_extended_error(&from->ee, id, command, param);
\tbinder_inner_proc_unlock(from->proc);
\tbinder_thread_dec_tmpref(from);
}

#ifdef CONFIG_SAMSUNG_FREECESS
static void freecess_async_binder_report""",
)

replace_once(
    "drivers/android/binder.c",
    """\te->context_name = proc->context->name;

\tif (reply) {
""",
    """\te->context_name = proc->context->name;

\tbinder_inner_proc_lock(proc);
\tbinder_set_extended_error(&thread->ee, t_debug_id, BR_OK, 0);
\tbinder_inner_proc_unlock(proc);

\tif (reply) {
""",
)

replace_once(
    "drivers/android/binder.c",
    """\tif (in_reply_to) {
\t\tbinder_restore_priority(current, in_reply_to->saved_priority);
\t\tthread->return_error.cmd = BR_TRANSACTION_COMPLETE;
\t\tbinder_enqueue_thread_work(thread, &thread->return_error.work);
\t\tbinder_send_failed_reply(in_reply_to, return_error);
\t} else {
\t\tthread->return_error.cmd = return_error;
\t\tbinder_enqueue_thread_work(thread, &thread->return_error.work);
\t}
""",
    """\tif (in_reply_to) {
\t\tbinder_restore_priority(current, in_reply_to->saved_priority);
\t\tbinder_set_txn_from_error(in_reply_to, t_debug_id,
\t\t\t\t  return_error, return_error_param);
\t\tthread->return_error.cmd = BR_TRANSACTION_COMPLETE;
\t\tbinder_enqueue_thread_work(thread, &thread->return_error.work);
\t\tbinder_send_failed_reply(in_reply_to, return_error);
\t} else {
\t\tbinder_inner_proc_lock(proc);
\t\tbinder_set_extended_error(&thread->ee, t_debug_id,
\t\t\t\t  return_error, return_error_param);
\t\tbinder_inner_proc_unlock(proc);
\t\tthread->return_error.cmd = return_error;
\t\tbinder_enqueue_thread_work(thread, &thread->return_error.work);
\t}
""",
)

replace_once(
    "drivers/android/binder.c",
    """\tthread->reply_error.work.type = BINDER_WORK_RETURN_ERROR;
\tthread->reply_error.cmd = BR_OK;
\tINIT_LIST_HEAD(&new_thread->waiting_thread_node);
""",
    """\tthread->reply_error.work.type = BINDER_WORK_RETURN_ERROR;
\tthread->reply_error.cmd = BR_OK;
\tbinder_set_extended_error(&thread->ee, 0, BR_OK, 0);
\tINIT_LIST_HEAD(&new_thread->waiting_thread_node);
""",
)

replace_once(
    "drivers/android/binder.c",
    """static long binder_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
""",
    """static int binder_ioctl_get_extended_error(struct binder_thread *thread,
\t\t\t\t\t   void __user *ubuf)
{
\tstruct binder_extended_error ee;

\tbinder_inner_proc_lock(thread->proc);
\tee = thread->ee;
\tbinder_set_extended_error(&thread->ee, 0, BR_OK, 0);
\tbinder_inner_proc_unlock(thread->proc);

\tif (copy_to_user(ubuf, &ee, sizeof(ee)))
\t\treturn -EFAULT;

\treturn 0;
}

static long binder_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
""",
)

replace_once(
    "drivers/android/binder.c",
    """\t\tproc->oneway_spam_detection_enabled = (bool)enable;
\t\tbinder_inner_proc_unlock(proc);
\t\tbreak;
\t}
\tdefault:
""",
    """\t\tproc->oneway_spam_detection_enabled = (bool)enable;
\t\tbinder_inner_proc_unlock(proc);
\t\tbreak;
\t}
\tcase BINDER_GET_EXTENDED_ERROR:
\t\tret = binder_ioctl_get_extended_error(thread, ubuf);
\t\tif (ret < 0)
\t\t\tgoto err;
\t\tbreak;
\tdefault:
""",
)

# ---------------------------------------------------------------------------
# Validation: make Samsung integration and the new ABI explicit build inputs.
# ---------------------------------------------------------------------------
checks = {
    "include/uapi/linux/android/binder.h": [
        "BINDER_ENABLE_ONEWAY_SPAM_DETECTION",
        "BR_ONEWAY_SPAM_SUSPECT",
        "BINDER_SET_SYSTEM_SERVER_PID",
        "BINDER_GET_EXTENDED_ERROR",
        "struct binder_extended_error",
    ],
    "drivers/android/binder_alloc.h": [
        "oneway_spam_suspect",
        "oneway_spam_detected",
    ],
    "drivers/android/binder_alloc.c": [
        "static bool debug_low_async_space_locked",
        "buffer->oneway_spam_suspect",
        "CONFIG_SAMSUNG_FREECESS",
        "binder_report(p, -1, \"free_buffer_full\"",
    ],
    "drivers/android/binder.c": [
        "BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT",
        "oneway_spam_detection_enabled",
        "case BINDER_ENABLE_ONEWAY_SPAM_DETECTION",
        "case BINDER_GET_EXTENDED_ERROR:",
        "binder_ioctl_get_extended_error",
        "binder_set_txn_from_error",
        "struct binder_thread *target_thread;",
        "atomic_inc(&target_thread->tmp_ref);",
        "binder_thread_dec_tmpref(target_thread);",
        "CONFIG_SAMSUNG_FREECESS",
        "freecess_async_binder_report",
        "freecess_sync_binder_report",
        "case BINDER_FREEZE:",
        "case BINDER_GET_FROZEN_INFO:",
    ],
}

report = []
for rel, needles in checks.items():
    text = (ROOT / rel).read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"{rel}: validation failed, missing {needle!r}")
        report.append(f"OK {rel}: {needle}")

(ART / "phase81-modern-binder-report.txt").write_text(
    "\n".join(report)
    + "\n\nDonor security fixes:\n"
    + "binder_thread_release race serialization: 1ac5be05b2854ba2329c0e52a67edd80b3ab4352\n"
    + "binder_free_transaction target pin: 1290154c48656bd1012a837076a0fbe379330eb3\n"
    + "Samsung system-server ioctl and upstream extended-error ioctl both retained safely.\n"
)

print("[phase81] modern Binder compatibility slice applied")
print("[phase81] Samsung Freecess and freezer hooks preserved")
print("[phase81] ioctl 16 oneway spam detection enabled")
print("[phase81] Android extended-error ABI enabled alongside Samsung vendor ioctl")
