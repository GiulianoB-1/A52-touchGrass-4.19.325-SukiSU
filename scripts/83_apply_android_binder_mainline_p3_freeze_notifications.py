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

def require(s, needle, what):
    if needle not in s:
        raise SystemExit(f"{what}: required marker not found: {needle}")

def replace_once(s, old, new, what):
    n = s.count(old)
    if n != 1:
        raise SystemExit(f"{what}: expected exactly one anchor, found {n}")
    return s.replace(old, new, 1)

# Phase 83 is layered on Phase 82.
p, s = load("include/uapi/linux/android/binder.h")
require(s, "TF_UPDATE_TXN", "Phase 82 UAPI")
require(s, "BR_TRANSACTION_PENDING_FROZEN", "Phase 82 UAPI")

if "struct binder_frozen_state_info" not in s:
    anchor = "struct binder_extended_error {"
    idx = s.find(anchor)
    if idx < 0:
        raise SystemExit("binder UAPI: extended error struct anchor missing")
    state = """struct binder_frozen_state_info {
	binder_uintptr_t cookie;
	__u32            is_frozen;
	__u32            reserved;
};

"""
    s = s[:idx] + state + s[idx:]

if "BR_FROZEN_BINDER" not in s:
    pos = s.find("BR_TRANSACTION_PENDING_FROZEN = _IO('r', 20)")
    if pos < 0:
        raise SystemExit("binder UAPI: pending frozen return missing")
    end = s.find("\n};", pos)
    if end < 0:
        raise SystemExit("binder UAPI: return enum terminator missing")
    addition = (
        "\n\tBR_FROZEN_BINDER = _IOR('r', 21, struct binder_frozen_state_info),\n"
        "\t/* cookie + frozen state transition */\n"
        "\n\tBR_CLEAR_FREEZE_NOTIFICATION_DONE = _IOR('r', 22, binder_uintptr_t),\n"
        "\t/* cookie for the cleared notification */"
    )
    s = s[:end] + addition + s[end:]

if "BC_REQUEST_FREEZE_NOTIFICATION" not in s:
    pos = s.find("BC_REPLY_SG")
    if pos < 0:
        raise SystemExit("binder UAPI: BC_REPLY_SG missing")
    end = s.find("\n};", pos)
    if end < 0:
        raise SystemExit("binder UAPI: command enum terminator missing")
    addition = (
        "\n\tBC_REQUEST_FREEZE_NOTIFICATION =\n"
        "\t\t_IOW('c', 19, struct binder_handle_cookie),\n"
        "\tBC_CLEAR_FREEZE_NOTIFICATION =\n"
        "\t\t_IOW('c', 20, struct binder_handle_cookie),\n"
        "\tBC_FREEZE_NOTIFICATION_DONE = _IOW('c', 21, binder_uintptr_t),"
    )
    s = s[:end] + addition + s[end:]

save(p, s)

p, s = load("drivers/android/binder.c")
require(s, "BINDER_WORK_TRANSACTION_PENDING", "Phase 82 work type")
require(s, "BR_TRANSACTION_PENDING_FROZEN", "Phase 82 core")

# Statistics: newest C Binder tracks freeze objects and all new BR/BC commands.
if "BINDER_STAT_FREEZE" not in s:
    s = replace_once(
        s,
        "\tBINDER_STAT_TRANSACTION_COMPLETE,\n\tBINDER_STAT_COUNT",
        "\tBINDER_STAT_TRANSACTION_COMPLETE,\n\tBINDER_STAT_FREEZE,\n\tBINDER_STAT_COUNT",
        "binder freeze statistic",
    )

s = s.replace(
    "atomic_t br[_IOC_NR(BR_TRANSACTION_PENDING_FROZEN) + 1];",
    "atomic_t br[_IOC_NR(BR_CLEAR_FREEZE_NOTIFICATION_DONE) + 1];",
    1,
)
# The vendor tree still sizes BC stats through BC_REPLY_SG after Phase 82.
s = s.replace(
    "atomic_t bc[_IOC_NR(BC_REPLY_SG) + 1];",
    "atomic_t bc[_IOC_NR(BC_FREEZE_NOTIFICATION_DONE) + 1];",
    1,
)

