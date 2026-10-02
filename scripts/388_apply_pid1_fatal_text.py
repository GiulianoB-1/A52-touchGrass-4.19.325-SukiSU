#!/usr/bin/env python3
from pathlib import Path
import sys

MARK="A52_PHASE388_PID1_FATAL_TEXT_V1"

def one(path, old, new, label):
    t=path.read_text()
    if new in t:
        print(f"{path}: {label} already applied")
        return
    n=t.count(old)
    if n!=1:
        raise SystemExit(f"{path}: {label}: expected 1 anchor, found {n}")
    path.write_text(t.replace(old,new,1))
    print(f"{path}: applied {label}")

def main():
    if len(sys.argv)!=2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")
    root=Path(sys.argv[1]).resolve()
    sysc=root/"arch/arm64/kernel/syscall.c"
    if not sysc.is_file():
        raise SystemExit(f"missing {sysc}")

    one(
        sysc,
        "#include <linux/sched.h>\n#include <linux/a52_p383_dual_recorder.h>\n",
        "#include <linux/sched.h>\n#include <linux/uaccess.h>\n#include <linux/uio.h>\n#include <linux/a52_p383_dual_recorder.h>\n",
        "fatal-text capture includes",
    )

    old_struct=r'''struct a52_p387_sysrec {
	u64 seq;
	s32 nr;
	s32 valid;
	u64 pc;
	u64 x0;
	u64 x1;
	s64 ret;
};
'''
    new_struct=r'''struct a52_p387_sysrec {
	u64 seq;
	s32 nr;
	s32 valid;
	u64 pc;
	u64 x0;
	u64 x1;
	u64 x2;
	u64 x3;
	s64 ret;
};

/*
 * A52_PHASE388_PID1_FATAL_TEXT_V1
 *
 * Capture stderr writev payloads in RAM before sys_writev() sees the closed
 * fd.  This lets us recover Bionic's fatal message even when fd 2 is invalid.
 */
#define A52_P388_MSGS 4U
#define A52_P388_MSG_BYTES 240U

struct a52_p388_msg {
	u64 seq;
	u32 len;
	u32 valid;
	char text[A52_P388_MSG_BYTES];
};

static struct a52_p388_msg a52_p388_msgs[A52_P388_MSGS];
static atomic_t a52_p388_msg_count = ATOMIC_INIT(0);

static void a52_p388_capture_writev(struct pt_regs *regs, int scno, u64 seq)
{
	struct a52_p388_msg *m;
	struct iovec iov;
	unsigned long iovp;
	unsigned long iovcnt;
	unsigned int slot;
	unsigned int i;
	size_t used = 0;

	if (scno != __NR_writev || regs->regs[0] != 2)
		return;

	slot = (unsigned int)atomic_fetch_inc(&a52_p388_msg_count);
	if (slot >= A52_P388_MSGS)
		return;

	m = &a52_p388_msgs[slot];
	WRITE_ONCE(m->valid, 0);
	m->seq = seq;
	m->len = 0;
	memset(m->text, 0, sizeof(m->text));

	iovp = regs->regs[1];
	iovcnt = min_t(unsigned long, regs->regs[2], 8UL);

	for (i = 0; i < iovcnt && used < A52_P388_MSG_BYTES - 1U; i++) {
		size_t want;
		size_t j;

		if (copy_from_user(&iov,
			(const void __user *)(iovp + i * sizeof(iov)),
			sizeof(iov)))
			break;

		want = min_t(size_t, iov.iov_len,
			A52_P388_MSG_BYTES - 1U - used);
		if (!want)
			continue;
		if (copy_from_user(m->text + used, iov.iov_base, want))
			break;

		for (j = 0; j < want; j++) {
			unsigned char c = m->text[used + j];
			if (c == '\n' || c == '\r' || c == '\t')
				m->text[used + j] = ' ';
			else if (c < 0x20 || c > 0x7e)
				m->text[used + j] = '.';
		}
		used += want;
	}

	m->text[used] = '\0';
	m->len = (u32)used;
	smp_wmb();
	WRITE_ONCE(m->valid, 1);
}

static void a52_p388_dump_messages(void)
{
	unsigned int count = min_t(unsigned int,
		(unsigned int)atomic_read(&a52_p388_msg_count), A52_P388_MSGS);
	unsigned int i;

	for (i = 0; i < count; i++) {
		struct a52_p388_msg *m = &a52_p388_msgs[i];
		unsigned int off = 0;
		unsigned int part = 0;

		if (!READ_ONCE(m->valid))
			continue;

		a52_p383_record("P388 MHEAD m=%u q=%llu len=%u",
			i, (unsigned long long)m->seq, READ_ONCE(m->len));

		while (off < READ_ONCE(m->len)) {
			char chunk[65];
			unsigned int n = min_t(unsigned int, 64U,
				READ_ONCE(m->len) - off);

			memcpy(chunk, m->text + off, n);
			chunk[n] = '\0';
			a52_p383_record("P388 MSG m=%u p=%u %s", i, part, chunk);
			off += n;
			part++;
		}
	}
}
'''
    one(sysc,old_struct,new_struct,"extend ring args and capture stderr writev")

    old_enter=r'''	WRITE_ONCE(r->x0, regs->regs[0]);
	WRITE_ONCE(r->x1, regs->regs[1]);
	WRITE_ONCE(r->ret, (s64)0x8000000000000000ULL);
	smp_wmb();
	WRITE_ONCE(r->valid, 1);
	return (int)slot;
'''
    new_enter=r'''	WRITE_ONCE(r->x0, regs->regs[0]);
	WRITE_ONCE(r->x1, regs->regs[1]);
	WRITE_ONCE(r->x2, regs->regs[2]);
	WRITE_ONCE(r->x3, regs->regs[3]);
	WRITE_ONCE(r->ret, (s64)0x8000000000000000ULL);
	a52_p388_capture_writev(regs, scno, seq);
	smp_wmb();
	WRITE_ONCE(r->valid, 1);
	return (int)slot;
'''
    one(sysc,old_enter,new_enter,"record x2/x3 and writev payload")

    old_dump=r'''		a52_p383_record("P387 S q=%llu nr=%d ret=%llx x0=%llx x1=%llx",
			(unsigned long long)seq,
			READ_ONCE(r->nr),
			(unsigned long long)READ_ONCE(r->ret),
			(unsigned long long)READ_ONCE(r->x0),
			(unsigned long long)READ_ONCE(r->x1));
		dumped++;
	}

	a52_p383_record_critical("P387 SDONE n=%u last=%llu",
'''
    new_dump=r'''		a52_p383_record("P387 S q=%llu nr=%d ret=%llx x0=%llx x1=%llx",
			(unsigned long long)seq,
			READ_ONCE(r->nr),
			(unsigned long long)READ_ONCE(r->ret),
			(unsigned long long)READ_ONCE(r->x0),
			(unsigned long long)READ_ONCE(r->x1));

		if (READ_ONCE(r->nr) == __NR_mprotect ||
		    READ_ONCE(r->nr) == __NR_prctl ||
		    READ_ONCE(r->nr) == __NR_rt_tgsigqueueinfo ||
		    READ_ONCE(r->nr) == __NR_writev) {
			a52_p383_record("P388 A q=%llu x2=%llx x3=%llx pc=%llx",
				(unsigned long long)seq,
				(unsigned long long)READ_ONCE(r->x2),
				(unsigned long long)READ_ONCE(r->x3),
				(unsigned long long)READ_ONCE(r->pc));
		}
		dumped++;
	}

	a52_p388_dump_messages();
	a52_p383_record_critical("P387 SDONE n=%u last=%llu",
'''
    one(sysc,old_dump,new_dump,"dump extended args and fatal text")

    txt=sysc.read_text()
    for tok in [MARK,"a52_p388_capture_writev","P388 MHEAD","P388 MSG","P388 A q="]:
        if tok not in txt:
            raise SystemExit(f"missing {tok}")
    print("A52 Phase388 PID1 fatal-text capture applied successfully")

if __name__=="__main__":
    main()
