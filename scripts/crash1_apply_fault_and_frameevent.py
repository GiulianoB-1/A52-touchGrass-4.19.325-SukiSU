#!/usr/bin/env python3
"""CRASH1 passive frame-event and fault recorder on the GKI no-UDC-handover image.

Run AFTER existing U2AB/Phase446/F0B observers have patched the effective
kernel. Crucially, F0B_ACTIVE_STAGE becomes zero (no synthetic DSI/F0).
Preserve COM3; no regulator, clock, DMA, reset or panel behavior changes.

Stores bounded 16-replica frame-event breadcrumbs, kernel fault registers,
and on OOPS/PANIC a 4-KiB last-kmsg window x16 to the existing P445 reserved
FCOMP RAM. Does not create overlapping pstore/ramoops regions.
"""
import argparse
import re
from pathlib import Path

MARK = "A52_CRASH1_FRAME_EVENT_PERSIST_V1"
P445 = "drivers/a52_display/msm/a52_phase445.c"
CRTC = "drivers/a52_display/msm/sde/sde_crtc.c"
DISPLAY = "drivers/a52_display/msm/dsi/dsi_display.c"
FAULT = "arch/arm64/mm/fault.c"

HELPER = r'''
/* A52_CRASH1_FRAME_EVENT_PERSIST_V1:
 * Fail-stop diagnostics only. No raw F0, no panel register writes.
 * All data stored with P445 section CRC and 16 redundant copies.
 */
#include <linux/kmsg_dump.h>
#include <linux/atomic.h>
#include <linux/ktime.h>
#include <linux/sched.h>
#include <asm/ptrace.h>

#define CRASH1_REPLICAS 16U
#define CRASH1_MAX_EVENTS 24
#define CRASH1_KMSG_BYTES 4096U
struct crash1_fevent {
    u32 magic, version, seq, tag, event, crtc_id, pid, reserved;
    u64 ns, cbdata, crtc, fevent;
    u64 ns_inv;
};
struct crash1_fault {
    u32 magic, version, pid, esr;
    u64 far, pc, lr, sp, pstate, ns;
    u64 far_inv, pc_inv, lr_inv;
    char comm[TASK_COMM_LEN];
};
static atomic_t crash1_event_count = ATOMIC_INIT(0);
static atomic_t crash1_fault_once = ATOMIC_INIT(0);
static atomic_t crash1_dump_once = ATOMIC_INIT(0);
static struct crash1_fault crash1_fault_copies[CRASH1_REPLICAS];
static char crash1_kmsg[CRASH1_KMSG_BYTES];
static char crash1_kmsg_copies[CRASH1_REPLICAS][CRASH1_KMSG_BYTES];
static struct kmsg_dumper crash1_dumper;
static int crash1_dumper_registered;

void a52_crash1_event(u32 tag, u32 event, const void *cbdata,
                      const void *crtc, const void *fevent, u32 crtc_id)
{
    struct crash1_fevent r = {0};
    struct crash1_fevent copies[CRASH1_REPLICAS];
    int seq, i;

    /* Remain passive; record the first several event transitions only. */
    seq = atomic_inc_return(&crash1_event_count);
    if (seq > CRASH1_MAX_EVENTS || !p445_image) return;
    r.magic = 0x31564546U;  /* FEV1 */
    r.version = 1U;
    r.seq = seq;
    r.tag = tag;
    r.event = event;
    r.crtc_id = crtc_id;
    r.pid = task_pid_nr(current);
    r.ns = ktime_get_ns();
    r.cbdata = (u64)(uintptr_t)cbdata;
    r.crtc = (u64)(uintptr_t)crtc;
    r.fevent = (u64)(uintptr_t)fevent;
    r.ns_inv = ~r.ns;
    for (i = 0; i < CRASH1_REPLICAS; i++) copies[i] = r;
    (void)a52_p445_store_section(0xc1a50001U, 0xc1a5U,
                  "CR1_FRAME_EVENT", (u32)seq, tag, copies, sizeof(copies));
}
EXPORT_SYMBOL_GPL(a52_crash1_event);

/* Called before the ordinary do_mem_abort() diagnostic logging. */
void a52_crash1_fault(unsigned long far, unsigned int esr,
                      const struct pt_regs *regs)
{
    struct crash1_fault r = {0};
    int i;

    if (!regs || user_mode(regs) || !p445_image ||
        atomic_cmpxchg(&crash1_fault_once, 0, 1) != 0) return;
    r.magic = 0x31544c46U; /* FLT1 */
    r.version = 1U;
    r.pid = task_pid_nr(current);
    r.esr = esr;
    r.far = far;
    r.pc = instruction_pointer(regs);
    r.lr = regs->regs[30];
    r.sp = regs->sp;
    r.pstate = regs->pstate;
    r.ns = ktime_get_ns();
    r.far_inv = ~r.far;
    r.pc_inv = ~r.pc;
    r.lr_inv = ~r.lr;
    memcpy(r.comm, current->comm, TASK_COMM_LEN);
    for (i = 0; i < CRASH1_REPLICAS; i++) crash1_fault_copies[i] = r;
    (void)a52_p445_store_section(0xc1a50002U, 0xc1a5U,
                  "CR1_ARM64_FAULT", r.esr, r.pid,
                  crash1_fault_copies, sizeof(crash1_fault_copies));
    pr_emerg("CRASH1 FAULT PID=%u FAR=%016llx PC=%016llx LR=%016llx ESR=%08x\n",
             r.pid, r.far, r.pc, r.lr, r.esr);
}
EXPORT_SYMBOL_GPL(a52_crash1_fault);

static void crash1_dump(struct kmsg_dumper *dumper,
                        enum kmsg_dump_reason reason)
{
    size_t len = 0;
    int i;

    if (reason != KMSG_DUMP_OOPS && reason != KMSG_DUMP_PANIC)
        return;
    if (!p445_image ||
        atomic_cmpxchg(&crash1_dump_once, 0, 1) != 0)
        return;
    memset(crash1_kmsg, 0, sizeof(crash1_kmsg));
    kmsg_dump_rewind(dumper);
    if (!kmsg_dump_get_buffer(dumper, false, crash1_kmsg,
                              sizeof(crash1_kmsg), &len) || !len)
        return;
    for (i = 0; i < CRASH1_REPLICAS; i++)
        memcpy(crash1_kmsg_copies[i], crash1_kmsg, sizeof(crash1_kmsg));
    (void)a52_p445_store_section(0xc1a50003U, 0xc1a5U,
                  "CR1_OOPS_KMSG", (u32)reason, (u32)len,
                  crash1_kmsg_copies, sizeof(crash1_kmsg_copies));
}
'''

