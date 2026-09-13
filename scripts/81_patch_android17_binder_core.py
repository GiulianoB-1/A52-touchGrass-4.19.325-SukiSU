#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
import sys

root = Path(sys.argv[1])
scripts = Path(__file__).resolve().parent
android = root / "drivers/android"

shutil.copy2(scripts / "81_binder_compat.h", android / "binder_compat.h")

binder = android / "binder.c"
text = binder.read_text()

text = text.replace("#include <kunit/visibility.h>\n", '#include "binder_compat.h"\n')
text = text.replace("#include <linux/cacheflush.h>\n", "#include <asm/cacheflush.h>\n")
text = text.replace('#include <trace/hooks/binder.h>\n', '')
text = text.replace('#include "binder_netlink.h"\n', '#include "binder_compat.h"\n')

# Restore the Samsung Freecess dependency that exists in the working vendor
# Binder but not in AOSP Binder.
trace_inc = '#include "binder_trace.h"\n'
if trace_inc not in text:
    raise SystemExit("binder_trace include missing for Freecess include")
text = text.replace(
    trace_inc,
    trace_inc + '\n#ifdef CONFIG_SAMSUNG_FREECESS\n#include <linux/freecess.h>\n#endif\n',
    1,
)

hooks = sorted(set(re.findall(r'\b(trace_android_vh_[A-Za-z0-9_]+)\s*\(', text)))
if hooks:
    anchor = '#include "binder_trace.h"\n'
    defs = "\n/* No Android vendor-hook framework exists in the 4.19 vendor tree. */\n"
    defs += "\n".join(f"#define {name}(...) do {{ }} while (0)" for name in hooks)
    defs += "\n"
    if anchor not in text:
        raise SystemExit("binder trace include anchor missing")
    text = text.replace(anchor, anchor + defs, 1)

text = text.replace("proc->alloc.vm_start", "(unsigned long)proc->alloc.buffer")

# Linux 4.19 predates vm_flags_mod(). Preserve the exact modern operation:
# set VM_DONTCOPY|VM_MIXEDMAP and clear VM_MAYWRITE.
vm_mod = "vm_flags_mod(vma, VM_DONTCOPY | VM_MIXEDMAP, VM_MAYWRITE);"
if vm_mod not in text:
    raise SystemExit("vm_flags_mod Binder mmap anchor missing")
text = text.replace(
    vm_mod,
    "vma->vm_flags = (vma->vm_flags | VM_DONTCOPY | VM_MIXEDMAP) & ~VM_MAYWRITE;",
    1,
)

# simple_recursive_removal() is newer than 4.19. binderfs is disabled for this
# core probe, so this entry cannot normally exist. d_delete()+dput() is also
# sufficient for the single process log dentry if reached unexpectedly.
recursive = "simple_recursive_removal(proc->binderfs_entry, NULL);"
if recursive not in text:
    raise SystemExit("Binder process dentry removal anchor missing")
text = text.replace(
    recursive,
    "d_delete(proc->binderfs_entry);\n\t\tdput(proc->binderfs_entry);",
    1,
)

# Restore Samsung Freecess Binder transaction visibility from the original
# touchGrass Binder. CONFIG_FAST_TRACK is not enabled on this device, so the
# original proc->todo path is the relevant implementation.
freecess_anchor = "static const char * const binder_return_strings[] = {"
if freecess_anchor not in text:
    raise SystemExit("Binder return-string anchor missing for Freecess restore")
