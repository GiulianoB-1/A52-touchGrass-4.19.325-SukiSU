#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK='A52_PHASE432_SF_HWC_BOOTANIM_CHAIN_V1'
SYSCALL=Path('arch/arm64/kernel/syscall.c')
MSM=Path('drivers/a52_display/msm/msm_drv.c')
REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')


def one(text:str, old:str, new:str, label:str)->str:
    n=text.count(old)
    if n!=1:
        raise SystemExit(f'Phase432 {label}: expected 1 anchor, found {n}')
    return text.replace(old,new,1)


def patch_rec(text:str)->str:
    if MARK in text:
        return text
    if 'A52_PHASE431_SAFE_SF_GAP_FORENSICS_V1' not in text:
        raise SystemExit('Phase432 requires Phase431 recorder lineage')

    old='''static atomic_t a52_r268_comp_pid = ATOMIC_INIT(-1);
static atomic_t a52_r268_exec_seen = ATOMIC_INIT(0);
'''
    new='''static atomic_t a52_r268_comp_pid = ATOMIC_INIT(-1);
static atomic_t a52_r268_exec_seen = ATOMIC_INIT(0);

/* A52_PHASE432_SF_HWC_BOOTANIM_CHAIN_V1
 * Phase268/269 guessed Composer from the executable path. Phase431 proved
 * that can latch the wrong QTI display service. The successful DRM primary
 * open is now the authoritative owner identity; old exec identity is fallback
 * only until the real owner is known.
 */
static atomic_t a52_p432_drm_owner_tgid = ATOMIC_INIT(-1);

void a52_ackfr_phase432_note_drm_owner(pid_t tgid)
{
	if (tgid > 0)
		atomic_cmpxchg(&a52_p432_drm_owner_tgid, -1, (int)tgid);
}
EXPORT_SYMBOL_GPL(a52_ackfr_phase432_note_drm_owner);

int a52_ackfr_phase432_drm_owner_tgid(void)
{
	return atomic_read(&a52_p432_drm_owner_tgid);
}
EXPORT_SYMBOL_GPL(a52_ackfr_phase432_drm_owner_tgid);
'''
    text=one(text,old,new,'DRM owner identity state')

    old='''bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid)
{
	return a52_r268_is_composer_pid((int)tgid);
}
'''
    new='''bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid)
{
	int owner = atomic_read(&a52_p432_drm_owner_tgid);
	if (owner > 0)
		return tgid == owner;
	return a52_r268_is_composer_pid((int)tgid);
}
'''
    text=one(text,old,new,'authoritative composer helper')

    text=one(text,
        'return !strncmp(message, "P431 ", 5) ||\n',
        'return !strncmp(message, "P432 ", 5) ||\n\t       !strncmp(message, "P431 ", 5) ||\n',
        'critical P432 admission')
    n=text.count('strncmp(fmt, "P431", 4) &&')
    if n < 3:
        raise SystemExit(f'Phase432 expected >=3 P431 recorder gates, found {n}')
    text=text.replace('strncmp(fmt, "P431", 4) &&',
                      'strncmp(fmt, "P432", 4) &&\n\t    strncmp(fmt, "P431", 4) &&')
    text += '\n/* '+MARK+': real DRM-owner identity + P432 chronology admitted. */\n'
    return text


