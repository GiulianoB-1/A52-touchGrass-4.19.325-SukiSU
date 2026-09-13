#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1])
h = root / "drivers/android/binder_alloc.h"
c = root / "drivers/android/binder_alloc.c"

s = h.read_text()
old_bits = """\tunsigned free:1;
\tunsigned allow_user_free:1;
\tunsigned async_transaction:1;
\tunsigned debug_id:29;
"""
new_bits = """\tunsigned free:1;
\tunsigned clear_on_free:1;
\tunsigned allow_user_free:1;
\tunsigned async_transaction:1;
\tunsigned oneway_spam_suspect:1;
\tunsigned debug_id:27;
"""
if old_bits not in s:
    raise SystemExit("binder_buffer bitfield anchor mismatch")
s = s.replace(old_bits, new_bits, 1)

if "binder_alloc_shrinker_exit" not in s:
    s = s.replace(
        "extern int binder_alloc_shrinker_init(void);\n",
        "extern int binder_alloc_shrinker_init(void);\n"
        "extern void binder_alloc_shrinker_exit(void);\n",
        1,
    )

s = s.replace(
    "void binder_alloc_copy_to_buffer(struct binder_alloc *alloc,",
    "int binder_alloc_copy_to_buffer(struct binder_alloc *alloc,",
    1,
)
s = s.replace(
    "void binder_alloc_copy_from_buffer(struct binder_alloc *alloc,",
    "int binder_alloc_copy_from_buffer(struct binder_alloc *alloc,",
    1,
)
h.write_text(s)

s = c.read_text()
s = s.replace(
    "static void binder_alloc_do_buffer_copy(struct binder_alloc *alloc,",
    "static int binder_alloc_do_buffer_copy(struct binder_alloc *alloc,",
    1,
)
s = s.replace(
    """\t/* All copies must be 32-bit aligned and 32-bit size */
\tBUG_ON(!check_buffer(alloc, buffer, buffer_offset, bytes));

\twhile (bytes) {""",
    """\t/* All copies must be 32-bit aligned and 32-bit size */
\tif (!check_buffer(alloc, buffer, buffer_offset, bytes))
\t\treturn -EINVAL;

\twhile (bytes) {""",
    1,
)

tail = """\t\tptr = ptr + size;
\t\tbuffer_offset += size;
\t}
}

void binder_alloc_copy_to_buffer"""
if tail not in s:
    raise SystemExit("binder allocator copy helper tail mismatch")
s = s.replace(
    tail,
    """\t\tptr = ptr + size;
\t\tbuffer_offset += size;
\t}
\treturn 0;
}

int binder_alloc_copy_to_buffer""",
    1,
)

old_to = """{
\tbinder_alloc_do_buffer_copy(alloc, true, buffer, buffer_offset,
\t\t\t\t    src, bytes);
}

void binder_alloc_copy_from_buffer"""
new_to = """{
\treturn binder_alloc_do_buffer_copy(alloc, true, buffer, buffer_offset,
\t\t\t\t\t   src, bytes);
}

int binder_alloc_copy_from_buffer"""
if old_to not in s:
    raise SystemExit("binder allocator copy-to wrapper mismatch")
s = s.replace(old_to, new_to, 1)

old_from = """{
\tbinder_alloc_do_buffer_copy(alloc, false, buffer, buffer_offset,
\t\t\t\t    dest, bytes);
}
"""
new_from = """{
\treturn binder_alloc_do_buffer_copy(alloc, false, buffer, buffer_offset,
\t\t\t\t\t   dest, bytes);
}
"""
if old_from not in s:
    raise SystemExit("binder allocator copy-from wrapper mismatch")
s = s.replace(old_from, new_from, 1)

init = """int binder_alloc_shrinker_init(void)
{
\tint ret = list_lru_init(&binder_alloc_lru);

\tif (ret == 0) {
\t\tret = register_shrinker(&binder_shrinker);
\t\tif (ret)
\t\t\tlist_lru_destroy(&binder_alloc_lru);
\t}
\treturn ret;
}
"""
if init not in s:
    raise SystemExit("binder shrinker init anchor mismatch")
s = s.replace(
    init,
    init + """
void binder_alloc_shrinker_exit(void)
{
\tunregister_shrinker(&binder_shrinker);
\tlist_lru_destroy(&binder_alloc_lru);
}
""",
    1,
)
c.write_text(s)