# Work types used for queued state and clear acknowledgements.
if "BINDER_WORK_FROZEN_BINDER" not in s:
    s = replace_once(
        s,
        "\t\tBINDER_WORK_CLEAR_DEATH_NOTIFICATION,\n\t} type;",
        "\t\tBINDER_WORK_CLEAR_DEATH_NOTIFICATION,\n"
        "\t\tBINDER_WORK_FROZEN_BINDER,\n"
        "\t\tBINDER_WORK_CLEAR_FREEZE_NOTIFICATION,\n"
        "\t} type;",
        "binder freeze work types",
    )

# Freeze notification object and ref pointer.
if "struct binder_ref_freeze" not in s:
    anchor = "struct binder_ref_death {"
    idx = s.find(anchor)
    if idx < 0:
        raise SystemExit("binder ref death struct anchor missing")
    # Insert after the full death structure.
    end = s.find("\n};", idx)
    if end < 0:
        raise SystemExit("binder ref death struct end missing")
    end += 3
    freeze_struct = """

struct binder_ref_freeze {
	struct binder_work work;
	binder_uintptr_t cookie;
	bool is_frozen:1;
	bool sent:1;
	bool resend:1;
};
"""
    s = s[:end] + freeze_struct + s[end:]

if "struct binder_ref_freeze *freeze;" not in s:
    s = replace_once(
        s,
        "\tstruct binder_ref_death *death;\n};",
        "\tstruct binder_ref_death *death;\n\tstruct binder_ref_freeze *freeze;\n};",
        "binder ref freeze pointer",
    )

if "struct list_head delivered_freeze;" not in s:
    s = replace_once(
        s,
        "\tstruct list_head delivered_death;\n\tint max_threads;",
        "\tstruct list_head delivered_death;\n"
        "\tstruct list_head delivered_freeze;\n"
        "\tint max_threads;",
        "binder proc delivered freeze list",
    )

# Cleanup paths include the final upstream fixes:
# - dequeue attached freeze work before freeing a ref
# - free detached clear work in binder_release_work()
# - drain delivered_freeze during proc release
if "if (ref->freeze) {" not in s[s.find("static void binder_cleanup_ref_olocked"):s.find("static int binder_inc_ref_olocked")]:
    old = """	if (ref->death) {
		binder_debug(BINDER_DEBUG_DEAD_BINDER,
			     "%d delete ref %d desc %d has death notification\\n",
			      ref->proc->pid, ref->data.debug_id,
			      ref->data.desc);
		binder_dequeue_work(ref->proc, &ref->death->work);
		binder_stats_deleted(BINDER_STAT_DEATH);
	}
	binder_stats_deleted(BINDER_STAT_REF);
"""
    new = """	if (ref->death) {
		binder_debug(BINDER_DEBUG_DEAD_BINDER,
			     "%d delete ref %d desc %d has death notification\\n",
			      ref->proc->pid, ref->data.debug_id,
			      ref->data.desc);
		binder_dequeue_work(ref->proc, &ref->death->work);
		binder_stats_deleted(BINDER_STAT_DEATH);
	}
	if (ref->freeze) {
		binder_dequeue_work(ref->proc, &ref->freeze->work);
		binder_stats_deleted(BINDER_STAT_FREEZE);
	}
	binder_stats_deleted(BINDER_STAT_REF);
"""
    s = replace_once(s, old, new, "binder cleanup freeze ref")

if "kfree(ref->freeze);" not in s:
    s = replace_once(
        s,
        "\tkfree(ref->death);\n\tkfree(ref);",
        "\tkfree(ref->death);\n\tkfree(ref->freeze);\n\tkfree(ref);",
        "binder free freeze ref",
    )

# Final safe iterator includes the 2024 node-UAF and rb-tree-OOB fixes:
# pin each node before dropping inner_lock and keep the previous pin until the
# next node is safely acquired.
if "static void binder_add_freeze_work" not in s:
    anchor = "static int binder_ioctl_freeze(struct binder_freeze_info *info,"
    require(s, anchor, "binder freezer ioctl anchor")
    helper = r'''static void binder_add_freeze_work(struct binder_proc *proc,
				   bool is_frozen)
{
	struct binder_node *prev = NULL;
	struct rb_node *n;
	struct binder_ref *ref;

	binder_inner_proc_lock(proc);
	for (n = rb_first(&proc->nodes); n; n = rb_next(n)) {
		struct binder_node *node;

		node = rb_entry(n, struct binder_node, rb_node);
		binder_inc_node_tmpref_ilocked(node);
		binder_inner_proc_unlock(proc);
		if (prev)
			binder_put_node(prev);

		binder_node_lock(node);
		hlist_for_each_entry(ref, &node->refs, node_entry) {
			binder_inner_proc_lock(ref->proc);
			if (!ref->freeze) {
				binder_inner_proc_unlock(ref->proc);
				continue;
			}
			ref->freeze->work.type = BINDER_WORK_FROZEN_BINDER;
			if (list_empty(&ref->freeze->work.entry)) {
				ref->freeze->is_frozen = is_frozen;
				binder_enqueue_work_ilocked(&ref->freeze->work,
							    &ref->proc->todo);
				binder_wakeup_proc_ilocked(ref->proc);
			} else {
				if (ref->freeze->sent &&
				    ref->freeze->is_frozen != is_frozen)
					ref->freeze->resend = true;
				ref->freeze->is_frozen = is_frozen;
			}
			binder_inner_proc_unlock(ref->proc);
		}
		prev = node;
		binder_node_unlock(node);

		binder_inner_proc_lock(proc);
		if (proc->is_dead)
			break;
	}
	binder_inner_proc_unlock(proc);
	if (prev)
		binder_put_node(prev);
}

'''
    s = s.replace(anchor, helper + anchor, 1)

