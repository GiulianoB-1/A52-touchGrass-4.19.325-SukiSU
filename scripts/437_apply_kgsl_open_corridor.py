#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

MARK='A52_PHASE437_KGSL_OPEN_CORRIDOR_V1'
REC=Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MAKE=Path('drivers/a52_secure/Makefile')
HELPER=Path('drivers/a52_secure/a52_phase437_open.c')
HDR=Path('include/linux/a52_phase437_open.h')
OPEN=Path('fs/open.c')
NAMEI=Path('fs/namei.c')
CHAR=Path('fs/char_dev.c')


def die(msg): raise SystemExit('Phase437: '+msg)

def one(s,old,new,label):
    n=s.count(old)
    if n!=1: die(f'{label}: expected 1 anchor, found {n}')
    return s.replace(old,new,1)

def add_inc(s):
    if '#include <linux/a52_phase437_open.h>' in s: return s
    a='#include <linux/a52_phase435_race.h>\n'
    return one(s,a,a+'#include <linux/a52_phase436_frontier.h>\n#include <linux/a52_phase437_open.h>\n','include')

HDR_C=r'''/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_A52_PHASE437_OPEN_H
#define _LINUX_A52_PHASE437_OPEN_H
#include <linux/types.h>
struct pt_regs;
enum a52_p437_stage {
 P437_META=0,
 P437_O0_TARGET=1,
 P437_O1_ACK_PRE=2,
 P437_O2_ACK_POST=3,
 P437_O3_GETFD_PRE=4,
 P437_O4_GETFD_POST=5,
 P437_O5_FILP_PRE=6,
 P437_O6_FILP_POST=7,
 P437_N0_DO_FILP_ENTRY=8,
 P437_N1_PATH_OPENAT_ENTRY=9,
 P437_N2_ALLOC_POST=10,
 P437_N3_DO_OPEN_PRE=11,
 P437_N4_DO_OPEN_ENTRY=12,
 P437_N5_COMPLETE_WALK_POST=13,
 P437_N6_MAY_OPEN_POST=14,
 P437_N7_VFS_PRE=15,
 P437_V0_VFS_OPEN_ENTRY=16,
 P437_D0_DENTRY_ENTRY=17,
 P437_D1_FOPS_PRE=18,
 P437_D2_FOPS_POST=19,
 P437_D3_SECURITY_PRE=20,
 P437_D4_SECURITY_POST=21,
 P437_D5_DRIVER_PRE=22,
 P437_C0_CHRDEV_ENTRY=23,
 P437_C1_KOBJ_PRE=24,
 P437_C2_KOBJ_POST=25,
 P437_C3_FOPS_PRE=26,
 P437_C4_FOPS_POST=27,
 P437_C5_DRIVER_PRE=28,
 P437_C6_DRIVER_POST=29,
 P437_D6_DRIVER_POST=30,
 P437_O7_OPEN_END=31,
};
void a52_p437_mark(u32 stage,u64 arg0,u64 arg1,const struct pt_regs *regs);
void a52_p437_arm_target(void);
bool a52_p437_target_current(void);
u64 a52_p437_boot_id(void);
#endif
'''

