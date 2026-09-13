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
    s = s.replace('#include <trace/hooks/binder.h>\n', '')
    p.write_text(s)

mk = android / "Makefile"
s = mk.read_text()
if (android / "binder_pick.c").exists() and "binder_pick.o" not in s:
    old = "obj-$(CONFIG_ANDROID_BINDER_IPC)\t+= binder.o binder_alloc.o"
    if old not in s:
        old = "obj-$(CONFIG_ANDROID_BINDER_IPC) += binder.o binder_alloc.o"
    if old not in s:
        raise SystemExit("Android Binder Makefile IPC anchor not recognized")
    s = s.replace(old, old + " binder_pick.o", 1)
mk.write_text(s)

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
