#!/usr/bin/env python3
from pathlib import Path
import re
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()

def load(rel):
    p = root / rel
    if not p.is_file():
        raise SystemExit(f"missing file: {p}")
    return p, p.read_text()

def save(p, s):
    p.write_text(s)

def replace_once(s, old, new, what):
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{what}: expected exactly one anchor, found {n}")
    return s.replace(old, new, 1)

def require(s, needle, what):
    if needle not in s:
        raise SystemExit(f"{what}: required marker not found: {needle}")

# ---------------------------------------------------------------------------
# UAPI: add modern AOSP Binder features without removing Samsung freezer UAPI.
# The vendor BINDER_SET_SYSTEM_SERVER_PID uses ioctl nr 17 too, but the full
# ioctl value differs because direction and structure size are encoded.
# ---------------------------------------------------------------------------
p, s = load("include/uapi/linux/android/binder.h")

if "struct binder_extended_error" not in s:
    m = re.search(r"(struct binder_frozen_status_info\s*\{.*?\n\};)", s, re.S)
    if not m:
        raise SystemExit("binder UAPI: binder_frozen_status_info anchor missing")
    ext = r'''\1

/* Extended error information for the most recent failed Binder operation. */
struct binder_extended_error {
	__u32 id;
	__u32 command;
	__s32 param;
};'''
    s = s[:m.start()] + re.sub(
        r"(struct binder_frozen_status_info\s*\{.*?\n\};)",
        ext,
        s[m.start():m.end()],
        count=1,
        flags=re.S,
    ) + s[m.end():]

if "BINDER_ENABLE_ONEWAY_SPAM_DETECTION" not in s:
    anchor = "#define BINDER_GET_FROZEN_INFO _IOWR('b', 15, struct binder_frozen_status_info)"
    require(s, anchor, "binder UAPI freezer ioctl")
    s = s.replace(
        anchor,
        anchor +
        "\n#define BINDER_ENABLE_ONEWAY_SPAM_DETECTION _IOW('b', 16, __u32)" +
        "\n#define BINDER_GET_EXTENDED_ERROR _IOWR('b', 17, struct binder_extended_error)",
        1,
    )

if "TF_CLEAR_BUF" not in s:
    s = replace_once(
        s,
        "\tTF_ACCEPT_FDS\t= 0x10,\t/* allow replies with file descriptors */",
        "\tTF_ACCEPT_FDS\t= 0x10,\t/* allow replies with file descriptors */\n"
        "\tTF_CLEAR_BUF\t= 0x20,\t/* clear buffer after transaction */",
        "binder UAPI TF_CLEAR_BUF",
    )

if "BR_ONEWAY_SPAM_SUSPECT" not in s:
    pos = s.find("BR_FROZEN_REPLY = _IO('r', 18)")
    if pos < 0:
        raise SystemExit("binder UAPI: BR_FROZEN_REPLY missing")
    end = s.find("\n};", pos)
    if end < 0:
        raise SystemExit("binder UAPI: return protocol enum terminator missing")
    addition = (
        "\n\tBR_ONEWAY_SPAM_SUSPECT = _IO('r', 19),\n"
        "\t/*\n"
        "\t * Current process sent too many one-way calls to a target.\n"
        "\t * No parameters.\n"
        "\t */"
    )
    s = s[:end] + addition + s[end:]

require(s, "#define BINDER_SET_SYSTEM_SERVER_PID", "Samsung system_server Binder ioctl")
save(p, s)

# ---------------------------------------------------------------------------
# Allocator metadata and clear-on-free support.
# ---------------------------------------------------------------------------
p, s = load("drivers/android/binder_alloc.h")
if "oneway_spam_suspect" not in s:
    old = (
        "\tunsigned free:1;\n"
        "\tunsigned allow_user_free:1;\n"
        "\tunsigned async_transaction:1;\n"
        "\tunsigned debug_id:29;"
    )
    new = (
        "\tunsigned free:1;\n"
        "\tunsigned clear_on_free:1;\n"
        "\tunsigned allow_user_free:1;\n"
        "\tunsigned async_transaction:1;\n"
        "\tunsigned oneway_spam_suspect:1;\n"
        "\tunsigned debug_id:27;"
    )
    s = replace_once(s, old, new, "binder_alloc buffer flags")