def patch_msm(text:str)->str:
    if MARK in text:
        return text
    if 'A52_PHASE418_ATOMIC_PRESENT_DISCRIMINATOR_V1' not in text:
        raise SystemExit('Phase432 requires Phase418 MSM atomic discriminator')

    old='''extern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);

static bool a52_r269_is_composer_task(void)
'''
    new='''extern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);
extern void a52_ackfr_phase432_note_drm_owner(pid_t tgid);
extern int a52_ackfr_phase432_drm_owner_tgid(void);
extern void a52_p432_frontier(u32 stage, pid_t tgid, u32 aux);
static atomic_t a52_p432_owner_open_seq = ATOMIC_INIT(0);
static atomic_t a52_p432_owner_ioctl_seq = ATOMIC_INIT(0);

static bool a52_r269_is_composer_task(void)
'''
    text=one(text,old,new,'Phase432 MSM externs')

    old='''	rc = context_init(dev, file);
	if (trace)
		a52_ackfr_record("DRMPOST 211 open-exit n=%u rc=%d",
				  trace_id, rc);
'''
    new='''	rc = context_init(dev, file);
	if (!rc && file && file->minor &&
	    file->minor->type == DRM_MINOR_PRIMARY &&
	    file->minor->index == 0) {
		unsigned int a52_p432_n = (unsigned int)atomic_inc_return(&a52_p432_owner_open_seq);
		a52_ackfr_phase432_note_drm_owner(current->tgid);
		a52_ackfr_record("P432 OWN n=%u p=%d t=%d c=%.15s idx=%d",
			a52_p432_n, current->pid, current->tgid, current->comm,
			file->minor->index);
		a52_p432_frontier(0x20U, current->tgid, a52_p432_n);
	}
	if (trace)
		a52_ackfr_record("DRMPOST 211 open-exit n=%u rc=%d",
				  trace_id, rc);
'''
    text=one(text,old,new,'primary DRM owner hook')

    for fn in ('a52_r211_drm_ioctl','a52_r211_drm_compat_ioctl'):
        start=text.find('static long '+fn+'(')
        if start<0: raise SystemExit('Phase432 missing '+fn)
        nxt=text.find('\nstatic ', start+10)
        if nxt<0: nxt=len(text)
        body=text[start:nxt]
        old='''	composer = a52_r269_is_composer_task();
	if (composer)
		composer_id = atomic_inc_return(&a52_r269_ioctl_sequence);
'''
        new='''	composer = a52_r269_is_composer_task();
	if (composer) {
		unsigned int a52_p432_io = (unsigned int)atomic_inc_return(&a52_p432_owner_ioctl_seq);
		composer_id = atomic_inc_return(&a52_r269_ioctl_sequence);
		if (a52_p432_io <= 16U)
			a52_ackfr_record("P432 IO n=%u p=%d t=%d nr=0x%x",
				a52_p432_io, current->pid, current->tgid, _IOC_NR(cmd));
		if (a52_p432_io == 1U)
			a52_p432_frontier(0x26U, current->tgid, _IOC_NR(cmd));
	}
'''
        body=one(body,old,new,fn+' owner ioctl chronology')
        text=text[:start]+body+text[nxt:]

    old='''	test = !!(a.flags & DRM_MODE_ATOMIC_TEST_ONLY);
	if (test)
		atomic_inc(&a52_p418_atomic_test);
	else
		atomic_inc(&a52_p418_atomic_real);
'''
    new='''	test = !!(a.flags & DRM_MODE_ATOMIC_TEST_ONLY);
	if (test)
		atomic_inc(&a52_p418_atomic_test);
	else
		atomic_inc(&a52_p418_atomic_real);
	if (*seq <= 4U || !test)
		a52_ackfr_record("P432 AT n=%u t=%d test=%u fl=%x objs=%u",
			*seq, current->tgid, test, a.flags, a.count_objs);
	a52_p432_frontier(test ? 0x27U : 0x28U, current->tgid, *seq);
'''
    text=one(text,old,new,'atomic frontier')

    text += '\n/* '+MARK+': primary card0 owner is authoritative Composer/HWC identity. */\n'
    return text