freecess = r'''
#ifdef CONFIG_SAMSUNG_FREECESS
static void binder_in_transaction(struct binder_proc *proc, int uid)
{
	struct rb_node *n = NULL;
	struct binder_thread *thread = NULL;
	struct binder_transaction *t = NULL;
	struct binder_work *w = NULL;
	bool found = false;

	binder_inner_proc_lock(proc);
	for (n = rb_first(&proc->threads); n != NULL; n = rb_next(n)) {
		thread = rb_entry(n, struct binder_thread, rb_node);
		if (!binder_worklist_empty_ilocked(&thread->todo)) {
			list_for_each_entry(w, &thread->todo, entry) {
				if (w->type == BINDER_WORK_TRANSACTION) {
					t = container_of(w, struct binder_transaction, work);
					if (!(t->flags & TF_ONE_WAY)) {
						found = true;
						break;
					}
				} else if (w->type != BINDER_WORK_TRANSACTION_COMPLETE &&
					   w->type != BINDER_WORK_NODE) {
					found = true;
					break;
				}
			}
			if (found) {
				binder_inner_proc_unlock(proc);
				cfb_report(uid, "thread");
				return;
			}
		}

		t = thread->transaction_stack;
		if (t && t->to_thread == thread) {
			binder_inner_proc_unlock(proc);
			cfb_report(uid, "transaction_stack");
			return;
		}
	}

	if (!binder_worklist_empty_ilocked(&proc->todo)) {
		list_for_each_entry(w, &proc->todo, entry) {
			if (w->type == BINDER_WORK_TRANSACTION) {
				t = container_of(w, struct binder_transaction, work);
				if (!(t->flags & TF_ONE_WAY)) {
					found = true;
					break;
				}
			} else if (w->type != BINDER_WORK_TRANSACTION_COMPLETE &&
				   w->type != BINDER_WORK_NODE) {
				found = true;
				break;
			}
		}
		if (found) {
			binder_inner_proc_unlock(proc);
			cfb_report(uid, "proc");
			return;
		}
	}
	binder_inner_proc_unlock(proc);
}

void binders_in_transcation(int uid)
{
	struct binder_proc *itr;

	mutex_lock(&binder_procs_lock);
	hlist_for_each_entry(itr, &binder_procs, proc_node) {
		if (itr && __kuid_val(itr->cred->euid) == uid)
			binder_in_transaction(itr, uid);
	}
	mutex_unlock(&binder_procs_lock);
}
#endif

'''
text = text.replace(freecess_anchor, freecess + freecess_anchor, 1)

# Android 17's binder_buffer stores user_data as an integer address. The
# retained 4.19 allocator stores it as void __user *, so make the address
# arithmetic explicit without changing the allocator ABI.
for old, new in (
    ("parent->buffer - buffer->user_data",
     "parent->buffer - (binder_uintptr_t)buffer->user_data"),
    ("parent->buffer - t->buffer->user_data",
     "parent->buffer - (binder_uintptr_t)t->buffer->user_data"),
    ("bp->parent_offset + parent->buffer - b->user_data",
     "bp->parent_offset + parent->buffer - (binder_uintptr_t)b->user_data"),
):
    if old not in text:
        raise SystemExit(f"Binder 4.19 pointer arithmetic anchor missing: {old}")
    text = text.replace(old, new, 1)

# TASK_FREEZABLE was added long after 4.19. Restore the equivalent freezer
# accounting used by the original 4.19 Binder wait path.
wait_sig = "static int binder_wait_for_work(struct binder_thread *thread,"
wait_start = text.find(wait_sig)
if wait_start < 0:
    raise SystemExit("binder_wait_for_work missing")
wait_end = text.find("\n}\n", wait_start)
if wait_end < 0:
    raise SystemExit("binder_wait_for_work end missing")
wait_end += 3
wait = text[wait_start:wait_end]
if "TASK_INTERRUPTIBLE|TASK_FREEZABLE" not in wait:
    raise SystemExit("TASK_FREEZABLE wait anchor missing")
wait = wait.replace("int ret = 0;\n", "int ret = 0;\n\n\tfreezer_do_not_count();\n", 1)
wait = wait.replace("TASK_INTERRUPTIBLE|TASK_FREEZABLE", "TASK_INTERRUPTIBLE", 1)
wait = wait.replace("\tbinder_inner_proc_unlock(proc);\n\n\treturn ret;",
                    "\tbinder_inner_proc_unlock(proc);\n\tfreezer_count();\n\n\treturn ret;", 1)