if "bool oneway_spam_detected;" not in s:
    s = replace_once(
        s,
        "\tsize_t pages_high;\n};",
        "\tsize_t pages_high;\n\tbool oneway_spam_detected;\n};",
        "binder_alloc spam latch",
    )
save(p, s)

p, s = load("drivers/android/binder_alloc.c")

# Replace the vendor logging-only detector with the AOSP one-shot detector.
start = s.find("static void debug_low_async_space_locked")
if start < 0:
    start = s.find("static bool debug_low_async_space_locked")
if start < 0:
    raise SystemExit("binder_alloc: low async helper missing")
end = s.find("static struct binder_buffer *binder_alloc_new_buf_locked", start)
if end < 0:
    raise SystemExit("binder_alloc: new_buf_locked anchor missing")

helper = r'''static bool debug_low_async_space_locked(struct binder_alloc *alloc, int pid)
{
	struct rb_node *n;
	struct binder_buffer *buffer;
	size_t total_alloc_size = 0;
	size_t num_buffers = 0;

	/*
	 * Report only once while async space remains unhealthy. Rearm after
	 * it climbs back above the 10% total-buffer threshold.
	 */
	if (alloc->free_async_space >= alloc->buffer_size / 10) {
		alloc->oneway_spam_detected = false;
		return false;
	}

	for (n = rb_first(&alloc->allocated_buffers); n != NULL;
	     n = rb_next(n)) {
		buffer = rb_entry(n, struct binder_buffer, rb_node);
		if (buffer->pid != pid)
			continue;
		if (!buffer->async_transaction)
			continue;
		total_alloc_size += binder_alloc_buffer_size(alloc, buffer);
		num_buffers++;
	}

	if (num_buffers > 50 || total_alloc_size > alloc->buffer_size / 4) {
		binder_alloc_debug(BINDER_DEBUG_USER_ERROR,
			"%d: pid %d spamming oneway? %zd buffers allocated for a total size of %zd\n",
			alloc->pid, pid, num_buffers, total_alloc_size);
		if (!alloc->oneway_spam_detected) {
			alloc->oneway_spam_detected = true;
			return true;
		}
	}

	return false;
}

'''
s = s[:start] + helper + s[end:]

if "buffer->oneway_spam_suspect = false;" not in s:
    s = replace_once(
        s,
        "\tbuffer->async_transaction = is_async;\n\tbuffer->extra_buffers_size = extra_buffers_size;",
        "\tbuffer->async_transaction = is_async;\n"
        "\tbuffer->clear_on_free = false;\n"
        "\tbuffer->oneway_spam_suspect = false;\n"
        "\tbuffer->extra_buffers_size = extra_buffers_size;",
        "binder_alloc initialize modern buffer flags",
    )

old_low = '''		if (alloc->free_async_space < alloc->buffer_size / 10) {
			/*
			 * Start detecting spammers once we have less than 20%
			 * of async space left (which is less than 10% of total
			 * buffer size).
			 */
			debug_low_async_space_locked(alloc, pid);
		}
'''
new_low = '''		if (alloc->free_async_space < alloc->buffer_size / 10)
			buffer->oneway_spam_suspect =
				debug_low_async_space_locked(alloc, pid);
		else
			alloc->oneway_spam_detected = false;
'''
if old_low in s:
    s = s.replace(old_low, new_low, 1)
elif "buffer->oneway_spam_suspect =" not in s:
    raise SystemExit("binder_alloc: async spam threshold block missing")

if "static void binder_alloc_clear_buf" not in s:
    anchor = "/**\n * binder_alloc_free_buf() - free a binder buffer"
    require(s, anchor, "binder_alloc free doc anchor")
    clear_helper = r'''static void binder_alloc_clear_buf(struct binder_alloc *alloc,
				   struct binder_buffer *buffer)
{
	size_t bytes = binder_alloc_buffer_size(alloc, buffer);
	binder_size_t buffer_offset = 0;

	while (bytes) {
		binder_size_t space_offset;
		unsigned long size;
		struct binder_lru_page *lru_page;
		struct page *page;
		pgoff_t pgoff;
		size_t index;
		void *kptr;

		space_offset = buffer_offset +
			((uintptr_t)buffer->user_data - (uintptr_t)alloc->buffer);
		pgoff = space_offset & ~PAGE_MASK;
		index = space_offset >> PAGE_SHIFT;
		lru_page = &alloc->pages[index];
		page = lru_page->page_ptr;
		BUG_ON(!page);
		size = min_t(size_t, bytes, PAGE_SIZE - pgoff);
		kptr = kmap(page);
		memset((char *)kptr + pgoff, 0, size);
		kunmap(page);
		bytes -= size;
		buffer_offset += size;
	}
}

'''
    s = s.replace(anchor, clear_helper + anchor, 1)

