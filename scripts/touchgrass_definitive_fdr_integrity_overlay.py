#!/usr/bin/env python3
from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected one anchor, found {count}')
    return text.replace(old, new, 1)


def main(root: Path) -> None:
    hp = root / 'include/linux/tg_fdr.h'
    cp = root / 'kernel/tg_fdr.c'
    h = hp.read_text()
    c = cp.read_text()

    h = replace_once(
        h,
        'u32 tg_fdr_hash_tag(const char *tag);\n',
        'u32 tg_fdr_hash_tag(const char *tag);\n'
        'bool tg_fdr_streaming_active(void);\n',
        'streaming-active declaration')

    c = replace_once(
        c,
        '#define TG_FDR_COMMIT\t\t\t0xA52FU\n',
        '#define TG_FDR_RECORD_VERSION\t\t1U\n',
        'record version constant')

    old_record = '''struct tg_fdr_record {
\tu64 seq;
\tu64 bank_seq;
\tu64 ns;
\tu64 object_id;
\tu64 a;
\tu64 b;
\tu64 c;
\tu64 d;
\ts32 rc;
\tu32 pid;
\tu32 tid;
\tu32 flags;
\tu32 session;
\tu32 event;
\tu16 cpu;
\tu16 subsystem;
\tu16 bank;
\tu16 commit;
} __packed;
'''
    new_record = '''struct tg_fdr_record {
\tu64 seq;
\tu64 bank_seq;
\tu64 ns;
\tu64 a;
\tu64 b;
\tu64 c;
\tu64 d;
\tu32 object_id;
\ts32 rc;
\tu32 pid;
\tu32 tid;
\tu32 flags;
\tu32 session;
\tu32 event;
\tu32 checksum;
\tu16 cpu;
\tu16 subsystem;
\tu16 bank;
\tu16 record_version;
} __packed;
'''
    c = replace_once(c, old_record, new_record, '96-byte record layout')

    c = replace_once(
        c,
        'static atomic_t tg_fdr_session = ATOMIC_INIT(0);\n'
        'static DECLARE_WAIT_QUEUE_HEAD(tg_fdr_waitq);\n',
        'static atomic_t tg_fdr_session = ATOMIC_INIT(0);\n'
        'static atomic_t tg_fdr_readers = ATOMIC_INIT(0);\n'
        'static atomic64_t tg_fdr_commits = ATOMIC64_INIT(0);\n'
        'static DECLARE_WAIT_QUEUE_HEAD(tg_fdr_waitq);\n',
        'reader/commit counters')

    helper_anchor = '''static u32 tg_fdr_session_id(void)
'''
    helper = '''static inline u32 tg_fdr_checksum_mix(u32 h, u32 value)
{
\th ^= value;
\th *= 16777619U;
\treturn h;
}

static inline u32 tg_fdr_checksum_u64(u32 h, u64 value)
{
\th = tg_fdr_checksum_mix(h, (u32)value);
\treturn tg_fdr_checksum_mix(h, (u32)(value >> 32));
}

static u32 tg_fdr_record_checksum(const struct tg_fdr_record *r)
{
\tu32 h = 2166136261U;

\th = tg_fdr_checksum_u64(h, r->seq);
\th = tg_fdr_checksum_u64(h, r->bank_seq);
\th = tg_fdr_checksum_u64(h, r->ns);
\th = tg_fdr_checksum_u64(h, r->a);
\th = tg_fdr_checksum_u64(h, r->b);
\th = tg_fdr_checksum_u64(h, r->c);
\th = tg_fdr_checksum_u64(h, r->d);
\th = tg_fdr_checksum_mix(h, r->object_id);
\th = tg_fdr_checksum_mix(h, (u32)r->rc);
\th = tg_fdr_checksum_mix(h, r->pid);
\th = tg_fdr_checksum_mix(h, r->tid);
\th = tg_fdr_checksum_mix(h, r->flags);
\th = tg_fdr_checksum_mix(h, r->session);
\th = tg_fdr_checksum_mix(h, r->event);
\th = tg_fdr_checksum_mix(h, (u32)r->cpu | ((u32)r->subsystem << 16));
\th = tg_fdr_checksum_mix(h, (u32)r->bank |
\t\t\t       ((u32)r->record_version << 16));
\treturn h ? h : 1U;
}

bool tg_fdr_streaming_active(void)
{
\treturn atomic_read(&tg_fdr_readers) > 0;
}

'''
    if helper not in c:
        if c.count(helper_anchor) != 1:
            raise SystemExit('checksum helper anchor mismatch')
        c = c.replace(helper_anchor, helper + helper_anchor, 1)

    c = replace_once(
        c,
        '''\tWRITE_ONCE(r->commit, 0);
\tsmp_wmb();
\tr->bank_seq = local_seq;
\tr->ns = ktime_get_ns();
\tr->object_id = object_id;
\tr->a = a;
\tr->b = b;
\tr->c = c;
\tr->d = d;
\tr->rc = rc;
\tr->pid = (u32)task_tgid_nr(current);
\tr->tid = (u32)task_pid_nr(current);
\tr->flags = flags;
\tr->session = tg_fdr_session_id();
\tr->event = event;
\tr->cpu = (u16)raw_smp_processor_id();
\tr->subsystem = subsystem;
\tr->bank = bank_id;
\tr->seq = global_seq;
\tsmp_wmb();
\tWRITE_ONCE(r->commit, TG_FDR_COMMIT);

\tif ((flags & TG_FDR_FLAG_CRITICAL) ||
\t    !(global_seq & (TG_FDR_WAKE_GRANULARITY - 1)))
\t\twake_up_interruptible(&tg_fdr_waitq);
''',
        '''\tWRITE_ONCE(r->checksum, 0);
\tsmp_wmb();
\tr->bank_seq = local_seq;
\tr->ns = ktime_get_ns();
\tr->a = a;
\tr->b = b;
\tr->c = c;
\tr->d = d;
\tr->object_id = object_id;
\tr->rc = rc;
\tr->pid = (u32)task_tgid_nr(current);
\tr->tid = (u32)task_pid_nr(current);
\tr->flags = flags;
\tr->session = tg_fdr_session_id();
\tr->event = event;
\tr->cpu = (u16)raw_smp_processor_id();
\tr->subsystem = subsystem;
\tr->bank = bank_id;
\tr->record_version = TG_FDR_RECORD_VERSION;
\tr->seq = global_seq;
\tsmp_wmb();
\tWRITE_ONCE(r->checksum, tg_fdr_record_checksum(r));
\tatomic64_inc(&tg_fdr_commits);

\tif ((flags & TG_FDR_FLAG_CRITICAL) ||
\t    !(global_seq & (TG_FDR_WAKE_GRANULARITY - 1)))
\t\twake_up_interruptible(&tg_fdr_waitq);
''',
        'record publication')

    c = replace_once(
        c,
        '''\tu16 commit_before, commit_after;
\tu64 seq_before, bank_seq_before;
''',
        '''\tu32 checksum_before, checksum_after;
\tu64 seq_before, bank_seq_before;
''',
        'reader checksum locals')

    c = replace_once(
        c,
        '''\tcommit_before = READ_ONCE(src->commit);
\tbank_seq_before = READ_ONCE(src->bank_seq);
\tseq_before = READ_ONCE(src->seq);
\tif (commit_before != TG_FDR_COMMIT || bank_seq_before != next || !seq_before)
\t\treturn false;
\tsmp_rmb();
\tmemcpy(out, src, sizeof(*out));
\tsmp_rmb();
\tcommit_after = READ_ONCE(src->commit);
\tif (commit_after != TG_FDR_COMMIT ||
\t    READ_ONCE(src->bank_seq) != bank_seq_before ||
\t    READ_ONCE(src->seq) != seq_before)
\t\treturn false;
\treturn true;
''',
        '''\tchecksum_before = READ_ONCE(src->checksum);
\tbank_seq_before = READ_ONCE(src->bank_seq);
\tseq_before = READ_ONCE(src->seq);
\tif (!checksum_before || bank_seq_before != next || !seq_before ||
\t    READ_ONCE(src->record_version) != TG_FDR_RECORD_VERSION)
\t\treturn false;
\tsmp_rmb();
\tmemcpy(out, src, sizeof(*out));
\tsmp_rmb();
\tchecksum_after = READ_ONCE(src->checksum);
\tif (checksum_after != checksum_before ||
\t    READ_ONCE(src->bank_seq) != bank_seq_before ||
\t    READ_ONCE(src->seq) != seq_before ||
\t    READ_ONCE(src->record_version) != TG_FDR_RECORD_VERSION ||
\t    out->checksum != tg_fdr_record_checksum(out))
\t\treturn false;
\treturn true;
''',
        'reader publication verification')

    c = replace_once(
        c,
        '\trd->seen_global = best.seq;\n',
        '\tif (best.seq > rd->seen_global)\n\t\trd->seen_global = best.seq;\n',
        'monotonic reader sequence')

    c = replace_once(
        c,
        '''\trd->seen_global = (u64)atomic64_read(&tg_fdr_global_seq);
\tfile->private_data = rd;
\treturn 0;
''',
        '''\trd->seen_global = (u64)atomic64_read(&tg_fdr_global_seq);
\tfile->private_data = rd;
\tatomic_inc(&tg_fdr_readers);
\treturn 0;
''',
        'reader activation')

    c = replace_once(
        c,
        '''static int tg_fdr_release(struct inode *inode, struct file *file)
{
\tkfree(file->private_data);
\treturn 0;
}
''',
        '''static int tg_fdr_release(struct inode *inode, struct file *file)
{
\tif (file->private_data)
\t\tatomic_dec_if_positive(&tg_fdr_readers);
\tkfree(file->private_data);
\treturn 0;
}
''',
        'reader deactivation')

    old_wait = '''\t\tret = wait_event_interruptible(tg_fdr_waitq,
\t\t\t(u64)atomic64_read(&tg_fdr_global_seq) != rd->seen_global);
\t\tif (ret)
\t\t\treturn ret;
'''
    new_wait = '''\t\t{
\t\t\tu64 seen_commits = (u64)atomic64_read(&tg_fdr_commits);
\t\t\tstruct tg_fdr_record retry;
\t\t\tif (tg_fdr_reader_next(rd, &retry)) {
\t\t\t\tif (copy_to_user(buf, &retry, sizeof(retry)))
\t\t\t\t\treturn -EFAULT;
\t\t\t\treturn sizeof(retry);
\t\t\t}
\t\t\tret = wait_event_interruptible(tg_fdr_waitq,
\t\t\t\t(u64)atomic64_read(&tg_fdr_commits) != seen_commits);
\t\t}
\t\tif (ret)
\t\t\treturn ret;
'''
    c = replace_once(c, old_wait, new_wait, 'commit-generation wait')

    c = replace_once(
        c,
        '''\t\t   "capacity_per_bank=%u total_capacity=%u reader_lost=%llu\\n",
''',
        '''\t\t   "capacity_per_bank=%u total_capacity=%u reader_lost=%llu active_readers=%d commits=%llu\\n",
''',
        'stats format')
    c = replace_once(
        c,
        '''\t\t   TG_FDR_BANK_COUNT * TG_FDR_RECORDS_PER_BANK,
\t\t   (unsigned long long)atomic64_read(&tg_fdr_reader_lost));
''',
        '''\t\t   TG_FDR_BANK_COUNT * TG_FDR_RECORDS_PER_BANK,
\t\t   (unsigned long long)atomic64_read(&tg_fdr_reader_lost),
\t\t   atomic_read(&tg_fdr_readers),
\t\t   (unsigned long long)atomic64_read(&tg_fdr_commits));
''',
        'stats arguments')

    c = replace_once(
        c,
        '''\t\tseq_printf(m,
\t\t\t   "bank=%u attempted=%llu retained=%llu wraps=%llu high_water=%llu\\n",
''',
        '''\t\tseq_printf(m,
\t\t\t   "bank=%u attempted=%llu retained=%llu overwritten=%llu wraps=%llu high_water=%llu\\n",
''',
        'bank stats format')
    c = replace_once(
        c,
        '''\t\t\t   (unsigned long long)(n > TG_FDR_RECORDS_PER_BANK ?
\t\t\t\t\t\tTG_FDR_RECORDS_PER_BANK : n),
\t\t\t   (unsigned long long)(n / TG_FDR_RECORDS_PER_BANK),
\t\t\t   (unsigned long long)(n > TG_FDR_RECORDS_PER_BANK ?
''',
        '''\t\t\t   (unsigned long long)(n > TG_FDR_RECORDS_PER_BANK ?
\t\t\t\t\t\tTG_FDR_RECORDS_PER_BANK : n),
\t\t\t   (unsigned long long)(n > TG_FDR_RECORDS_PER_BANK ?
\t\t\t\t\t\tn - TG_FDR_RECORDS_PER_BANK : 0),
\t\t\t   (unsigned long long)(n > TG_FDR_RECORDS_PER_BANK ?
\t\t\t\t\t\t(n - 1) / TG_FDR_RECORDS_PER_BANK : 0),
\t\t\t   (unsigned long long)(n > TG_FDR_RECORDS_PER_BANK ?
''',
        'bank stats values')

    hp.write_text(h)
    cp.write_text(c)

    if 'u32 checksum;' not in c or 'tg_fdr_streaming_active' not in c:
        raise SystemExit('integrity overlay verification failed')
    print('TouchGrass definitive FDR integrity v2 staged')
    print('record_bytes=96 checksum=fnv1a32-numeric publication=checksum-last')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: touchgrass_definitive_fdr_integrity_overlay.py <kernel-root>')
    main(Path(sys.argv[1]).resolve())