def patch_syscall(text:str)->str:
    if MARK in text:
        return text
    if 'A52_PHASE431_SAFE_SF_GAP_FORENSICS_V1' not in text:
        raise SystemExit('Phase432 requires Phase431 syscall lineage')

    text=one(text,'#define A52_P431_WITNESS_BYTES       0x0200U\n',
                  '#define A52_P431_WITNESS_BYTES       0x0100U\n','snapshot witness bytes')
    text=one(text,'#define A52_P431_WITNESS_STRIDE      0x0080U\n',
                  '#define A52_P431_WITNESS_STRIDE      0x0050U\n','snapshot witness stride')
    anchor='#define A52_P431_EXEC_BUSY_MS        800ULL\n'
    block='''#define A52_P431_EXEC_BUSY_MS        800ULL

#define A52_P432_FRONTIER_OFF        0x7f00U
#define A52_P432_FRONTIER_BYTES      0x0100U
#define A52_P432_FRONTIER_STRIDE     0x0050U
#define A52_P432_F_SF_SEEN           0x21U
#define A52_P432_F_BOOTANIM_SEEN     0x23U
'''
    text=one(text,anchor,block,'frontier geometry')

    text=one(text,'static struct task_struct *a52_p431_exec_task;\n',
                  'static struct task_struct *a52_p431_exec_task __maybe_unused;\n','exec task maybe unused')
    text=one(text,'static int a52_p431_exec_fn(void *unused)\n',
                  'static __maybe_unused int a52_p431_exec_fn(void *unused)\n','exec fn maybe unused')

    needle='''	raw_spin_unlock_irqrestore(&a52_p431_witness_lock, flags);
}

static __maybe_unused int a52_p431_exec_fn(void *unused)
'''
    insert='''	raw_spin_unlock_irqrestore(&a52_p431_witness_lock, flags);
}

void a52_p432_frontier(u32 stage, pid_t tgid, u32 aux)
{
	struct a52_p431_witness w;
	unsigned long flags;
	void *d0, *d1, *d2;
	u32 seq;

	if (!READ_ONCE(a52_p430_task_sideband))
		return;
	memset(&w, 0, sizeof(w));
	seq = (u32)atomic_inc_return(&a52_p431_witness_seq);
	w.magic = A52_P431_WITNESS_MAGIC;
	w.cntpct = a52_p431_cntpct();
	w.sf_start_ns = READ_ONCE(a52_p430_sf_start_ns);
	w.loops = (u64)(u32)tgid;
	w.write_ns = ktime_get_boottime_ns();
	w.sequence = seq;
	w.sequence_inv = ~seq;
	w.cpu = (u32)raw_smp_processor_id();
	w.stage = stage;
	w.snapshot_id = aux;
	w.cntfrq = a52_p431_cntfrq();
	w.crc32c = a52_r382_crc32c(&w, offsetof(struct a52_p431_witness, crc32c));
	w.commit = A52_P431_WITNESS_COMMIT;
	w.version = A52_P431_WITNESS_VERSION;

	raw_spin_lock_irqsave(&a52_p431_witness_lock, flags);
	d0 = (u8 *)a52_p430_task_sideband + A52_P432_FRONTIER_OFF;
	d1 = (u8 *)d0 + A52_P432_FRONTIER_STRIDE;
	d2 = (u8 *)d1 + A52_P432_FRONTIER_STRIDE;
	memcpy(d0, &w, sizeof(w));
	memcpy(d1, &w, sizeof(w));
	memcpy(d2, &w, sizeof(w));
	wmb();
	__flush_dcache_area(d0, sizeof(w));
	__flush_dcache_area(d1, sizeof(w));
	__flush_dcache_area(d2, sizeof(w));
	dsb(sy);
	raw_spin_unlock_irqrestore(&a52_p431_witness_lock, flags);
}
EXPORT_SYMBOL_GPL(a52_p432_frontier);

static __maybe_unused int a52_p431_exec_fn(void *unused)
'''
    text=one(text,needle,insert,'frontier writer')

    text=one(text,'#define A52_P430_ROLE_SYSTEM        4U\n',
                  '#define A52_P430_ROLE_SYSTEM        4U\n#define A52_P432_ROLE_BOOTANIM       5U\n','bootanimation role')
    text=one(text,'extern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);\n',
                  'extern bool a52_ackfr_phase269_is_composer_tgid(pid_t tgid);\nextern int a52_ackfr_phase432_drm_owner_tgid(void);\n','DRM owner getter')

    text=one(text,
        '''static void a52_p430_fill(struct a52_p430_task_record *r,
			  struct task_struct *p, unsigned int snapshot_id,
			  unsigned int role, unsigned int slot)
''',
        '''static void a52_p430_fill(struct a52_p430_task_record *r,
			  struct task_struct *p, unsigned int snapshot_id,
			  unsigned int role, unsigned int slot, bool heavy)
''',
        'fill signature')
    old='''	get_task_comm(r->comm, p);
	r->wchan = (u64)get_wchan(p);
	if (r->wchan)
		sprint_symbol(r->wchan_symbol, (unsigned long)r->wchan);
	if (try_get_task_stack(p)) {
		r->stack_nr = stack_trace_save_tsk(p, r->stack, A52_P430_STACK, 0);
		if (p->mm) {
			regs = task_pt_regs(p);
			r->user_pc = READ_ONCE(regs->pc);
			r->user_lr = READ_ONCE(regs->regs[30]);
			r->user_sp = READ_ONCE(regs->sp);
		}
		put_task_stack(p);
	}
'''
    new='''	get_task_comm(r->comm, p);
	if (heavy) {
		r->wchan = (u64)get_wchan(p);
		if (r->wchan)
			sprint_symbol(r->wchan_symbol, (unsigned long)r->wchan);
		if (try_get_task_stack(p)) {
			r->stack_nr = stack_trace_save_tsk(p, r->stack, A52_P430_STACK, 0);
			if (p->mm) {
				regs = task_pt_regs(p);
				r->user_pc = READ_ONCE(regs->pc);
				r->user_lr = READ_ONCE(regs->regs[30]);
				r->user_sp = READ_ONCE(regs->sp);
			}
			put_task_stack(p);
		}
	}
'''
    text=one(text,old,new,'light first snapshot')
    text=one(text,'a52_p430_fill(&r, tasks[i], snapshot_id, roles[i], i);',
                  'a52_p430_fill(&r, tasks[i], snapshot_id, roles[i], i, snapshot_id != 0U);',
                  'fill call')

    old='''		else if (role == A52_P430_ROLE_SYSTEM)
			match = a52_p430_comm_is(g, "system_server");
'''
    new='''		else if (role == A52_P430_ROLE_SYSTEM)
			match = a52_p430_comm_is(g, "system_server");
		else if (role == A52_P432_ROLE_BOOTANIM)
			match = a52_p430_comm_is(g, "bootanimation");
'''
    text=one(text,old,new,'bootanimation finder')

    start=text.find('static unsigned int a52_p430_display_tasks(')
    end=text.find('\nstatic void a52_p430_fill(',start)
    if start<0 or end<0: raise SystemExit('Phase432 display_tasks bounds missing')
    repl=r'''static unsigned int a52_p430_display_tasks(struct task_struct **tasks,
		u32 *roles, unsigned int cap, int *sf_pid, int *cp_pid, int *ss_pid,
		int *ba_pid)
{
	struct task_struct *sf, *cp, *ss, *ba;
	unsigned int nr = 0;

	sf = a52_p430_find_leader(A52_P430_ROLE_SF);
	cp = a52_p430_find_leader(A52_P430_ROLE_COMPOSER);
	ba = a52_p430_find_leader(A52_P432_ROLE_BOOTANIM);
	ss = a52_p430_find_leader(A52_P430_ROLE_SYSTEM);
	if (sf) {
		if (sf_pid) *sf_pid = sf->pid;
		a52_p430_add(tasks, roles, &nr, cap, sf, A52_P430_ROLE_SF);
		a52_p430_add_one_thread(sf, A52_P430_ROLE_SF, tasks, roles, &nr, cap);
	}
	if (cp) {
		if (cp_pid) *cp_pid = cp->pid;
		a52_p430_add(tasks, roles, &nr, cap, cp, A52_P430_ROLE_COMPOSER);
		a52_p430_add_one_thread(cp, A52_P430_ROLE_COMPOSER, tasks, roles, &nr, cap);
	}
	if (ba) {
		if (ba_pid) *ba_pid = ba->pid;
		a52_p430_add(tasks, roles, &nr, cap, ba, A52_P432_ROLE_BOOTANIM);
	}
	if (ss) {
		if (ss_pid) *ss_pid = ss->pid;
		a52_p430_add(tasks, roles, &nr, cap, ss, A52_P430_ROLE_SYSTEM);
	}
	if (sf) put_task_struct(sf);
	if (cp) put_task_struct(cp);
	if (ba) put_task_struct(ba);
	if (ss) put_task_struct(ss);
	return nr;
}
'''
    text=text[:start]+repl+text[end:]

    text=one(text,'\tint sf = -1, cp = -1, ss = -1;\n',
                  '\tint sf = -1, cp = -1, ss = -1, ba = -1;\n','snapshot BA local')
    text=one(text,
        '''	nr = a52_p430_display_tasks(tasks, roles, A52_P430_PER_SNAPSHOT,
		&sf, &cp, &ss);
	a52_ackfr_record("P431 SL id=%u nr=%u sf=%d cp=%d ss=%d",
		snapshot_id, nr, sf, cp, ss);
''',
        '''	nr = a52_p430_display_tasks(tasks, roles, A52_P430_PER_SNAPSHOT,
		&sf, &cp, &ss, &ba);
	a52_ackfr_record("P432 SL id=%u nr=%u sf=%d cp=%d ba=%d ss=%d",
		snapshot_id, nr, sf, cp, ba, ss);
''',
        'snapshot discovery log')
    for oldp,newp in (("P431 SB","P432 SB"),("P431 TB","P432 TB"),("P431 TD","P432 TD"),("P431 SD","P432 SD"),("P431 SF0","P432 SF0"),("P431 AT0","P432 AT0")):
        text=text.replace(oldp,newp)

    text=one(text,
        'static const u32 rel_s[5] = { 1U, 3U, 5U, 20U, 60U };\n',
        'static const u32 rel_ms[5] = { 250U, 1000U, 3000U, 5000U, 20000U };\n',
        'early snapshot schedule')
    text=one(text,
        'elapsed = div_u64(ktime_get_boottime_ns() - a52_p430_sf_start_ns,\n\t\t\t\tNSEC_PER_SEC);',
        'elapsed = div_u64(ktime_get_boottime_ns() - a52_p430_sf_start_ns,\n\t\t\t\tNSEC_PER_MSEC);',
        'sampler elapsed milliseconds')
    text=text.replace('ARRAY_SIZE(rel_s) && elapsed >= rel_s[next]',
                      'ARRAY_SIZE(rel_ms) && elapsed >= rel_ms[next]')
    text=text.replace('next >= ARRAY_SIZE(rel_s)', 'next >= ARRAY_SIZE(rel_ms)')

    anchor='''	bool endpoint_done = false;
	bool immediate_done = false;
	struct task_struct *sf;
'''
    repl='''	bool endpoint_done = false;
	bool immediate_done = false;
	pid_t a52_p432_ba_pid = -1;
	struct task_struct *sf;
'''
    text=one(text,anchor,repl,'BA sampler state')
    anchor='''		if (a52_p430_sf_start_ns && !immediate_done) {
'''
    block='''		if (a52_p430_sf_start_ns) {
			struct task_struct *ba = a52_p430_find_leader(A52_P432_ROLE_BOOTANIM);
			pid_t now_ba = ba ? ba->pid : -1;
			if (now_ba != a52_p432_ba_pid) {
				a52_ackfr_record("P432 BA old=%d new=%d", a52_p432_ba_pid, now_ba);
				a52_p432_frontier(A52_P432_F_BOOTANIM_SEEN, now_ba, now_ba > 0);
				a52_p432_ba_pid = now_ba;
			}
			if (ba) put_task_struct(ba);
		}
		if (a52_p430_sf_start_ns && !immediate_done) {
'''
    text=one(text,anchor,block,'BA lifecycle polling')
    text=one(text,
        '''				a52_ackfr_record("P432 SF0 p=%d ns=%llu", sf->pid,
					(unsigned long long)a52_p430_sf_start_ns);
				a52_p431_witness_write(1U, 0U, 0);
''',
        '''				a52_ackfr_record("P432 SF0 p=%d ns=%llu", sf->pid,
					(unsigned long long)a52_p430_sf_start_ns);
				a52_p431_witness_write(1U, 0U, 0);
				a52_p432_frontier(A52_P432_F_SF_SEEN, sf->tgid, sf->pid);
''',
        'SF frontier')

    old='''	a52_p431_exec_task = kthread_run(a52_p431_exec_fn, NULL, "a52_p431_exec");
	if (IS_ERR(a52_p431_exec_task)) {
		a52_ackfr_record("P431 EXEC err=%ld", PTR_ERR(a52_p431_exec_task));
		a52_p431_exec_task = NULL;
	}
'''
    text=one(text,old,'\t/* Phase432: CPU7 busy witness intentionally disabled to reduce perturbation. */\n','disable CPU7 witness')

    text=one(text,
        '''	BUILD_BUG_ON(A52_P431_WITNESS_OFF + A52_P431_WITNESS_BYTES >
		A52_P430_TASK_BYTES);
''',
        '''	BUILD_BUG_ON(A52_P431_WITNESS_OFF + A52_P431_WITNESS_BYTES > A52_P432_FRONTIER_OFF);
	BUILD_BUG_ON(A52_P432_FRONTIER_OFF + A52_P432_FRONTIER_BYTES > A52_P430_TASK_BYTES);
	BUILD_BUG_ON(3U * A52_P432_FRONTIER_STRIDE > A52_P432_FRONTIER_BYTES);
''',
        'frontier geometry guards')

    text += '\n/* '+MARK+': light SF+0; real HWC owner + bootanimation frontier. */\n'
    return text


