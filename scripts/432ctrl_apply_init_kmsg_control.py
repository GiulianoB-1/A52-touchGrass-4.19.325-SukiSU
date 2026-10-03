#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK = 'A52_PHASE432_CTRL_INIT_KMSG_V1'
SYSCALL = Path('arch/arm64/kernel/syscall.c')
REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
PRINTK = Path('kernel/printk/printk.c')
EXEC = Path('fs/exec.c')
EXIT = Path('kernel/exit.c')
CORE = Path('fs/coredump.c')


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'P432C {label}: expected 1 anchor, found {n}')
    return text.replace(old, new, 1)


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1' not in text:
        raise SystemExit('P432C requires Phase414 sequential recorder')
    anchor = '''\tif (queue_disk)\n\t\ta52_p414_queue_disk();\n}\n\nstatic int __init a52_p414_init(void)\n'''
    insert = '''\tif (queue_disk)\n\t\ta52_p414_queue_disk();\n}\n\n/* A52_PHASE432_CTRL_INIT_KMSG_V1
 * Lightweight text-only writer for the control experiment.  This deliberately
 * bypasses the older R48 event path and appends only to the sequential Samsung
 * recorder tier.  a52_p414_append_text() already truncates to its 88-byte text
 * field and batches disk flushing on the unbound workqueue.
 */
void a52_ackfr_p432c_record(const char *fmt, ...)
{
\tchar message[88];
\tva_list args;

\tif (!fmt)
\t\treturn;
\tva_start(args, fmt);
\tvscnprintf(message, sizeof(message), fmt, args);
\tva_end(args);
\ta52_p414_append_text(message);
}
EXPORT_SYMBOL_GPL(a52_ackfr_p432c_record);

static int __init a52_p414_init(void)
'''
    text = one(text, anchor, insert, 'Samsung-tier writer export')
    text += '\n/* ' + MARK + ': direct sequential-tier writer for passive init/exit capture. */\n'
    return text


