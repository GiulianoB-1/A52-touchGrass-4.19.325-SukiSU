#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else "workspace/touchgrass-a52xq").resolve()

def load(rel):
    p = root / rel
    if not p.is_file():
        raise SystemExit(f"missing file: {p}")
    return p, p.read_text()

def save(p, s):
    p.write_text(s)

def require(s, needle, what):
    if needle not in s:
        raise SystemExit(f"{what}: required marker not found: {needle}")

def replace_once(s, old, new, what):
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{what}: expected exactly one anchor, found {n}")
    return s.replace(old, new, 1)

# This phase is intentionally based on Phase 81 output.
p, s = load("include/uapi/linux/android/binder.h")
require(s, "TF_CLEAR_BUF", "Phase 81 UAPI")
require(s, "BR_ONEWAY_SPAM_SUSPECT", "Phase 81 UAPI")

if "TF_UPDATE_TXN" not in s:
    s = replace_once(
        s,
        "\tTF_CLEAR_BUF\t= 0x20,\t/* clear buffer after transaction */",
        "\tTF_CLEAR_BUF\t= 0x20,\t/* clear buffer after transaction */\n"
        "\tTF_UPDATE_TXN\t= 0x40,\t/* update the outdated pending async txn */",
        "binder UAPI TF_UPDATE_TXN",
    )

if "BR_TRANSACTION_PENDING_FROZEN" not in s:
    pos = s.find("BR_ONEWAY_SPAM_SUSPECT = _IO('r', 19)")
    if pos < 0:
        raise SystemExit("binder UAPI: BR_ONEWAY_SPAM_SUSPECT missing")
    end = s.find("\n};", pos)
    if end < 0:
        raise SystemExit("binder UAPI: return protocol terminator missing")
    addition = (
        "\n\tBR_TRANSACTION_PENDING_FROZEN = _IO('r', 20),\n"
        "\t/*\n"
        "\t * The target of the last async transaction is frozen.\n"
        "\t * The transaction remains queued until the target thaws.\n"
        "\t */"
    )
    s = s[:end] + addition + s[end:]

save(p, s)

p, s = load("drivers/android/binder.c")
require(s, "BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT", "Phase 81 binder work type")
require(s, "Phase81_UAF_TARGET_THREAD_PIN", "Phase 81 Binder UAF hardening")

s = s.replace(
    "atomic_t br[_IOC_NR(BR_ONEWAY_SPAM_SUSPECT) + 1];",
    "atomic_t br[_IOC_NR(BR_TRANSACTION_PENDING_FROZEN) + 1];",
    1,
)

if "BINDER_WORK_TRANSACTION_PENDING" not in s:
    s = replace_once(
        s,
        "\t\tBINDER_WORK_TRANSACTION_COMPLETE,\n"
        "\t\tBINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT,",
        "\t\tBINDER_WORK_TRANSACTION_COMPLETE,\n"
        "\t\tBINDER_WORK_TRANSACTION_PENDING,\n"
        "\t\tBINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT,",
        "binder pending-frozen work type",
    )

# Add the current Android rules for replacing stale updateable async txns.
if "static bool binder_can_update_transaction" not in s:
    anchor = "/**\n * binder_proc_transaction() - sends a transaction to a process and wakes it up"
    require(s, anchor, "binder_proc_transaction documentation")
    helpers = r'''/**
 * binder_can_update_transaction() - can a pending txn be superseded?
 * @t1: old pending async transaction in the frozen process
 * @t2: new async transaction that may supersede it
 */
static bool binder_can_update_transaction(struct binder_transaction *t1,
					  struct binder_transaction *t2)
{
	if ((t1->flags & t2->flags & (TF_ONE_WAY | TF_UPDATE_TXN)) !=
	    (TF_ONE_WAY | TF_UPDATE_TXN) || !t1->to_proc || !t2->to_proc)
		return false;

	if (t1->to_proc->tsk == t2->to_proc->tsk &&
	    t1->code == t2->code &&
	    t1->flags == t2->flags &&
	    t1->buffer->pid == t2->buffer->pid &&
	    t1->buffer->target_node->ptr == t2->buffer->target_node->ptr &&
	    t1->buffer->target_node->cookie == t2->buffer->target_node->cookie)
		return true;

	return false;
}

static struct binder_transaction *
binder_find_outdated_transaction_ilocked(struct binder_transaction *t,
					 struct list_head *target_list)
{
	struct binder_work *w;

	list_for_each_entry(w, target_list, entry) {
		struct binder_transaction *queued;

		if (w->type != BINDER_WORK_TRANSACTION)
			continue;
		queued = container_of(w, struct binder_transaction, work);
		if (binder_can_update_transaction(queued, t))
			return queued;
	}

	return NULL;
}

'''
    s = s.replace(anchor, helpers + anchor, 1)