old_free = '''void binder_alloc_free_buf(struct binder_alloc *alloc,
			    struct binder_buffer *buffer)
{
	mutex_lock(&alloc->mutex);
	binder_free_buf_locked(alloc, buffer);
	mutex_unlock(&alloc->mutex);
}
'''
new_free = '''void binder_alloc_free_buf(struct binder_alloc *alloc,
			    struct binder_buffer *buffer)
{
	if (buffer->clear_on_free) {
		binder_alloc_clear_buf(alloc, buffer);
		buffer->clear_on_free = false;
	}
	mutex_lock(&alloc->mutex);
	binder_free_buf_locked(alloc, buffer);
	mutex_unlock(&alloc->mutex);
}
'''
if old_free in s:
    s = s.replace(old_free, new_free, 1)
elif "if (buffer->clear_on_free)" not in s:
    raise SystemExit("binder_alloc: free buffer body anchor missing")

old_deferred = '''		/* Transaction should already have been freed */
		BUG_ON(buffer->transaction);

		binder_free_buf_locked(alloc, buffer);
'''
new_deferred = '''		/* Transaction should already have been freed */
		BUG_ON(buffer->transaction);
		if (buffer->clear_on_free) {
			binder_alloc_clear_buf(alloc, buffer);
			buffer->clear_on_free = false;
		}

		binder_free_buf_locked(alloc, buffer);
'''
if old_deferred in s:
    s = s.replace(old_deferred, new_deferred, 1)

save(p, s)

# ---------------------------------------------------------------------------
# Binder core.
# ---------------------------------------------------------------------------
p, s = load("drivers/android/binder.c")

if "#define binder_set_extended_error" not in s:
    anchor = "#define to_flat_binder_object(hdr)"
    require(s, anchor, "binder extended error macro anchor")
    macro = r'''#define binder_set_extended_error(ee, _id, _command, _param) \
	do { \
		(ee)->id = (_id); \
		(ee)->command = (_command); \
		(ee)->param = (_param); \
	} while (0)

'''
    s = s.replace(anchor, macro + anchor, 1)

s = s.replace(
    "atomic_t br[_IOC_NR(BR_FROZEN_REPLY) + 1];",
    "atomic_t br[_IOC_NR(BR_ONEWAY_SPAM_SUSPECT) + 1];",
    1,
)

if "BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT" not in s:
    s = replace_once(
        s,
        "\t\tBINDER_WORK_TRANSACTION_COMPLETE,\n\t\tBINDER_WORK_RETURN_ERROR,",
        "\t\tBINDER_WORK_TRANSACTION_COMPLETE,\n"
        "\t\tBINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT,\n"
        "\t\tBINDER_WORK_RETURN_ERROR,",
        "binder work type spam suspect",
    )

if "oneway_spam_detection_enabled" not in s:
    s = replace_once(
        s,
        "\tstruct dentry *binderfs_entry;\n};",
        "\tstruct dentry *binderfs_entry;\n"
        "\tbool oneway_spam_detection_enabled;\n};",
        "binder proc spam opt-in",
    )

if "struct binder_extended_error ee;" not in s:
    s = replace_once(
        s,
        "\tstruct binder_error return_error;\n\tstruct binder_error reply_error;\n\twait_queue_head_t wait;",
        "\tstruct binder_error return_error;\n"
        "\tstruct binder_error reply_error;\n"
        "\tstruct binder_extended_error ee;\n"
        "\twait_queue_head_t wait;",
        "binder thread extended error",
    )

# Clear stale extended errors at the beginning of each transaction.
if "binder_set_extended_error(&thread->ee, t_debug_id, BR_OK, 0);" not in s:
    anchor = "\te->context_name = proc->context->name;\n"
    require(s, anchor, "binder transaction log context anchor")
    s = s.replace(
        anchor,
        anchor +
        "\n\tbinder_inner_proc_lock(proc);\n"
        "\tbinder_set_extended_error(&thread->ee, t_debug_id, BR_OK, 0);\n"
        "\tbinder_inner_proc_unlock(proc);\n",
        1,
    )