# Notify watchers on both thaw and a successful freeze. Do not publish a
# failed freeze attempt.
old_thaw = """	if (!info->enable) {
		binder_inner_proc_lock(target_proc);
		target_proc->sync_recv = false;
		target_proc->async_recv = false;
		target_proc->is_frozen = false;
		binder_inner_proc_unlock(target_proc);
		return 0;
	}
"""
new_thaw = """	if (!info->enable) {
		binder_inner_proc_lock(target_proc);
		target_proc->sync_recv = false;
		target_proc->async_recv = false;
		target_proc->is_frozen = false;
		binder_inner_proc_unlock(target_proc);
		binder_add_freeze_work(target_proc, false);
		return 0;
	}
"""
if old_thaw in s:
    s = s.replace(old_thaw, new_thaw, 1)
elif "binder_add_freeze_work(target_proc, false);" not in s:
    raise SystemExit("binder thaw notification anchor missing")

old_end = """	if (ret < 0) {
		binder_inner_proc_lock(target_proc);
		target_proc->is_frozen = false;
		binder_inner_proc_unlock(target_proc);
	}

	return ret;
}
"""
new_end = """	if (ret < 0) {
		binder_inner_proc_lock(target_proc);
		target_proc->is_frozen = false;
		binder_inner_proc_unlock(target_proc);
	} else {
		binder_add_freeze_work(target_proc, true);
	}

	return ret;
}
"""
# Scope replacement after binder_ioctl_freeze to avoid unrelated patterns.
freeze_start = s.find("static int binder_ioctl_freeze")
freeze_end = s.find("static int binder_ioctl_get_freezer_info", freeze_start)
segment = s[freeze_start:freeze_end]
if old_end in segment:
    segment = segment.replace(old_end, new_end, 1)
    s = s[:freeze_start] + segment + s[freeze_end:]
elif "binder_add_freeze_work(target_proc, true);" not in segment:
    raise SystemExit("binder successful freeze notification anchor missing")