HELPER_C=r'''// SPDX-License-Identifier: GPL-2.0-only
/* A52_PHASE437_KGSL_OPEN_CORRIDOR_V1
 * Independent fixed-slot recorder around the first SurfaceFlinger
 * /dev/kgsl-3d0 open. No polling, no remote-task inspection, no DSI/MMIO.
 */
#include <linux/a52_phase437_open.h>
#include <linux/atomic.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/ktime.h>
#include <linux/sched.h>
#include <linux/string.h>
#include <asm/cacheflush.h>
#include <asm/ptrace.h>

#define A52_P437_PHYS       0xB1AFA000ULL
#define A52_P437_BYTES      0x2000U
#define A52_P437_COPY_BYTES 0x1000U
#define A52_P437_SLOT_BYTES 128U
#define A52_P437_SLOTS      32U
#define A52_P437_MAGIC      0x3733344543415254ULL /* TRACE437 */
#define A52_P437_COMMIT     0x437c0de5U
#define A52_P437_VERSION    1U

const char a52_p437_build_tag[] = "A52_PHASE437_KGSL_OPEN_CORRIDOR_V1";

struct a52_p437_record {
 u64 magic,boot_id,boottime_ns,arch_counter,arg0,arg1,pc,lr,sp,pstate;
 u32 version,stage,cpu,pid,tgid,slot;
 char comm[TASK_COMM_LEN];
 u32 crc32c,commit;
} __packed;
static void *a52_p437_base;
static atomic_t a52_p437_written[A52_P437_SLOTS];
static atomic_t a52_p437_target_pid=ATOMIC_INIT(-1);
static atomic_t a52_p437_target_tgid=ATOMIC_INIT(-1);
static u64 a52_p437_id;
static __always_inline u64 p437_cnt(void){u64 v;asm volatile("isb; mrs %0, cntpct_el0":"=r"(v));return v;}
static __always_inline u64 p437_frq(void){u64 v;asm volatile("mrs %0, cntfrq_el0":"=r"(v));return v;}
static u32 p437_crc(const void *buffer,size_t len){const u8 *b=buffer;u32 c=~0U;size_t i;unsigned int bit;for(i=0;i<len;i++){c^=b[i];for(bit=0;bit<8;bit++)c=(c>>1)^((c&1U)?0x82f63b78U:0U);}return ~c;}
static void p437_copy(void *dst,const struct a52_p437_record *r){struct a52_p437_record *o=dst;memcpy(o,r,offsetof(struct a52_p437_record,commit));wmb();WRITE_ONCE(o->commit,A52_P437_COMMIT);wmb();__flush_dcache_area(o,sizeof(*o));dsb(sy);}
void a52_p437_mark(u32 stage,u64 arg0,u64 arg1,const struct pt_regs *regs){struct a52_p437_record r;void *d0,*d1;if(!READ_ONCE(a52_p437_base)||stage>=A52_P437_SLOTS)return;if(atomic_cmpxchg(&a52_p437_written[stage],0,1))return;memset(&r,0,sizeof(r));r.magic=A52_P437_MAGIC;r.boot_id=READ_ONCE(a52_p437_id);r.boottime_ns=ktime_get_boottime_ns();r.arch_counter=p437_cnt();r.arg0=arg0;r.arg1=arg1;if(regs){r.pc=regs->pc;r.lr=regs->regs[30];r.sp=regs->sp;r.pstate=regs->pstate;}r.version=A52_P437_VERSION;r.stage=stage;r.cpu=raw_smp_processor_id();r.pid=current->pid;r.tgid=current->tgid;r.slot=stage;memcpy(r.comm,current->comm,TASK_COMM_LEN);r.crc32c=p437_crc(&r,offsetof(struct a52_p437_record,crc32c));d0=(u8*)a52_p437_base+stage*A52_P437_SLOT_BYTES;d1=(u8*)a52_p437_base+A52_P437_COPY_BYTES+stage*A52_P437_SLOT_BYTES;p437_copy(d0,&r);p437_copy(d1,&r);}
EXPORT_SYMBOL_GPL(a52_p437_mark);
void a52_p437_arm_target(void){if(!current->group_leader||strcmp(current->group_leader->comm,"surfaceflinger"))return;atomic_cmpxchg(&a52_p437_target_pid,-1,current->pid);atomic_cmpxchg(&a52_p437_target_tgid,-1,current->tgid);}
EXPORT_SYMBOL_GPL(a52_p437_arm_target);
bool a52_p437_target_current(void){return atomic_read(&a52_p437_target_pid)==current->pid&&atomic_read(&a52_p437_target_tgid)==current->tgid;}
EXPORT_SYMBOL_GPL(a52_p437_target_current);
u64 a52_p437_boot_id(void){return READ_ONCE(a52_p437_id);} EXPORT_SYMBOL_GPL(a52_p437_boot_id);
static int __init a52_p437_init(void){u64 c,f;unsigned int i;BUILD_BUG_ON(sizeof(struct a52_p437_record)!=A52_P437_SLOT_BYTES);BUILD_BUG_ON(A52_P437_SLOTS*A52_P437_SLOT_BYTES!=A52_P437_COPY_BYTES);a52_p437_base=memremap(A52_P437_PHYS,A52_P437_BYTES,MEMREMAP_WB);if(!a52_p437_base)return 0;memset(a52_p437_base,0,A52_P437_BYTES);for(i=0;i<A52_P437_SLOTS;i++)atomic_set(&a52_p437_written[i],0);wmb();__flush_dcache_area(a52_p437_base,A52_P437_BYTES);dsb(sy);c=p437_cnt();f=p437_frq();a52_p437_id=c^(ktime_get_boottime_ns()<<1)^(u64)(unsigned long)a52_p437_init;if(!a52_p437_id)a52_p437_id=1;a52_p437_mark(P437_META,f,A52_P437_PHYS,NULL);return 0;}
core_initcall(a52_p437_init);
'''