def patch_syscall(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_PHASE432_SF_HWC_BOOTANIM_CHAIN_V1' not in text:
        raise SystemExit('P432C requires Phase432 syscall lineage')

    start = text.find('static void a52_p430_snapshot(unsigned int snapshot_id)\n')
    mid = text.find('static int a52_p430_sampler_fn(void *unused)\n', start)
    end = text.find('static int __init a52_p430_init(void)\n', mid)
    if start < 0 or mid < 0 or end < 0:
        raise SystemExit('P432C snapshot/sampler boundaries missing')
    replacement = '''static void __maybe_unused a52_p430_snapshot(unsigned int snapshot_id)
{
\t/* Phase432-CTRL: all periodic task snapshots are intentionally disabled. */
\t(void)snapshot_id;
}

static __maybe_unused int a52_p430_sampler_fn(void *unused)
{
\t/* Phase432-CTRL: no task walk, wchan, remote stack, or register sampling. */
\t(void)unused;
\treturn 0;
}

'''
    text = text[:start] + replacement + text[end:]

    old = '''\ta52_p430_sampler = kthread_run(a52_p430_sampler_fn, NULL, "a52_p430_sf");
\tif (IS_ERR(a52_p430_sampler))
\t\ta52_p430_sampler = NULL;
\t/* Phase432: CPU7 busy witness intentionally disabled to reduce perturbation. */
\treturn 0;
'''
    new = '''\t/* Phase432-CTRL: no userspace sampler thread is started. */
\t/* Phase432: CPU7 busy witness intentionally disabled to reduce perturbation. */
\treturn 0;
'''
    text = one(text, old, new, 'disable task sampler kthread')

    f0 = text.find('static void a52_p430_fill(')
    f1 = text.find('\nstatic void a52_p430_write(', f0)
    if f0 < 0 or f1 < 0:
        raise SystemExit('P432C fill boundaries missing')
    body = text[f0:f1]
    body = body.replace('\tstruct pt_regs *regs;\n', '')
    heavy = '''\tget_task_comm(r->comm, p);
\tif (heavy) {
\t\tr->wchan = (u64)get_wchan(p);
\t\tif (r->wchan)
\t\t\tsprint_symbol(r->wchan_symbol, (unsigned long)r->wchan);
\t\tif (try_get_task_stack(p)) {
\t\t\tr->stack_nr = stack_trace_save_tsk(p, r->stack, A52_P430_STACK, 0);
\t\t\tif (p->mm) {
\t\t\t\tregs = task_pt_regs(p);
\t\t\t\tr->user_pc = READ_ONCE(regs->pc);
\t\t\t\tr->user_lr = READ_ONCE(regs->regs[30]);
\t\t\t\tr->user_sp = READ_ONCE(regs->sp);
\t\t\t}
\t\t\tput_task_stack(p);
\t\t}
\t}
'''
    safe = '''\tget_task_comm(r->comm, p);
\t/* Phase432-CTRL: metadata only; never inspect a remote task stack/registers. */
\t(void)heavy;
'''
    if heavy not in body:
        raise SystemExit('P432C Phase432 heavy-fill anchor missing')
    body = body.replace(heavy, safe, 1)
    text = text[:f0] + body + text[f1:]
    text += '\n/* ' + MARK + ': periodic userspace sampler and remote unwind path disabled. */\n'
    return text


def patch_printk(text: str) -> str:
    if MARK in text:
        return text
    sig = 'static ssize_t devkmsg_write(struct kiocb *iocb, struct iov_iter *from)\n'
    pos = text.find(sig)
    if pos < 0:
        raise SystemExit('P432C devkmsg_write missing')

    helper = r'''/* A52_PHASE432_CTRL_INIT_KMSG_V1
 * Passive userspace /dev/kmsg tap.  The copied iterator is never consumed from
 * the real write path.  No allocation, printk, console call, task walk, or
 * remote task access occurs here.
 */
extern void a52_ackfr_p432c_record(const char *fmt, ...);
#define A52_P432C_INIT_GENERAL_LIMIT 600U
#define A52_P432C_KMSG_COPY          192U
static atomic_t a52_p432c_init_seen = ATOMIC_INIT(0);
static atomic_t a52_p432c_kmsg_saved = ATOMIC_INIT(0);

static bool a52_p432c_kmsg_source(void)
{
\treturn !strcmp(current->comm, "init") ||
\t       !strcmp(current->comm, "ueventd") ||
\t       !strcmp(current->comm, "bootanimation") ||
\t       !strcmp(current->comm, "surfaceflinger");
}

static bool a52_p432c_kmsg_priority(const char *s)
{
\treturn strstr(s, "took") || strstr(s, "exited") ||
\t       strstr(s, "killed") || strstr(s, "Wait for") ||
\t       strstr(s, "wait_for_prop") || strstr(s, "starting service") ||
\t       strstr(s, "processing action") || strstr(s, "bootanim") ||
\t       strstr(s, "SurfaceFlinger") || strstr(s, "surfaceflinger") ||
\t       strstr(s, "odsign") || strstr(s, "odrefresh") ||
\t       strstr(s, "init_user0") || strstr(s, "restorecon") ||
\t       strstr(s, "fsverity") || strstr(s, "apexd") ||
\t       strstr(s, "keystore") || strstr(s, "vold") ||
\t       strstr(s, "keymaster") || strstr(s, "KeyMint") ||
\t       strstr(s, "keymint") || strstr(s, "qseecom");
}

static void a52_p432c_capture_devkmsg(struct iov_iter *from)
{
\tstruct iov_iter mirror;
\tchar text[A52_P432C_KMSG_COPY];
\tsize_t n;
\tunsigned int seen = 0, saved;
\tbool init_writer, priority;

\tif (!from || !a52_p432c_kmsg_source())
\t\treturn;
\tmirror = *from;
\tn = min_t(size_t, iov_iter_count(&mirror), sizeof(text) - 1U);
\tif (!n || copy_from_iter(text, n, &mirror) != n)
\t\treturn;
\ttext[n] = '\0';
\tpriority = a52_p432c_kmsg_priority(text);
\tinit_writer = !strcmp(current->comm, "init");
\tif (init_writer) {
\t\tseen = (unsigned int)atomic_inc_return(&a52_p432c_init_seen);
\t\tif (seen > A52_P432C_INIT_GENERAL_LIMIT && !priority)
\t\t\treturn;
\t} else if (!priority) {
\t\treturn;
\t}
\tsaved = (unsigned int)atomic_inc_return(&a52_p432c_kmsg_saved);
\ta52_ackfr_p432c_record("P432C K n=%u c=%.12s %.54s",
\t\t\t\t saved, current->comm, text);
}

'''
    text = text[:pos] + helper + text[pos:]

    anchor = '''\tif (!user || len > LOG_LINE_MAX)
\t\treturn -EINVAL;

\t/* Ignore when user logging is disabled. */
'''
    repl = '''\tif (!user || len > LOG_LINE_MAX)
\t\treturn -EINVAL;

\ta52_p432c_capture_devkmsg(from);

\t/* Ignore when user logging is disabled. */
'''
    if anchor not in text:
        anchor = '''\tif (len > PRINTKRB_RECORD_MAX)
\t\treturn -EINVAL;

\t/* Ignore when user logging is disabled. */
'''
        repl = '''\tif (len > PRINTKRB_RECORD_MAX)
\t\treturn -EINVAL;

\ta52_p432c_capture_devkmsg(from);

\t/* Ignore when user logging is disabled. */
'''
    text = one(text, anchor, repl, 'devkmsg pre-ratelimit hook')
    return text


def patch_exec(text: str) -> str:
    if MARK in text:
        return text
    if 'A52_R212_EXEC_LIMIT' not in text:
        raise SystemExit('P432C requires Phase212 exec trace')

    old = '''\treturn path && (strstr(path, "surfaceflinger") ||
\t\tstrstr(path, "composer") || strstr(path, "display") ||
\t\tstrstr(path, "gralloc") || strstr(path, "allocator") ||
\t\tstrstr(path, "mapper"));
'''
    new = '''\treturn path && (strstr(path, "surfaceflinger") ||
\t\tstrstr(path, "bootanimation") || strstr(path, "composer") ||
\t\tstrstr(path, "display") || strstr(path, "gralloc") ||
\t\tstrstr(path, "allocator") || strstr(path, "mapper"));
'''
    text = one(text, old, new, 'add bootanimation to exec filter')

    anchor = '''\tif (a52_r212_graphics_exec(filename->name)) {
\t\ttrace_id = atomic_inc_return(&a52_r212_exec_sequence);
\t\ttrace = trace_id <= A52_R212_EXEC_LIMIT;
'''
    repl = '''\tif (a52_r212_graphics_exec(filename->name)) {
\t\tif (strstr(filename->name, "surfaceflinger"))
\t\t\ta52_ackfr_p432c_record("P432C SFEXEC p=%d t=%d", current->pid, current->tgid);
\t\telse if (strstr(filename->name, "bootanimation"))
\t\t\ta52_ackfr_p432c_record("P432C BAEXEC p=%d t=%d", current->pid, current->tgid);
\t\ttrace_id = atomic_inc_return(&a52_r212_exec_sequence);
\t\ttrace = trace_id <= A52_R212_EXEC_LIMIT;
'''
    text = one(text, anchor, repl, 'passive exec milestones')

    include_anchor = '#include <trace/events/sched.h>\n\n#define A52_R212_EXEC_LIMIT 96\n'
    include_repl = '#include <trace/events/sched.h>\n\nextern void a52_ackfr_p432c_record(const char *fmt, ...);\n#define A52_R212_EXEC_LIMIT 96\n'
    text = one(text, include_anchor, include_repl, 'exec recorder declaration')
    text += '\n/* ' + MARK + ': bootanimation exec + SF/BA passive milestones. */\n'
    return text


def patch_exit(text: str) -> str:
    if MARK in text:
        return text
    sig = 'void\ndo_group_exit(int exit_code)\n{\n\tstruct signal_struct *sig = current->signal;\n'
    if sig not in text:
        sig = 'void do_group_exit(int exit_code)\n{\n\tstruct signal_struct *sig = current->signal;\n'
    if sig not in text:
        raise SystemExit('P432C do_group_exit anchor missing')

    helper = r'''/* A52_PHASE432_CTRL_INIT_KMSG_V1 */
extern void a52_ackfr_p432c_record(const char *fmt, ...);
static bool a52_p432c_exit_target(void)
{
\tconst char *comm = current->group_leader ? current->group_leader->comm : current->comm;
\treturn !strcmp(comm, "surfaceflinger") || !strcmp(comm, "bootanimation");
}

'''
    comment = '/*\n * Take down every thread in the group.  This is called by fatal signals\n'
    p = text.find(comment)
    if p < 0:
        comment = '/*\n * Take down every thread in the group. This is called by fatal signals\n'
        p = text.find(comment)
    if p < 0:
        raise SystemExit('P432C group-exit comment anchor missing')
    text = text[:p] + helper + text[p:]

    old = sig
    new = sig + '''\n\tif (a52_p432c_exit_target()) {
\t\tconst char *comm = current->group_leader ? current->group_leader->comm : current->comm;
\t\ta52_ackfr_p432c_record("P432C EXIT t=%d p=%d code=%x st=%u sig=%u pf=%u c=%.15s",
\t\t\tcurrent->tgid, current->pid, exit_code,
\t\t\t(unsigned int)((exit_code >> 8) & 0xff),
\t\t\t(unsigned int)(exit_code & 0x7f),
\t\t\t!!(current->flags & PF_SIGNALED), comm);
\t}
'''
    text = one(text, old, new, 'group exit record')
    text += '\n/* ' + MARK + ': process-group exit cause for SF/bootanimation. */\n'
    return text


def patch_core(text: str) -> str:
    if MARK in text:
        return text
    inc = '#include <linux/coredump.h>\n'
    text = one(text, inc, inc + 'extern void a52_ackfr_p432c_record(const char *fmt, ...);\n', 'coredump recorder declaration')
    anchor = '''void do_coredump(const kernel_siginfo_t *siginfo)
{
\tstruct core_state core_state;
'''
    repl = '''void do_coredump(const kernel_siginfo_t *siginfo)
{
\tconst char *a52_p432c_comm = current->group_leader ? current->group_leader->comm : current->comm;
\tstruct core_state core_state;

\tif (!strcmp(a52_p432c_comm, "surfaceflinger") ||
\t    !strcmp(a52_p432c_comm, "bootanimation"))
\t\ta52_ackfr_p432c_record("P432C CORE t=%d p=%d sig=%d c=%.15s",
\t\t\tcurrent->tgid, current->pid, siginfo->si_signo, a52_p432c_comm);
'''
    text = one(text, anchor, repl, 'core request record')
    text += '\n/* ' + MARK + ': core-dump request marker for SF/bootanimation. */\n'
    return text


def validate(root: Path) -> None:
    s = (root / SYSCALL).read_text(errors='replace')
    r = (root / REC).read_text(errors='replace')
    p = (root / PRINTK).read_text(errors='replace')
    e = (root / EXEC).read_text(errors='replace')
    x = (root / EXIT).read_text(errors='replace')
    c = (root / CORE).read_text(errors='replace')

    for tok in (MARK, 'a52_ackfr_p432c_record(const char *fmt, ...)', 'a52_p414_append_text(message)'):
        if tok not in r:
            raise SystemExit('P432C recorder token missing: ' + tok)
    for tok in (MARK, 'P432C K n=%u c=%.12s %.54s', 'A52_P432C_INIT_GENERAL_LIMIT 600U',
                'odsign', 'odrefresh', 'init_user0', 'restorecon', 'fsverity', 'apexd',
                'keystore', 'vold', 'keymaster', 'KeyMint', 'qseecom', 'a52_p432c_capture_devkmsg(from)'):
        if tok not in p:
            raise SystemExit('P432C printk token missing: ' + tok)
    if p.find('a52_p432c_capture_devkmsg(from);') > p.find('/* Ratelimit when not explicitly enabled. */'):
        raise SystemExit('P432C devkmsg hook landed after ratelimit')

    for tok in (MARK, 'P432C SFEXEC p=%d t=%d', 'P432C BAEXEC p=%d t=%d', 'bootanimation'):
        if tok not in e:
            raise SystemExit('P432C exec token missing: ' + tok)
    for tok in (MARK, 'P432C EXIT t=%d p=%d code=%x st=%u sig=%u pf=%u c=%.15s'):
        if tok not in x:
            raise SystemExit('P432C exit token missing: ' + tok)
    for tok in (MARK, 'P432C CORE t=%d p=%d sig=%d c=%.15s'):
        if tok not in c:
            raise SystemExit('P432C core token missing: ' + tok)

    if 'kthread_run(a52_p430_sampler_fn' in s:
        raise SystemExit('P432C userspace sampler still starts')
    f0 = s.find('static void a52_p430_fill(')
    f1 = s.find('\nstatic void a52_p430_write(', f0)
    body = s[f0:f1]
    for bad in ('get_wchan(', 'stack_trace_save_tsk(', 'task_pt_regs(', 'try_get_task_stack('):
        if bad in body:
            raise SystemExit('P432C remote task inspection remains: ' + bad)
    sampler0 = s.find('static __maybe_unused int a52_p430_sampler_fn')
    sampler1 = s.find('static int __init a52_p430_init', sampler0)
    sampler = s[sampler0:sampler1]
    for bad in ('a52_p430_find_leader(', 'a52_p430_snapshot(', 'msleep_interruptible('):
        if bad in sampler:
            raise SystemExit('P432C active sampler work remains: ' + bad)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    ns = ap.parse_args()
    for rel in (SYSCALL, REC, PRINTK, EXEC, EXIT, CORE):
        if not (ns.root / rel).is_file():
            raise SystemExit('P432C source missing: ' + str(rel))
    if not ns.check_only:
        for rel, fn in ((REC, patch_rec), (SYSCALL, patch_syscall), (PRINTK, patch_printk),
                        (EXEC, patch_exec), (EXIT, patch_exit), (CORE, patch_core)):
            q = ns.root / rel
            q.write_text(fn(q.read_text(errors='replace')))
    validate(ns.root)
    print('Phase432-CTRL passive init/KMSG control: PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