# Request/clear/ack helpers use the final upstream behavior, including valid
# requests against dead nodes, where the initial state message is skipped.
if "binder_request_freeze_notification" not in s:
    anchor = "static void\nbinder_free_buf"
    idx = s.find(anchor)
    if idx < 0:
        # vendor formatting usually has a newline after static void
        idx = s.find("static void\nbinder_free_buf")
    if idx < 0:
        idx = s.find("static void binder_free_buf")
    if idx < 0:
        raise SystemExit("binder_free_buf anchor missing")

    helpers = r'''static int
binder_request_freeze_notification(struct binder_proc *proc,
				   struct binder_thread *thread,
				   struct binder_handle_cookie *handle_cookie)
{
	struct binder_ref_freeze *freeze;
	struct binder_ref *ref;

	freeze = kzalloc(sizeof(*freeze), GFP_KERNEL);
	if (!freeze)
		return -ENOMEM;

	binder_proc_lock(proc);
	ref = binder_get_ref_olocked(proc, handle_cookie->handle, false);
	if (!ref) {
		binder_user_error("%d:%d BC_REQUEST_FREEZE_NOTIFICATION invalid ref %d\n",
				  proc->pid, thread->pid, handle_cookie->handle);
		binder_proc_unlock(proc);
		kfree(freeze);
		return -EINVAL;
	}

	binder_node_lock(ref->node);
	if (ref->freeze) {
		binder_user_error("%d:%d BC_REQUEST_FREEZE_NOTIFICATION already set\n",
				  proc->pid, thread->pid);
		binder_node_unlock(ref->node);
		binder_proc_unlock(proc);
		kfree(freeze);
		return -EINVAL;
	}

	binder_stats_created(BINDER_STAT_FREEZE);
	INIT_LIST_HEAD(&freeze->work.entry);
	freeze->cookie = handle_cookie->cookie;
	freeze->work.type = BINDER_WORK_FROZEN_BINDER;
	ref->freeze = freeze;

	/* Dead nodes are a valid race: keep the request but skip initial state. */
	if (ref->node->proc) {
		binder_inner_proc_lock(ref->node->proc);
		freeze->is_frozen = ref->node->proc->is_frozen;
		binder_inner_proc_unlock(ref->node->proc);

		binder_inner_proc_lock(proc);
		binder_enqueue_work_ilocked(&freeze->work, &proc->todo);
		binder_wakeup_proc_ilocked(proc);
		binder_inner_proc_unlock(proc);
	}

	binder_node_unlock(ref->node);
	binder_proc_unlock(proc);
	return 0;
}

static int
binder_clear_freeze_notification(struct binder_proc *proc,
				 struct binder_thread *thread,
				 struct binder_handle_cookie *handle_cookie)
{
	struct binder_ref_freeze *freeze;
	struct binder_ref *ref;

	binder_proc_lock(proc);
	ref = binder_get_ref_olocked(proc, handle_cookie->handle, false);
	if (!ref) {
		binder_user_error("%d:%d BC_CLEAR_FREEZE_NOTIFICATION invalid ref %d\n",
				  proc->pid, thread->pid, handle_cookie->handle);
		binder_proc_unlock(proc);
		return -EINVAL;
	}

	binder_node_lock(ref->node);
	if (!ref->freeze) {
		binder_user_error("%d:%d BC_CLEAR_FREEZE_NOTIFICATION freeze notification not active\n",
				  proc->pid, thread->pid);
		binder_node_unlock(ref->node);
		binder_proc_unlock(proc);
		return -EINVAL;
	}

	freeze = ref->freeze;
	binder_inner_proc_lock(proc);
	if (freeze->cookie != handle_cookie->cookie) {
		binder_user_error("%d:%d BC_CLEAR_FREEZE_NOTIFICATION cookie mismatch %016llx != %016llx\n",
				  proc->pid, thread->pid,
				  (u64)freeze->cookie, (u64)handle_cookie->cookie);
		binder_inner_proc_unlock(proc);
		binder_node_unlock(ref->node);
		binder_proc_unlock(proc);
		return -EINVAL;
	}

	ref->freeze = NULL;
	freeze->work.type = BINDER_WORK_CLEAR_FREEZE_NOTIFICATION;
	if (list_empty(&freeze->work.entry)) {
		binder_enqueue_work_ilocked(&freeze->work, &proc->todo);
		binder_wakeup_proc_ilocked(proc);
	} else if (freeze->sent) {
		freeze->resend = true;
	}
	binder_inner_proc_unlock(proc);
	binder_node_unlock(ref->node);
	binder_proc_unlock(proc);
	return 0;
}

static int
binder_freeze_notification_done(struct binder_proc *proc,
				struct binder_thread *thread,
				binder_uintptr_t cookie)
{
	struct binder_ref_freeze *freeze = NULL;
	struct binder_work *w;

	binder_inner_proc_lock(proc);
	list_for_each_entry(w, &proc->delivered_freeze, entry) {
		struct binder_ref_freeze *tmp =
			container_of(w, struct binder_ref_freeze, work);

		if (tmp->cookie == cookie) {
			freeze = tmp;
			break;
		}
	}

	if (!freeze) {
		binder_user_error("%d:%d BC_FREEZE_NOTIFICATION_DONE %016llx not found\n",
				  proc->pid, thread->pid, (u64)cookie);
		binder_inner_proc_unlock(proc);
		return -EINVAL;
	}

	binder_dequeue_work_ilocked(&freeze->work);
	freeze->sent = false;
	if (freeze->resend) {
		freeze->resend = false;
		binder_enqueue_work_ilocked(&freeze->work, &proc->todo);
		binder_wakeup_proc_ilocked(proc);
	}
	binder_inner_proc_unlock(proc);
	return 0;
}

'''
    s = s[:idx] + helpers + s[idx:]