# Preserve private transaction contents when userspace asks for it.
if "t->buffer->clear_on_free = !!(t->flags & TF_CLEAR_BUF);" not in s:
    s = replace_once(
        s,
        "\tt->buffer->transaction = t;\n\tt->buffer->target_node = target_node;",
        "\tt->buffer->transaction = t;\n"
        "\tt->buffer->target_node = target_node;\n"
        "\tt->buffer->clear_on_free = !!(t->flags & TF_CLEAR_BUF);",
        "binder TF_CLEAR_BUF integration",
    )

# Complete work becomes the spam notification only for the threshold-crossing txn.
old_complete_assign = "\ttcomplete->type = BINDER_WORK_TRANSACTION_COMPLETE;\n\tt->work.type = BINDER_WORK_TRANSACTION;"
if old_complete_assign in s:
    s = s.replace(
        old_complete_assign,
        "\tif (t->buffer->oneway_spam_suspect)\n"
        "\t\ttcomplete->type = BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT;\n"
        "\telse\n"
        "\t\ttcomplete->type = BINDER_WORK_TRANSACTION_COMPLETE;\n"
        "\tt->work.type = BINDER_WORK_TRANSACTION;",
        1,
    )
elif "tcomplete->type = BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT" not in s:
    raise SystemExit("binder transaction complete assignment anchor missing")

# Report an extended error to the thread that originated a failed synchronous txn.
if "static void binder_set_txn_from_error" not in s:
    anchor = "#ifdef CONFIG_SAMSUNG_FREECESS"
    pos = s.find(anchor, s.find("static struct binder_node *binder_get_node_refs_for_txn"))
    if pos < 0:
        raise SystemExit("binder set_txn_from_error insertion anchor missing")
    helper = r'''static void binder_set_txn_from_error(struct binder_transaction *t, int id,
				      uint32_t command, int32_t param)
{
	struct binder_thread *from = binder_get_txn_from_and_acq_inner(t);

	if (!from)
		return;

	if (from->ee.command == BR_OK)
		binder_set_extended_error(&from->ee, id, command, param);
	binder_inner_proc_unlock(from->proc);
	binder_thread_dec_tmpref(from);
}

'''
    s = s[:pos] + helper + s[pos:]

old_reply_error = '''	if (in_reply_to) {
		binder_restore_priority(current, in_reply_to->saved_priority);
		thread->return_error.cmd = BR_TRANSACTION_COMPLETE;
'''
if old_reply_error in s and "binder_set_txn_from_error(in_reply_to" not in s:
    s = s.replace(
        old_reply_error,
        '''	if (in_reply_to) {
		binder_restore_priority(current, in_reply_to->saved_priority);
		binder_set_txn_from_error(in_reply_to, t_debug_id,
				return_error, return_error_param);
		thread->return_error.cmd = BR_TRANSACTION_COMPLETE;
''',
        1,
    )

old_direct_error = '''	} else {
		thread->return_error.cmd = return_error;
		binder_enqueue_thread_work(thread, &thread->return_error.work);
	}
}
'''
if old_direct_error in s and "binder_set_extended_error(&thread->ee, t_debug_id" in s:
    # This anchor is at the end of binder_transaction. Add direct failure state.
    s = s.replace(
        old_direct_error,
        '''	} else {
		binder_inner_proc_lock(proc);
		binder_set_extended_error(&thread->ee, t_debug_id,
				return_error, return_error_param);
		binder_inner_proc_unlock(proc);
		thread->return_error.cmd = return_error;
		binder_enqueue_thread_work(thread, &thread->return_error.work);
	}
}
''',
        1,
    )

# Return BR_ONEWAY_SPAM_SUSPECT only to processes that explicitly enable it.
old_read = '''		case BINDER_WORK_TRANSACTION_COMPLETE: {
			binder_inner_proc_unlock(proc);
			cmd = BR_TRANSACTION_COMPLETE;
			kfree(w);
'''
new_read = '''		case BINDER_WORK_TRANSACTION_COMPLETE:
		case BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT: {
			if (proc->oneway_spam_detection_enabled &&
			    w->type == BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT)
				cmd = BR_ONEWAY_SPAM_SUSPECT;
			else
				cmd = BR_TRANSACTION_COMPLETE;
			binder_inner_proc_unlock(proc);
			kfree(w);
'''
if old_read in s:
    s = s.replace(old_read, new_read, 1)