def patch_rec(s):
    if MARK in s:return s
    s=one(s,'#define A52_P414_RAM_BYTES           (SZ_1M - SZ_16K)\n','#define A52_P414_RAM_BYTES           (SZ_1M - SZ_16K - SZ_8K)\n','P414 carve')
    return s+'\n/* '+MARK+': final 24 KiB reserved from P414: P437 8 KiB + P436 16 KiB. */\n'

def patch_open(s):
    if MARK in s:return s
    s=add_inc(s)
    s=one(s,'\tbool a52_p435_target = false;\n','\tbool a52_p435_target = false;\n\tbool a52_p437_target = false;\n','open target local')
    old='''\tif (!strcmp(current->comm, "surfaceflinger") &&
\t    !strcmp(tmp->name, "/dev/kgsl-3d0")) {
\t\ta52_p435_target = true;
\t\ta52_p435_arm_target((u64)(unsigned long)tmp->name, 0);
\t}
'''
    new='''\tif (current->group_leader &&
\t    !strcmp(current->group_leader->comm, "surfaceflinger") &&
\t    !strcmp(tmp->name, "/dev/kgsl-3d0")) {
\t\ta52_p435_target = true;
\t\ta52_p437_target = true;
\t\ta52_p435_arm_target((u64)(unsigned long)tmp->name, 0);
\t\ta52_p436_arm_target();
\t\ta52_p437_arm_target();
\t\ta52_p437_mark(P437_O0_TARGET, how->flags,
\t\t\t(u64)(unsigned long)tmp->name, NULL);
\t}
'''
    s=one(s,old,new,'group-leader target')
    old='''\t\tif (trace)
\t\t\t/* A52_PHASE268_DRM_TGID_TRACE_V1: diagnostic identity only. */
\t\t\ta52_ackfr_record("DRMPOST 212 path n=%u p=%d c=%.16s %.32s g=%d",
\t\t\t\t\t  trace_id, current->pid, current->comm, tmp->name, current->tgid);
'''
    new='''\t\tif (trace) {
\t\t\t/* A52_PHASE268_DRM_TGID_TRACE_V1: diagnostic identity only. */
\t\t\tif (a52_p437_target)
\t\t\t\ta52_p437_mark(P437_O1_ACK_PRE, trace_id, 0, NULL);
\t\t\ta52_ackfr_record("DRMPOST 212 path n=%u p=%d c=%.16s %.32s g=%d",
\t\t\t\t\t  trace_id, current->pid, current->comm, tmp->name, current->tgid);
\t\t\tif (a52_p437_target)
\t\t\t\ta52_p437_mark(P437_O2_ACK_POST, trace_id, 0, NULL);
\t\t}
'''
    s=one(s,old,new,'ack bracket')
    old='''\tif (a52_p435_target)
\t\ta52_p435_mark(P435_K1_BEFORE_GET_FD, how->flags, 0, NULL);
\tfd = get_unused_fd_flags(how->flags);
\tif (a52_p435_target)
\t\ta52_p435_mark(P435_K2_AFTER_GET_FD, (u64)(s64)fd, 0, NULL);
'''
    new='''\tif (a52_p435_target)
\t\ta52_p435_mark(P435_K1_BEFORE_GET_FD, how->flags, 0, NULL);
\tif (a52_p437_target)
\t\ta52_p437_mark(P437_O3_GETFD_PRE, how->flags, 0, NULL);
\tfd = get_unused_fd_flags(how->flags);
\tif (a52_p437_target)
\t\ta52_p437_mark(P437_O4_GETFD_POST, (u64)(s64)fd, 0, NULL);
\tif (a52_p435_target)
\t\ta52_p435_mark(P435_K2_AFTER_GET_FD, (u64)(s64)fd, 0, NULL);
'''
    s=one(s,old,new,'getfd bracket')
    old='''\t\tif (a52_p435_target)
\t\t\ta52_p435_mark(P435_K3_BEFORE_FILP_OPEN, (u64)(s64)fd, dfd, NULL);
\t\tf = do_filp_open(dfd, tmp, &op);
'''
    new='''\t\tif (a52_p435_target)
\t\t\ta52_p435_mark(P435_K3_BEFORE_FILP_OPEN, (u64)(s64)fd, dfd, NULL);
\t\tif (a52_p437_target)
\t\t\ta52_p437_mark(P437_O5_FILP_PRE, (u64)(s64)fd, dfd, NULL);
\t\tf = do_filp_open(dfd, tmp, &op);
\t\tif (a52_p437_target)
\t\t\ta52_p437_mark(P437_O6_FILP_POST,
\t\t\t\tIS_ERR(f) ? (u64)(s64)PTR_ERR(f) : 0,
\t\t\t\t(u64)(unsigned long)f, NULL);
'''
    s=one(s,old,new,'filp bracket')
    s=one(s,'\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 path-ret n=%u fd=%d", trace_id, fd);\n','\tif (a52_p437_target)\n\t\ta52_p437_mark(P437_O7_OPEN_END, (u64)(s64)fd, 0, NULL);\n\tif (trace)\n\t\ta52_ackfr_record("DRMPOST 212 path-ret n=%u fd=%d", trace_id, fd);\n','open end')
    return s+'\n/* '+MARK+': group-leader target + recorder/getfd/filp brackets. */\n'