# Add write protocol commands just before the existing default.
if "case BC_REQUEST_FREEZE_NOTIFICATION:" not in s:
    # Locate binder_thread_write's default after BC_DEAD_BINDER_DONE.
    start = s.find("static int binder_thread_write")
    end = s.find("static void binder_stat_br", start)
    segment = s[start:end]
    anchor = "\n\t\tdefault:\n\t\t\tpr_err"
    idx = segment.rfind(anchor)
    if idx < 0:
        raise SystemExit("binder_thread_write default anchor missing")
    cases = r'''
		case BC_REQUEST_FREEZE_NOTIFICATION:
		case BC_CLEAR_FREEZE_NOTIFICATION: {
			struct binder_handle_cookie hc;

			if (copy_from_user(&hc, ptr, sizeof(hc)))
				return -EFAULT;
			ptr += sizeof(hc);
			if (cmd == BC_REQUEST_FREEZE_NOTIFICATION)
				ret = binder_request_freeze_notification(proc, thread, &hc);
			else
				ret = binder_clear_freeze_notification(proc, thread, &hc);
			if (ret)
				return ret;
		} break;
		case BC_FREEZE_NOTIFICATION_DONE: {
			binder_uintptr_t cookie;

			if (get_user(cookie, (binder_uintptr_t __user *)ptr))
				return -EFAULT;
			ptr += sizeof(cookie);
			ret = binder_freeze_notification_done(proc, thread, cookie);
			if (ret)
				return ret;
		} break;
'''
    segment = segment[:idx] + cases + segment[idx:]
    s = s[:start] + segment + s[end:]

# Add read protocol handling after death notifications.
if "case BINDER_WORK_FROZEN_BINDER:" not in s[s.find("static int binder_thread_read"):s.find("static void binder_release_work")]:
    start = s.find("static int binder_thread_read")
    end = s.find("static void binder_release_work", start)
    segment = s[start:end]
    # Insert before the switch closes, using the final death case trailer.
    anchor = """		} break;
		}

		if (!t)
"""
    if anchor not in segment:
        raise SystemExit("binder_thread_read switch end anchor missing")
    cases = r'''		} break;
		case BINDER_WORK_FROZEN_BINDER: {
			struct binder_ref_freeze *freeze;
			struct binder_frozen_state_info info;

			memset(&info, 0, sizeof(info));
			freeze = container_of(w, struct binder_ref_freeze, work);
			info.is_frozen = freeze->is_frozen;
			info.cookie = freeze->cookie;
			freeze->sent = true;
			binder_enqueue_work_ilocked(w, &proc->delivered_freeze);
			binder_inner_proc_unlock(proc);

			if (put_user(BR_FROZEN_BINDER, (uint32_t __user *)ptr))
				return -EFAULT;
			ptr += sizeof(uint32_t);
			if (copy_to_user(ptr, &info, sizeof(info)))
				return -EFAULT;
			ptr += sizeof(info);
			binder_stat_br(proc, thread, BR_FROZEN_BINDER);
			goto done;
		} break;
		case BINDER_WORK_CLEAR_FREEZE_NOTIFICATION: {
			struct binder_ref_freeze *freeze =
				container_of(w, struct binder_ref_freeze, work);
			binder_uintptr_t cookie = freeze->cookie;

			binder_inner_proc_unlock(proc);
			kfree(freeze);
			binder_stats_deleted(BINDER_STAT_FREEZE);
			if (put_user(BR_CLEAR_FREEZE_NOTIFICATION_DONE,
				     (uint32_t __user *)ptr))
				return -EFAULT;
			ptr += sizeof(uint32_t);
			if (put_user(cookie, (binder_uintptr_t __user *)ptr))
				return -EFAULT;
			ptr += sizeof(binder_uintptr_t);
			binder_stat_br(proc, thread, BR_CLEAR_FREEZE_NOTIFICATION_DONE);
		} break;
		}

		if (!t)
'''
    segment = segment.replace(anchor, cases, 1)
    s = s[:start] + segment + s[end:]