elif "case BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT" not in s:
    raise SystemExit("binder thread read complete-work anchor missing")

# Make cleanup treat a spam-suspect completion exactly like a normal completion.
needle = '''		case BINDER_WORK_TRANSACTION_COMPLETE: {
			binder_debug(BINDER_DEBUG_DEAD_TRANSACTION,
				"undelivered TRANSACTION_COMPLETE\\n");
'''
if needle in s:
    s = s.replace(
        needle,
        '''		case BINDER_WORK_TRANSACTION_COMPLETE:
		case BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT: {
			binder_debug(BINDER_DEBUG_DEAD_TRANSACTION,
				"undelivered TRANSACTION_COMPLETE\\n");
''',
        1,
    )

# Initialize the per-thread extended error state.
if "binder_set_extended_error(&thread->ee, 0, BR_OK, 0);" not in s:
    anchor = "\tthread->reply_error.cmd = BR_OK;\n"
    require(s, anchor, "binder thread initialization anchor")
    s = s.replace(
        anchor,
        anchor + "\tbinder_set_extended_error(&thread->ee, 0, BR_OK, 0);\n",
        1,
    )

# Add modern extended error ioctl helper before binder_ioctl.
if "static int binder_ioctl_get_extended_error" not in s:
    anchor = "static long binder_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)"
    require(s, anchor, "binder ioctl function anchor")
    helper = r'''static int binder_ioctl_get_extended_error(struct binder_thread *thread,
					   void __user *ubuf)
{
	struct binder_extended_error ee;

	binder_inner_proc_lock(thread->proc);
	ee = thread->ee;
	binder_set_extended_error(&thread->ee, 0, BR_OK, 0);
	binder_inner_proc_unlock(thread->proc);

	if (copy_to_user(ubuf, &ee, sizeof(ee)))
		return -EFAULT;

	return 0;
}

'''
    s = s.replace(anchor, helper + anchor, 1)

if "case BINDER_ENABLE_ONEWAY_SPAM_DETECTION:" not in s:
    anchor = "\tdefault:\n\t\tret = -EINVAL;"
    require(s, anchor, "binder ioctl default anchor")
    cases = r'''	case BINDER_ENABLE_ONEWAY_SPAM_DETECTION: {
		uint32_t enable;

		if (copy_from_user(&enable, ubuf, sizeof(enable))) {
			ret = -EFAULT;
			goto err;
		}
		binder_inner_proc_lock(proc);
		proc->oneway_spam_detection_enabled = (bool)enable;
		binder_inner_proc_unlock(proc);
		break;
	}
	case BINDER_GET_EXTENDED_ERROR:
		ret = binder_ioctl_get_extended_error(thread, ubuf);
		if (ret < 0)
			goto err;
		break;
'''
    s = s.replace(anchor, cases + anchor, 1)

save(p, s)

# ---------------------------------------------------------------------------
# Final source-level audit.
# ---------------------------------------------------------------------------
checks = {
    "include/uapi/linux/android/binder.h": [
        "TF_CLEAR_BUF",
        "BINDER_ENABLE_ONEWAY_SPAM_DETECTION",
        "BINDER_GET_EXTENDED_ERROR",
        "BR_ONEWAY_SPAM_SUSPECT",
        "BINDER_SET_SYSTEM_SERVER_PID",
    ],
    "drivers/android/binder_alloc.h": [
        "clear_on_free",
        "oneway_spam_suspect",
        "oneway_spam_detected",
    ],
    "drivers/android/binder_alloc.c": [
        "binder_alloc_clear_buf",
        "debug_low_async_space_locked",
        "oneway_spam_suspect",
    ],
    "drivers/android/binder.c": [
        "binder_set_extended_error",
        "BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT",
        "oneway_spam_detection_enabled",
        "binder_ioctl_get_extended_error",
        "t->buffer->clear_on_free = !!(t->flags & TF_CLEAR_BUF);",
    ],
}

for rel, markers in checks.items():
    data = (root / rel).read_text()
    for marker in markers:
        require(data, marker, rel)

print("Android Binder mainline Phase 1 backport applied successfully")