text = text[:wait_start] + wait + text[wait_end:]

# 4.19 has no compat_ptr_ioctl helper. Binder's ioctl ABI is pointer-width
# neutral here and the working vendor Binder routes compat directly to it.
if ".compat_ioctl = compat_ptr_ioctl," not in text:
    raise SystemExit("compat_ptr_ioctl anchor missing")
text = text.replace(".compat_ioctl = compat_ptr_ioctl,",
                    ".compat_ioctl = binder_ioctl,", 1)

pat = re.compile(
    r'(t->buffer\s*=\s*binder_alloc_new_buf\(&target_proc->alloc,\s*'
    r'tr->data_size,\s*tr->offsets_size,\s*extra_buffers_size,\s*'
    r'!reply\s*&&\s*\(t->flags\s*&\s*TF_ONE_WAY\))\s*\);',
    re.S,
)
text, count = pat.subn(r'\1, thread->pid);', text, count=1)
if count != 1:
    raise SystemExit(f"binder_alloc_new_buf call shape mismatch: {count}")

def replace_function_body(src: str, signature: str, body: str) -> str:
    start = src.find(signature)
    if start < 0:
        return src
    brace = src.find("{", start)
    if brace < 0:
        raise SystemExit(f"opening brace missing for {signature}")
    depth = 0
    end = None
    for pos in range(brace, len(src)):
        if src[pos] == "{":
            depth += 1
        elif src[pos] == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break
    if end is None:
        raise SystemExit(f"closing brace missing for {signature}")
    return src[:brace] + "{\n" + body + "\n}" + src[end:]

text = replace_function_body(
    text,
    "static void binder_netlink_report(",
    "\t/* Android 17 netlink reporting is deferred on Linux 4.19. */",
)

binder.write_text(text)

# The modern tracepoint uses allocator vm_start, while the retained 4.19
# allocator exposes the same userspace base as buffer.
trace_h = android / "binder_trace.h"
trace_text = trace_h.read_text()
if "alloc->vm_start" in trace_text:
    trace_text = trace_text.replace("alloc->vm_start", "(unsigned long)alloc->buffer")
trace_h.write_text(trace_text)

for name in ("binder_internal.h", "binder_pick.c", "binder_pick.h"):
    p = android / name
    if not p.exists():
        continue
    s = p.read_text()
    s = s.replace("#include <kunit/visibility.h>\n", '#include "binder_compat.h"\n')
    s = s.replace("#include <linux/android_vendor.h>\n", '#include "binder_compat.h"\n')
    s = s.replace('#include <trace/hooks/binder.h>\n', '')
    p.write_text(s)

# Android 17's binder_pick.c only chooses between the C and Rust Binder
# implementations. This 4.19 vendor kernel cannot host the Rust driver, so
# keep the Android 17 C Binder and provide the selector hooks as simple stubs.
(android / "binder_pick.h").write_text(r"""/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _LINUX_BINDER_PICK_IMPL_H
#define _LINUX_BINDER_PICK_IMPL_H
#include <linux/errno.h>
#include <linux/module.h>
static inline void binder_remove_trace_events(struct module *module) { }
static inline int binder_try_unload_builtin(void) { return -EOPNOTSUPP; }
static inline int on_binderfs_mount(void) { return 0; }
void binder_unload_builtin(void);
#endif
""")

cfg = root / "arch/arm64/configs/a52xq_defconfig"
s = cfg.read_text()
if "CONFIG_ANDROID_BINDERFS=y" in s:
    s = s.replace("CONFIG_ANDROID_BINDERFS=y", "# CONFIG_ANDROID_BINDERFS is not set", 1)
cfg.write_text(s)

# The allocator ABI bridge is kept in a separate helper so it can be reviewed
# independently from the imported Binder core.
allocator_patch = scripts / "81_patch_binder_allocator.py"
ns = {"__name__": "__main__", "__file__": str(allocator_patch)}
exec(compile(allocator_patch.read_text(), str(allocator_patch), "exec"), ns)
