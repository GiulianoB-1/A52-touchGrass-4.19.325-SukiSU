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

static void tg_fdr_persist_critical(const struct tg_fdr_record *r)
{
	/*
	 * Sparse metadata-only mirror into the existing PSTORE_CONSOLE path.
	 * No dynamic names, buffers, commands, or filesystem writes.
	 */
	printk_deferred(KERN_NOTICE
		"TGFDRC v=2 s=%08x q=%llu b=%u l=%llu n=%llu c=%u p=%u t=%u "
		"e=%08x o=%08x r=%d f=%08x a=%llx b0=%llx c0=%llx d=%llx x=%08x\n",
		r->session, (unsigned long long)r->seq, r->bank,
		(unsigned long long)r->bank_seq, (unsigned long long)r->ns,
		r->cpu, r->pid, r->tid, r->event, r->object_id,
		r->rc, r->flags,
		(unsigned long long)r->a, (unsigned long long)r->b,
		(unsigned long long)r->c, (unsigned long long)r->d,
		r->checksum);
}
'''
    if 'tg_fdr_persist_critical(' not in text:
        if text.count(helper_anchor) != 1:
            raise SystemExit('tg_fdr.c persistent helper anchor mismatch')
        text = text.replace(helper_anchor, helper_anchor + helper, 1)

    emit_anchor = '''\tWRITE_ONCE(r->checksum, tg_fdr_record_checksum(r));
\tatomic64_inc(&tg_fdr_commits);

\tif ((flags & TG_FDR_FLAG_CRITICAL) ||
'''
    emit_new = '''\tWRITE_ONCE(r->checksum, tg_fdr_record_checksum(r));
\tatomic64_inc(&tg_fdr_commits);

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
    new = '''\tu32 flags = 0;

\tif (!rc && tag && (!strncmp(tag, "USER:", 5) ||
\t\t\t   !strncmp(tag, "MOUNT:", 6)))
\t\tflags |= TG_FDR_FLAG_CRITICAL;
\tif (rc < 0 && tag && strncmp(tag, "PROBE:", 6) &&
\t    strncmp(tag, "INITCALL:", 9))
\t\tflags |= TG_FDR_FLAG_CRITICAL;
'''
    if old in b:
        b = b.replace(old, new, 1)
    elif new not in b:
        raise SystemExit('tg_boot_reference.c critical policy anchor mismatch')
    bp.write_text(b)

    gp = root / 'kernel/tg_gpu_reference.c'
    g = gp.read_text()
    old_gpu = '''\ttg_fdr_emit_tag(tg_gpu_ref_subsystem(tag), tag, rc, 0, a, b, c, d, 0);
'''
    new_gpu = '''\ttg_fdr_emit_tag(tg_gpu_ref_subsystem(tag), tag, rc, 0, a, b, c, d,
\t\t\trc < 0 ? TG_FDR_FLAG_CRITICAL : 0);
'''
    if old_gpu in g:
        g = g.replace(old_gpu, new_gpu, 1)
    elif new_gpu not in g:
        raise SystemExit('tg_gpu_reference.c critical policy anchor mismatch')
    gp.write_text(g)

    if 'TGFDRC v=2' not in p.read_text():
        raise SystemExit('critical pstore marker missing')
    print('TouchGrass FDR sparse critical PSTORE_CONSOLE mirror staged')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: touchgrass_definitive_fdr_persistence_overlay.py <kernel-root>')
    main(Path(sys.argv[1]).resolve())