def exactly(s, old, new, label):
    n = s.count(old)
    if n != 1:
        raise RuntimeError("CRASH1 %s: anchor count %d (wanted 1)" % (label, n))
    return s.replace(old, new, 1)

def modify_p445(s):
    if MARK in s:
        return s
    s = exactly(s, "static int __init p445_init(void)\n",
                HELPER + "\nstatic int __init p445_init(void)\n", "p445 helper")
    # Register only after the P445 reserved-RAM copy is initialized.
    s = exactly(s, "    p445_sync_header();\n    mod_delayed_work(system_unbound_wq,&p445_recompile_scan_work,",
         """    p445_sync_header();
    crash1_dumper.dump = crash1_dump;
    crash1_dumper_registered = !kmsg_dump_register(&crash1_dumper);
    pr_info("A52CRASH1 dumper_registered=%d P445_RAM=%d\\n",
            crash1_dumper_registered, !!p445_ram);
    mod_delayed_work(system_unbound_wq,&p445_recompile_scan_work,""",
         "dumper init")
    return s

def modify_crtc(s):
    if "A52_CRASH1_CB_HOOK_V1" in s:
        return s
    anchor = "static void sde_crtc_frame_event_cb(void *data, u32 event)\n"
    s = exactly(s, anchor,
         "/* A52_CRASH1_CB_HOOK_V1 */\n"
         "extern void a52_crash1_event(u32, u32, const void *, const void *,\n"
         "                             const void *, u32);\n" + anchor, "callback")
    s = exactly(s, "\tcrtc_id = drm_crtc_index(crtc);\n",
         "\tcrtc_id = drm_crtc_index(crtc);\n"
         "\ta52_crash1_event(1U, event, cb_data, crtc, NULL, crtc_id);\n",
         "callback valid crtc")
    s = exactly(s, '\t/* log and clear plane ubwc errors if any */',
         '\ta52_crash1_event(2U, event, cb_data, crtc, fevent, crtc_id);\n'
         '\t/* log and clear plane ubwc errors if any */', "popped event")
    s = exactly(s,
         "\tfevent->ts = ktime_get();\n\tkthread_queue_work(",
         "\tfevent->ts = ktime_get();\n"
         "\ta52_crash1_event(3U, event, cb_data, crtc, fevent, crtc_id);\n"
         "\tkthread_queue_work(", "timestamp write")
    return s