def patch_namei(s):
    if MARK in s:return s
    s=add_inc(s)
    s=one(s,'\tint error;\n\n\tif (a52_p435_target_current())\n\t\ta52_p435_mark(P435_K4_PATH_OPENAT, flags, op->open_flag, NULL);\n','\tint error;\n\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_N1_PATH_OPENAT_ENTRY, flags, op->open_flag, NULL);\n\tif (a52_p435_target_current())\n\t\ta52_p435_mark(P435_K4_PATH_OPENAT, flags, op->open_flag, NULL);\n','path_openat entry')
    s=one(s,'\tfile = alloc_empty_file(op->open_flag, current_cred());\n\tif (IS_ERR(file))\n','\tfile = alloc_empty_file(op->open_flag, current_cred());\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_N2_ALLOC_POST,\n\t\t\tIS_ERR(file) ? (u64)(s64)PTR_ERR(file) : 0,\n\t\t\t(u64)(unsigned long)file, NULL);\n\tif (IS_ERR(file))\n','alloc post')
    s=one(s,'\t\tif (!error)\n\t\t\terror = do_open(nd, file, op);\n','\t\tif (!error) {\n\t\t\tif (a52_p437_target_current())\n\t\t\t\ta52_p437_mark(P437_N3_DO_OPEN_PRE, flags, file->f_mode, NULL);\n\t\t\terror = do_open(nd, file, op);\n\t\t}\n','do_open pre')
    s=one(s,'\tint error;\n\n\tif (a52_p435_target_current())\n\t\ta52_p435_mark(P435_K5_DO_OPEN, op->open_flag, file->f_mode, NULL);\n','\tint error;\n\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_N4_DO_OPEN_ENTRY, op->open_flag, file->f_mode, NULL);\n\tif (a52_p435_target_current())\n\t\ta52_p435_mark(P435_K5_DO_OPEN, op->open_flag, file->f_mode, NULL);\n','do_open entry')
    s=one(s,'\t\terror = complete_walk(nd);\n\t\tif (error)\n','\t\terror = complete_walk(nd);\n\t\tif (a52_p437_target_current())\n\t\t\ta52_p437_mark(P437_N5_COMPLETE_WALK_POST, (u64)(s64)error, 0, NULL);\n\t\tif (error)\n','complete walk')
    s=one(s,'\terror = may_open(&nd->path, acc_mode, open_flag);\n\tif (!error && !(file->f_mode & FMODE_OPENED))\n\t\terror = vfs_open(&nd->path, file);\n','\terror = may_open(&nd->path, acc_mode, open_flag);\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_N6_MAY_OPEN_POST, (u64)(s64)error,\n\t\t\t(u64)(unsigned long)nd->path.dentry, NULL);\n\tif (!error && !(file->f_mode & FMODE_OPENED)) {\n\t\tif (a52_p437_target_current())\n\t\t\ta52_p437_mark(P437_N7_VFS_PRE, file->f_mode, 0, NULL);\n\t\terror = vfs_open(&nd->path, file);\n\t}\n','may/vfs')
    s=one(s,'\tstruct file *filp;\n\n\tset_nameidata(&nd, dfd, pathname);\n','\tstruct file *filp;\n\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_N0_DO_FILP_ENTRY, op->lookup_flags, dfd, NULL);\n\tset_nameidata(&nd, dfd, pathname);\n','do_filp entry')
    return s+'\n/* '+MARK+': namei open corridor. */\n'

