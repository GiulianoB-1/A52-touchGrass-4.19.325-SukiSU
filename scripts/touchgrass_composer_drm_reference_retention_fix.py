#!/usr/bin/env python3
"""Convert the precise golden display recorder from a ring to first-events retention."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: touchgrass_composer_drm_reference_retention_fix.py <kernel-root>')

p = Path(sys.argv[1]) / 'kernel/tg_display_reference.c'
s = p.read_text()

old = '''    u64 seq = atomic64_inc_return(&tg_disp_seq);\n    u32 slot = (u32)((seq - 1) % TG_DISP_REF_MAX);\n\n    e = &tg_disp_entries[slot];'''
new = '''    u64 seq = atomic64_inc_return(&tg_disp_seq);\n    u32 slot;\n\n    /* Preserve the startup sequence permanently. A working Composer can\n     * generate display traffic every frame, so never overwrite early data. */\n    if (seq > TG_DISP_REF_MAX)\n        return;\n    slot = (u32)(seq - 1);\n    e = &tg_disp_entries[slot];'''
if s.count(old) != 1:
    raise SystemExit('record retention anchor mismatch')
s = s.replace(old, new, 1)

old = '''    u64 total = atomic64_read(&tg_disp_seq);\n    u64 first = total > TG_DISP_REF_MAX ? total - TG_DISP_REF_MAX + 1 : 1;\n    u64 seq;\n\n    seq_puts(m, "# touchgrass_composer_drm_reference_v1\\n");\n    seq_printf(m, "# tracked_tgid=%d total=%llu retained=%llu\\n",\n               atomic_read(&tg_disp_composer_tgid),\n               (unsigned long long)total,\n               (unsigned long long)(total >= first ? total - first + 1 : 0));'''
new = '''    u64 total = atomic64_read(&tg_disp_seq);\n    u64 kept = min_t(u64, total, TG_DISP_REF_MAX);\n    u64 seq;\n\n    seq_puts(m, "# touchgrass_composer_drm_reference_v1 first-events-retention\\n");\n    seq_printf(m, "# tracked_tgid=%d total=%llu retained=%llu dropped=%llu\\n",\n               atomic_read(&tg_disp_composer_tgid),\n               (unsigned long long)total,\n               (unsigned long long)kept,\n               (unsigned long long)(total > kept ? total - kept : 0));'''
if s.count(old) != 1:
    raise SystemExit('proc retention anchor mismatch')
s = s.replace(old, new, 1)

old = '''    for (seq = first; seq <= total; seq++) {\n        struct tg_disp_ref_entry *e = &tg_disp_entries[(seq - 1) % TG_DISP_REF_MAX];'''
new = '''    for (seq = 1; seq <= kept; seq++) {\n        struct tg_disp_ref_entry *e = &tg_disp_entries[seq - 1];'''
if s.count(old) != 1:
    raise SystemExit('proc iteration anchor mismatch')
s = s.replace(old, new, 1)

p.write_text(s)
print('touchgrass_composer_drm_reference_v1: first-events retention applied')