# Replace only the vendor Phase 80/81 implementation. Keep Samsung priority
# handling while adopting current Android frozen-async behavior.
start = s.find("static int binder_proc_transaction(struct binder_transaction *t,")
if start < 0:
    raise SystemExit("binder_proc_transaction definition missing")
end = s.find("\n/**\n * binder_get_node_refs_for_txn()", start)
if end < 0:
    raise SystemExit("binder_proc_transaction end anchor missing")

old_func = s[start:end]
if "BR_TRANSACTION_PENDING_FROZEN" not in old_func:
    new_func = r'''static int binder_proc_transaction(struct binder_transaction *t,
				    struct binder_proc *proc,
				    struct binder_thread *thread)
{
	struct binder_node *node = t->buffer->target_node;
	struct binder_priority node_prio;
	bool oneway = !!(t->flags & TF_ONE_WAY);
	bool pending_async = false;
	struct binder_transaction *t_outdated = NULL;
	bool frozen = false;

	BUG_ON(!node);
	binder_node_lock(node);
	node_prio.prio = node->min_priority;
	node_prio.sched_policy = node->sched_policy;

	if (oneway) {
		BUG_ON(thread);
		if (node->has_async_transaction)
			pending_async = true;
		else
			node->has_async_transaction = true;
	}

	binder_inner_proc_lock(proc);
	if (proc->is_frozen) {
		frozen = true;
		proc->sync_recv |= !oneway;
		proc->async_recv |= oneway;
	}

	if ((frozen && !oneway) || proc->is_dead ||
	    (thread && thread->is_dead)) {
		binder_inner_proc_unlock(proc);
		binder_node_unlock(node);
		return frozen ? BR_FROZEN_REPLY : BR_DEAD_REPLY;
	}

	if (!thread && !pending_async)
		thread = binder_select_thread_ilocked(proc);

	if (thread) {
		binder_transaction_priority(thread->task, t, node_prio,
					    node->inherit_rt);
		binder_enqueue_thread_work_ilocked(thread, &t->work);
	} else if (!pending_async) {
		binder_enqueue_work_ilocked(&t->work, &proc->todo);
	} else {
		if ((t->flags & TF_UPDATE_TXN) && frozen) {
			t_outdated = binder_find_outdated_transaction_ilocked(
					t, &node->async_todo);
			if (t_outdated) {
				binder_debug(BINDER_DEBUG_TRANSACTION,
					     "txn %d supersedes %d\n",
					     t->debug_id, t_outdated->debug_id);
				list_del_init(&t_outdated->work.entry);
				proc->outstanding_txns--;
			}
		}
		binder_enqueue_work_ilocked(&t->work, &node->async_todo);
	}

	if (!pending_async)
		binder_wakeup_thread_ilocked(proc, thread, !oneway);

	proc->outstanding_txns++;
	binder_inner_proc_unlock(proc);
	binder_node_unlock(node);

	/*
	 * The old queued transaction is no longer reachable by Binder worklists.
	 * Release its translated objects and allocator buffer outside the locks.
	 */
	if (t_outdated) {
		struct binder_buffer *buffer = t_outdated->buffer;

		t_outdated->buffer = NULL;
		buffer->transaction = NULL;
		binder_transaction_buffer_release(proc, buffer, 0, false);
		binder_alloc_free_buf(&proc->alloc, buffer);
		kfree(t_outdated);
		binder_stats_deleted(BINDER_STAT_TRANSACTION);
	}

	if (oneway && frozen)
		return BR_TRANSACTION_PENDING_FROZEN;

	return 0;
}
'''
    s = s[:start] + new_func + s[end:]

