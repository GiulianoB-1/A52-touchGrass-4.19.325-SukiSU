#!/usr/bin/env python3
from pathlib import Path
import sys


def main(root: Path):
    p = root / 'kernel/tg_fdr.c'
    text = p.read_text()

    include_anchor = '#include <linux/proc_fs.h>\n'
    if '#include <linux/printk.h>\n' not in text:
        if text.count(include_anchor) != 1:
            raise SystemExit('tg_fdr.c printk include anchor mismatch')
        text = text.replace(include_anchor,
                            include_anchor + '#include <linux/printk.h>\n', 1)

    helper_anchor = 'static DECLARE_WAIT_QUEUE_HEAD(tg_fdr_waitq);\n'
    helper = r'''

static u32 tg_fdr_critical_checksum(const struct tg_fdr_record *r)
{
	u64 x = r->seq ^ r->bank_seq ^ r->ns ^ r->object_id ^
		r->a ^ r->b ^ r->c ^ r->d;
	x ^= ((u64)(u32)r->rc << 32) ^ r->event;
	x ^= ((u64)r->session << 32) ^ ((u64)r->subsystem << 16) ^ r->bank;
	x ^= x >> 33;
	x *= 0xff51afd7ed558ccdULL;
	x ^= x >> 33;
	return (u32)x ^ (u32)(x >> 32);
}

static void tg_fdr_persist_critical(const struct tg_fdr_record *r)
{
	/*
	 * Sparse metadata-only mirror into the existing PSTORE_CONSOLE path.
	 * No buffers, device names, command payloads, or filesystem writes.
	 */
	printk_deferred(KERN_NOTICE
		"TGFDRC v=1 s=%08x q=%llu b=%u l=%llu n=%llu c=%u p=%u t=%u "
		"e=%08x o=%llx r=%d f=%08x a=%llx x=%08x\n",
		r->session, (unsigned long long)r->seq, r->bank,
		(unsigned long long)r->bank_seq, (unsigned long long)r->ns,
		r->cpu, r->pid, r->tid, r->event,
		(unsigned long long)r->object_id, r->rc, r->flags,
		(unsigned long long)r->a, tg_fdr_critical_checksum(r));
}
'''
    if 'tg_fdr_persist_critical(' not in text:
        if text.count(helper_anchor) != 1:
            raise SystemExit('tg_fdr.c persistent helper anchor mismatch')
        text = text.replace(helper_anchor, helper_anchor + helper, 1)

    emit_anchor = '''\tsmp_wmb();
\tWRITE_ONCE(r->commit, TG_FDR_COMMIT);

\tif ((flags & TG_FDR_FLAG_CRITICAL) ||
'''
    emit_new = '''\tsmp_wmb();
\tWRITE_ONCE(r->commit, TG_FDR_COMMIT);

\tif (flags & TG_FDR_FLAG_CRITICAL)
\t\ttg_fdr_persist_critical(r);

\tif ((flags & TG_FDR_FLAG_CRITICAL) ||
'''
    if 'tg_fdr_persist_critical(r);' not in text:
        if text.count(emit_anchor) != 1:
            raise SystemExit('tg_fdr.c critical mirror anchor mismatch')
        text = text.replace(emit_anchor, emit_new, 1)
    p.write_text(text)

    bp = root / 'kernel/tg_boot_reference.c'
    b = bp.read_text()
    old = '''\tu32 flags = (!rc && tag && (!strncmp(tag, "USER:", 5) ||
\t\t    !strncmp(tag, "MOUNT:", 6))) ? TG_FDR_FLAG_CRITICAL : 0;
'''
    new = '''\tu32 flags = (rc < 0 || (!rc && tag &&
\t\t    (!strncmp(tag, "USER:", 5) || !strncmp(tag, "MOUNT:", 6)))) ?
\t\t    TG_FDR_FLAG_CRITICAL : 0;
'''
    if old in b:
        b = b.replace(old, new, 1)
    elif new not in b:
        raise SystemExit('tg_boot_reference.c critical failure policy anchor mismatch')
    bp.write_text(b)

    if 'TGFDRC v=1' not in p.read_text():
        raise SystemExit('critical pstore marker missing')
    print('TouchGrass FDR critical PSTORE_CONSOLE mirror staged')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: touchgrass_definitive_fdr_persistence_overlay.py <kernel-root>')
    main(Path(sys.argv[1]).resolve())