# Final release-work UAF fix: only detached CLEAR work is independently owned
# by a proc worklist. Attached FROZEN work is dequeued by ref cleanup.
if "case BINDER_WORK_CLEAR_FREEZE_NOTIFICATION:" not in s[s.find("static void binder_release_work"):s.find("static struct binder_thread *binder_get_thread_ilocked")]:
    start = s.find("static void binder_release_work")
    end = s.find("static struct binder_thread *binder_get_thread_ilocked", start)
    segment = s[start:end]
    anchor = """		case BINDER_WORK_NODE:
			break;
		default:
"""
    addition = """		case BINDER_WORK_NODE:
			break;
		case BINDER_WORK_CLEAR_FREEZE_NOTIFICATION: {
			struct binder_ref_freeze *freeze;

			freeze = container_of(w, struct binder_ref_freeze, work);
			binder_debug(BINDER_DEBUG_DEAD_TRANSACTION,
				     "undelivered freeze notification, %016llx\\n",
				     (u64)freeze->cookie);
			kfree(freeze);
			binder_stats_deleted(BINDER_STAT_FREEZE);
		} break;
		default:
"""
    if anchor not in segment:
        raise SystemExit("binder_release_work node/default anchor missing")
    segment = segment.replace(anchor, addition, 1)
    s = s[:start] + segment + s[end:]

# Proc lifetime cleanup, including delivered_freeze leak fix.
if "BUG_ON(!list_empty(&proc->delivered_freeze));" not in s:
    s = replace_once(
        s,
        "\tBUG_ON(!list_empty(&proc->delivered_death));",
        "\tBUG_ON(!list_empty(&proc->delivered_death));\n"
        "\tBUG_ON(!list_empty(&proc->delivered_freeze));",
        "binder free proc freeze list assertion",
    )

if "INIT_LIST_HEAD(&proc->delivered_freeze);" not in s:
    s = replace_once(
        s,
        "\tINIT_LIST_HEAD(&proc->delivered_death);\n\tINIT_LIST_HEAD(&proc->waiting_threads);",
        "\tINIT_LIST_HEAD(&proc->delivered_death);\n"
        "\tINIT_LIST_HEAD(&proc->delivered_freeze);\n"
        "\tINIT_LIST_HEAD(&proc->waiting_threads);",
        "binder proc freeze list initialization",
    )

if "binder_release_work(proc, &proc->delivered_freeze);" not in s:
    s = replace_once(
        s,
        "\tbinder_release_work(proc, &proc->todo);\n"
        "\tbinder_release_work(proc, &proc->delivered_death);",
        "\tbinder_release_work(proc, &proc->todo);\n"
        "\tbinder_release_work(proc, &proc->delivered_death);\n"
        "\tbinder_release_work(proc, &proc->delivered_freeze);",
        "binder delivered freeze cleanup",
    )

# Minimal debug output support for the new work types.
if 'case BINDER_WORK_FROZEN_BINDER:' not in s[s.find("static void print_binder_work_ilocked"):]:
    start = s.find("static void print_binder_work_ilocked")
    if start >= 0:
        end = s.find("static void print_binder_thread_ilocked", start)
        segment = s[start:end]
        anchor = """	case BINDER_WORK_CLEAR_DEATH_NOTIFICATION:
		seq_printf(m, "%shas cleared death notification\\n", prefix);
		break;
"""
        if anchor in segment:
            segment = segment.replace(
                anchor,
                anchor +
                """	case BINDER_WORK_FROZEN_BINDER:
		seq_printf(m, "%shas frozen binder\\n", prefix);
		break;
	case BINDER_WORK_CLEAR_FREEZE_NOTIFICATION:
		seq_printf(m, "%shas cleared freeze notification\\n", prefix);
		break;
""",
                1,
            )
            s = s[:start] + segment + s[end:]

save(p, s)

checks = {
    "include/uapi/linux/android/binder.h": [
        "struct binder_frozen_state_info",
        "BR_FROZEN_BINDER",
        "BR_CLEAR_FREEZE_NOTIFICATION_DONE",
        "BC_REQUEST_FREEZE_NOTIFICATION",
        "BC_CLEAR_FREEZE_NOTIFICATION",
        "BC_FREEZE_NOTIFICATION_DONE",
    ],
    "drivers/android/binder.c": [
        "BINDER_STAT_FREEZE",
        "BINDER_WORK_FROZEN_BINDER",
        "BINDER_WORK_CLEAR_FREEZE_NOTIFICATION",
        "struct binder_ref_freeze",
        "delivered_freeze",
        "binder_add_freeze_work",
        "binder_request_freeze_notification",
        "binder_clear_freeze_notification",
        "binder_freeze_notification_done",
        "binder_add_freeze_work(target_proc, false);",
        "binder_add_freeze_work(target_proc, true);",
        "binder_release_work(proc, &proc->delivered_freeze);",
    ],
}

for rel, markers in checks.items():
    data = (root / rel).read_text()
    for marker in markers:
        require(data, marker, rel)

print("Android Binder mainline Phase 3 freeze notifications applied successfully")