def patch_fopen(s):
    if 'P437_D0_DENTRY_ENTRY' in s:return s
    s=add_inc(s)
    s=one(s,'\tstatic const struct file_operations empty_fops = {};\n\tint error;\n\n\tif (a52_p435_target_current())\n','\tstatic const struct file_operations empty_fops = {};\n\tint error;\n\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_D0_DENTRY_ENTRY,\n\t\t\t(u64)(unsigned long)inode, (u64)inode->i_rdev, NULL);\n\tif (a52_p435_target_current())\n','dentry entry')
    s=one(s,'\tf->f_op = fops_get(inode->i_fop);\n\tif (a52_p435_target_current())\n','\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_D1_FOPS_PRE,\n\t\t\t(u64)(unsigned long)inode->i_fop, (u64)inode->i_rdev, NULL);\n\tf->f_op = fops_get(inode->i_fop);\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_D2_FOPS_POST,\n\t\t\t(u64)(unsigned long)f->f_op, (u64)inode->i_rdev, NULL);\n\tif (a52_p435_target_current())\n','dentry fops')
    s=one(s,'\tif (a52_p435_target_current())\n\t\ta52_p435_mark(P435_K9_SECURITY_PRE, (u64)(unsigned long)f->f_op, 0, NULL);\n\terror = security_file_open(f);\n\tif (a52_p435_target_current())\n\t\ta52_p435_mark(P435_K10_SECURITY_POST, (u64)(s64)error, 0, NULL);\n','\tif (a52_p435_target_current())\n\t\ta52_p435_mark(P435_K9_SECURITY_PRE, (u64)(unsigned long)f->f_op, 0, NULL);\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_D3_SECURITY_PRE, (u64)(unsigned long)f->f_op, 0, NULL);\n\terror = security_file_open(f);\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_D4_SECURITY_POST, (u64)(s64)error, 0, NULL);\n\tif (a52_p435_target_current())\n\t\ta52_p435_mark(P435_K10_SECURITY_POST, (u64)(s64)error, 0, NULL);\n','security bracket')
    s=one(s,'\t\tif (a52_p435_target_current())\n\t\t\ta52_p435_mark(P435_K11_DRIVER_OPEN_PRE, (u64)(unsigned long)open, (u64)inode->i_rdev, NULL);\n\t\terror = open(inode, f);\n\t\tif (error)\n','\t\tif (a52_p435_target_current())\n\t\t\ta52_p435_mark(P435_K11_DRIVER_OPEN_PRE, (u64)(unsigned long)open, (u64)inode->i_rdev, NULL);\n\t\tif (a52_p437_target_current())\n\t\t\ta52_p437_mark(P437_D5_DRIVER_PRE,\n\t\t\t\t(u64)(unsigned long)open, (u64)inode->i_rdev, NULL);\n\t\terror = open(inode, f);\n\t\tif (a52_p437_target_current())\n\t\t\ta52_p437_mark(P437_D6_DRIVER_POST,\n\t\t\t\t(u64)(s64)error, (u64)(unsigned long)open, NULL);\n\t\tif (error)\n','dentry driver')
    s=one(s,'int vfs_open(const struct path *path, struct file *file)\n{\n\tif (a52_p435_target_current())\n','int vfs_open(const struct path *path, struct file *file)\n{\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_V0_VFS_OPEN_ENTRY,\n\t\t\t(u64)(unsigned long)path->dentry, file->f_mode, NULL);\n\tif (a52_p435_target_current())\n','vfs entry')
    return s+'\n/* '+MARK+': VFS/dentry-open corridor. */\n'