# The async sender gets a distinct completion when the transaction is parked
# behind a frozen target. Pending-frozen is not a transaction failure.
old_async = '''	} else {
		BUG_ON(target_node == NULL);
		BUG_ON(t->buffer->async_transaction != 1);
		binder_enqueue_thread_work(thread, tcomplete);
		return_error = binder_proc_transaction(t, target_proc, NULL);
		if (return_error)
			goto err_dead_proc_or_thread;
	}
'''
new_async = '''	} else {
		BUG_ON(target_node == NULL);
		BUG_ON(t->buffer->async_transaction != 1);
		return_error = binder_proc_transaction(t, target_proc, NULL);
		if (return_error == BR_TRANSACTION_PENDING_FROZEN)
			tcomplete->type = BINDER_WORK_TRANSACTION_PENDING;
		binder_enqueue_thread_work(thread, tcomplete);
		if (return_error &&
		    return_error != BR_TRANSACTION_PENDING_FROZEN)
			goto err_dead_proc_or_thread;
	}
'''
if old_async in s:
    s = s.replace(old_async, new_async, 1)
elif "tcomplete->type = BINDER_WORK_TRANSACTION_PENDING;" not in s:
    raise SystemExit("binder async transaction completion anchor missing")

# Return the new completion to userspace while retaining Phase 81 spam opt-in.
old_read = '''		case BINDER_WORK_TRANSACTION_COMPLETE:
		case BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT: {
			if (proc->oneway_spam_detection_enabled &&
			    w->type == BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT)
				cmd = BR_ONEWAY_SPAM_SUSPECT;
			else
				cmd = BR_TRANSACTION_COMPLETE;
'''
new_read = '''		case BINDER_WORK_TRANSACTION_COMPLETE:
		case BINDER_WORK_TRANSACTION_PENDING:
		case BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT: {
			if (proc->oneway_spam_detection_enabled &&
			    w->type == BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT)
				cmd = BR_ONEWAY_SPAM_SUSPECT;
			else if (w->type == BINDER_WORK_TRANSACTION_PENDING)
				cmd = BR_TRANSACTION_PENDING_FROZEN;
			else
				cmd = BR_TRANSACTION_COMPLETE;
'''
if old_read in s:
    s = s.replace(old_read, new_read, 1)
elif "case BINDER_WORK_TRANSACTION_PENDING:" not in s[s.find("static int binder_thread_read"):]:
    raise SystemExit("binder thread read pending-work anchor missing")

# Work cleanup treats a pending completion exactly like a normal completion.
old_release = '''		case BINDER_WORK_TRANSACTION_COMPLETE:
		case BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT: {
'''
new_release = '''		case BINDER_WORK_TRANSACTION_COMPLETE:
		case BINDER_WORK_TRANSACTION_PENDING:
		case BINDER_WORK_TRANSACTION_ONEWAY_SPAM_SUSPECT: {
'''
# There can be one read switch and one release switch. The read switch has
# already been expanded above, so only replace a remaining two-case block.
if old_release in s:
    s = s.replace(old_release, new_release, 1)

save(p, s)

checks = {
    "include/uapi/linux/android/binder.h": [
        "TF_UPDATE_TXN",
        "BR_TRANSACTION_PENDING_FROZEN",
    ],
    "drivers/android/binder.c": [
        "BINDER_WORK_TRANSACTION_PENDING",
        "binder_can_update_transaction",
        "binder_find_outdated_transaction_ilocked",
        "tcomplete->type = BINDER_WORK_TRANSACTION_PENDING;",
        "return BR_TRANSACTION_PENDING_FROZEN;",
    ],
}

for rel, markers in checks.items():
    data = (root / rel).read_text()
    for marker in markers:
        require(data, marker, rel)

print("Android Binder mainline Phase 2 frozen async backport applied successfully")