def validate(root:Path)->None:
    s=(root/SYSCALL).read_text(errors='replace')
    m=(root/MSM).read_text(errors='replace')
    r=(root/REC).read_text(errors='replace')
    for tok in (
        MARK,'A52_P432_FRONTIER_OFF        0x7f00U','A52_P432_ROLE_BOOTANIM       5U',
        'a52_p432_frontier(u32 stage, pid_t tgid, u32 aux)','EXPORT_SYMBOL_GPL(a52_p432_frontier)',
        'P432 SF0 p=%d ns=%llu','P432 SL id=%u nr=%u sf=%d cp=%d ba=%d ss=%d',
        'P432 BA old=%d new=%d','snapshot_id != 0U','rel_ms[5] = { 250U, 1000U, 3000U, 5000U, 20000U }','CPU7 busy witness intentionally disabled'):
        if tok not in s: raise SystemExit('Phase432 syscall token missing: '+tok)
    if 'kthread_run(a52_p431_exec_fn' in s:
        raise SystemExit('Phase432 CPU7 busy witness still starts')
    for tok in (
        MARK,'P432 OWN n=%u p=%d t=%d c=%.15s idx=%d','P432 IO n=%u p=%d t=%d nr=0x%x',
        'P432 AT n=%u t=%d test=%u fl=%x objs=%u','DRM_MINOR_PRIMARY','a52_ackfr_phase432_note_drm_owner'):
        if tok not in m: raise SystemExit('Phase432 MSM token missing: '+tok)
    for tok in (
        MARK,'a52_p432_drm_owner_tgid','a52_ackfr_phase432_note_drm_owner',
        'a52_ackfr_phase432_drm_owner_tgid','!strncmp(message, "P432 ", 5)',
        'strncmp(fmt, "P432", 4)'):
        if tok not in r: raise SystemExit('Phase432 recorder token missing: '+tok)
    u=(root/Path('drivers/scsi/ufs/ufshcd.c')).read_text(errors='replace')
    d=(root/Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')).read_text(errors='replace')
    if 'a52_p430_ufs_compact(snapshot_id);' in s:
        raise SystemExit('Phase432 UFS sampler call reappeared')
    if 'Phase430: retain HBA identity even though Phase378 sampler stays retired.' in u:
        raise SystemExit('Phase432 unsafe Phase430 HBA registration reappeared')
    if 'P430 PANIC_ARMED ctrl=%d irq=%u' not in d:
        raise SystemExit('Phase432 exact-F0 panic path missing')


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--root',type=Path,required=True); ap.add_argument('--check-only',action='store_true'); ns=ap.parse_args()
    for p in (SYSCALL,MSM,REC,Path('drivers/scsi/ufs/ufshcd.c'),Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')):
        if not (ns.root/p).is_file(): raise SystemExit('Phase432 source missing: '+str(p))
    if not ns.check_only:
        for p,fn in ((REC,patch_rec),(MSM,patch_msm),(SYSCALL,patch_syscall)):
            q=ns.root/p; q.write_text(fn(q.read_text(errors='replace')))
    validate(ns.root)
    print('Phase432 SF/HWC/bootanimation chain forensics: PASS')
    return 0

if __name__=='__main__': raise SystemExit(main())