def patch_char(s):
    if MARK in s:return s
    s=add_inc(s)
    s=one(s,'\tbool a52t = a52_r261_chr(&a52n);\n\tif (a52t) a52_ackfr_record','\tbool a52t = a52_r261_chr(&a52n);\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_C0_CHRDEV_ENTRY, (u64)inode->i_rdev,\n\t\t\t(u64)(unsigned long)inode->i_cdev, NULL);\n\tif (a52t) a52_ackfr_record','chr entry')
    s=one(s,'\t\tspin_unlock(&cdev_lock);\n\t\tkobj = kobj_lookup(cdev_map, inode->i_rdev, &idx);\n\t\tif (a52t) a52_ackfr_record','\t\tspin_unlock(&cdev_lock);\n\t\tif (a52_p437_target_current())\n\t\t\ta52_p437_mark(P437_C1_KOBJ_PRE, (u64)inode->i_rdev, idx, NULL);\n\t\tkobj = kobj_lookup(cdev_map, inode->i_rdev, &idx);\n\t\tif (a52_p437_target_current())\n\t\t\ta52_p437_mark(P437_C2_KOBJ_POST,\n\t\t\t\t(u64)(unsigned long)kobj, idx, NULL);\n\t\tif (a52t) a52_ackfr_record','kobj bracket')
    s=one(s,'\tret = -ENXIO;\n\tfops = fops_get(p->ops);\n\tif (a52t) a52_ackfr_record','\tret = -ENXIO;\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_C3_FOPS_PRE,\n\t\t\t(u64)(unsigned long)(p ? p->ops : NULL), 0, NULL);\n\tfops = fops_get(p->ops);\n\tif (a52_p437_target_current())\n\t\ta52_p437_mark(P437_C4_FOPS_POST,\n\t\t\t(u64)(unsigned long)fops, (u64)(unsigned long)p, NULL);\n\tif (a52t) a52_ackfr_record','char fops')
    s=one(s,'\t\tif (a52t) a52_p435_mark(P435_C3_DRIVER_PRE, (u64)(unsigned long)filp->f_op->open, 0, NULL);\n\t\tret = filp->f_op->open(inode, filp);\n\t\tif (a52t) a52_ackfr_record','\t\tif (a52t) a52_p435_mark(P435_C3_DRIVER_PRE, (u64)(unsigned long)filp->f_op->open, 0, NULL);\n\t\tif (a52_p437_target_current())\n\t\t\ta52_p437_mark(P437_C5_DRIVER_PRE,\n\t\t\t\t(u64)(unsigned long)filp->f_op->open, (u64)inode->i_rdev, NULL);\n\t\tret = filp->f_op->open(inode, filp);\n\t\tif (a52_p437_target_current())\n\t\t\ta52_p437_mark(P437_C6_DRIVER_POST,\n\t\t\t\t(u64)(s64)ret, (u64)(unsigned long)filp->f_op->open, NULL);\n\t\tif (a52t) a52_ackfr_record','char driver')
    return s+'\n/* '+MARK+': chrdev handoff corridor. */\n'

def apply(root):
    files={REC:patch_rec,OPEN:(lambda s: patch_fopen(patch_open(s))),NAMEI:patch_namei,CHAR:patch_char}
    for rel,fn in files.items():
        p=root/rel
        if not p.is_file():die('missing '+str(rel))
        p.write_text(fn(p.read_text(errors='replace')))
    (root/HDR).write_text(HDR_C)
    (root/HELPER).write_text(HELPER_C)
    p=root/MAKE;s=p.read_text();line='obj-y += a52_phase437_open.o'
    if line not in s:p.write_text(s.rstrip()+'\n# '+MARK+'\n'+line+'\n')

def validate(root):
    rec=(root/REC).read_text(errors='replace'); op=(root/OPEN).read_text(errors='replace'); na=(root/NAMEI).read_text(errors='replace'); ch=(root/CHAR).read_text(errors='replace'); he=(root/HELPER).read_text(errors='replace'); hd=(root/HDR).read_text(errors='replace')
    for tok in ['(SZ_1M - SZ_16K - SZ_8K)','P437_O1_ACK_PRE','P437_O2_ACK_POST','current->group_leader','a52_p436_arm_target();','P437_O5_FILP_PRE','P437_O6_FILP_POST']:
        if tok not in rec+op:die('open/rec token missing '+tok)
    for tok in ['P437_N0_DO_FILP_ENTRY','P437_N1_PATH_OPENAT_ENTRY','P437_N7_VFS_PRE']:
        if tok not in na:die('namei token missing '+tok)
    for tok in ['P437_V0_VFS_OPEN_ENTRY','P437_D3_SECURITY_PRE','P437_D4_SECURITY_POST','P437_D6_DRIVER_POST']:
        if tok not in op:die('open token missing '+tok)
    for tok in ['P437_C0_CHRDEV_ENTRY','P437_C1_KOBJ_PRE','P437_C2_KOBJ_POST','P437_C6_DRIVER_POST']:
        if tok not in ch:die('char token missing '+tok)
    for tok in [MARK,'0xB1AFA000ULL','A52_P437_BYTES      0x2000U','core_initcall(a52_p437_init)']:
        if tok not in he:die('helper token missing '+tok)
    if hd.count('P437_')<32:die('stage enum incomplete')
    print('Phase437 KGSL open corridor: PASS')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--check-only',action='store_true');ns=ap.parse_args();root=ns.root.resolve()
    if not ns.check_only:apply(root)
    validate(root);return 0
if __name__=='__main__':raise SystemExit(main())
