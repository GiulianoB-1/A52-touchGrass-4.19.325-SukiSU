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