def modify_fault(s):
    if "A52_CRASH1_FAULT_HOOK_V1" in s:
        return s
    expression = r"(?m)^(?:asmlinkage\s+)?void\s+do_mem_abort\s*\(\s*unsigned long\s+far\s*,\s*unsigned int\s+esr\s*,\s*struct\s+pt_regs\s*\*\s*regs\s*\)\s*\{"
    hits = list(re.finditer(expression, s))
    if len(hits) != 1:
        raise RuntimeError("CRASH1 ARM64 do_mem_abort signature: %d matches" % len(hits))
    start = hits[0].end()
    hook = """
    /* A52_CRASH1_FAULT_HOOK_V1: persist FAR/PC/LR before printk may freeze */
    if (!user_mode(regs))
        a52_crash1_fault(far, esr, regs);
"""
    s = s[:start] + hook + s[start:]
    first_include = s.find("#include ")
    if first_include < 0:
        raise RuntimeError("CRASH1 cannot place fault declaration")
    s = s[:first_include] + ("/* A52_CRASH1_ARM64_DECL */\n"
         "extern void a52_crash1_fault(unsigned long, unsigned int,\n"
         "                             const struct pt_regs *);\n") + s[first_include:]
    return s

def check(root):
    s=(root/P445).read_text()
    c=(root/CRTC).read_text()
    f=(root/FAULT).read_text()
    d=(root/DISPLAY).read_text()
    for desc,ok in (
        ("P445 16x crash payload",MARK in s and
         "CR1_ARM64_FAULT" in s and "CR1_OOPS_KMSG" in s and
         "CRASH1_REPLICAS 16U" in s),
        ("dumper registration", "kmsg_dump_register(&crash1_dumper)" in s),
        ("frame event 3 boundaries", all(
           f"a52_crash1_event({i}U" in c for i in (1,2,3))),
        ("fault PC before oops", "A52_CRASH1_FAULT_HOOK_V1" in f),
        ("NO experimental raw F0", "#define F0B_ACTIVE_STAGE 0" in d
          and "#define F0B_ACTIVE_STAGE 6" not in d),
    ):
        if not ok:
            raise RuntimeError("CRASH1 audit failed: "+desc)
        print("CRASH1 audit PASS:",desc)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",required=True,type=Path)
    p.add_argument("--check-only",action="store_true")
    a=p.parse_args()
    root=a.root
    if not a.check_only:
        files=((P445,modify_p445),(CRTC,modify_crtc),(FAULT,modify_fault))
        for path,fn in files:
            target=root/path
            target.write_text(fn(target.read_text()))
        target=root/DISPLAY
        d=target.read_text()
        d=exactly(d,"#define F0B_ACTIVE_STAGE 6",
                  "#define F0B_ACTIVE_STAGE 0", "disable raw F0")
        target.write_text(d)
    check(root)
    print("CRASH1: no raw F0; normal Samsung F0 unchanged; panic=0 unchanged.")

if __name__=="__main__":
    main()
