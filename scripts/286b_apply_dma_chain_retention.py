#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
DSI = Path('drivers/a52_display/msm/dsi/dsi_ctrl.c')
MARK = 'A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1
 * Phase284 taught us that emitting a trace line is not sufficient evidence:
 * useful early records can be overwritten before ramoops is harvested.
 *
 * Capture the exact P286 varargs into a private circular tail buffer before
 * text packing, independent of the normal FDR ring. At the DMA timeout, replay
 * the final 32 causal events into the normal persistent recorder immediately
 * before Phase280 freezes it. This preserves the chain that directly led to
 * the timeout even if earlier live P286 lines were overwritten.
 *
 * Replay grammar, all values raw hexadecimal:
 *   P286 R0 q=<seq> t=<type> n=<argc> a=<v0> b=<v1>
 *   P286 R1 q=<seq> c=<v2> d=<v3> e=<v4>     (only when argc > 2)
 * Type map is emitted in the build artifact schema.
 */
#define A52_P286_TAIL 32U
#define A52_P286_VALUES 5U

struct a52_p286_sample {
	u64 seq;
	u64 v[A52_P286_VALUES];
	u8 type;
	u8 n;
};

static struct a52_p286_sample a52_p286_tail[A52_P286_TAIL];
static DEFINE_SPINLOCK(a52_p286_lock);
static atomic64_t a52_p286_seq = ATOMIC64_INIT(0);

static void a52_p286_store(u8 type, u8 n, const u64 *v)
{
	struct a52_p286_sample s;
	unsigned long flags;
	u64 seq;

	if (!type || !n || n > A52_P286_VALUES || !v)
		return;
	memset(&s, 0, sizeof(s));
	seq = (u64)atomic64_inc_return(&a52_p286_seq);
	s.seq = seq;
	s.type = type;
	s.n = n;
	memcpy(s.v, v, n * sizeof(v[0]));
	spin_lock_irqsave(&a52_p286_lock, flags);
	a52_p286_tail[(seq - 1) % A52_P286_TAIL] = s;
	spin_unlock_irqrestore(&a52_p286_lock, flags);
}

static void a52_p286_capture_fmt(const char *fmt, va_list src)
{
	va_list ap;
	u64 v[A52_P286_VALUES] = { 0 };
	u8 type = 0, n = 0;

	if (!fmt || strncmp(fmt, "P286 ", 5))
		return;
	va_copy(ap, src);
	if (!strcmp(fmt, "P286 A c=%d mf=%x f=%x t=%u l=%u")) {
		type = 1; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned int); v[2] = va_arg(ap, unsigned int);
		v[3] = va_arg(ap, unsigned int); v[4] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P286 B c=%d f=%x h=%x pm=%d ve=%d")) {
		type = 2; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned int); v[2] = va_arg(ap, unsigned int);
		v[3] = (u64)(s64)va_arg(ap, int); v[4] = (u64)(s64)va_arg(ap, int);
	} else if (!strcmp(fmt, "P286 D c=%d f=%x last=%d bm=%d b=%d")) {
		type = 3; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned int);
		v[2] = (u64)(s64)va_arg(ap, int); v[3] = (u64)(s64)va_arg(ap, int);
		v[4] = (u64)(s64)va_arg(ap, int);
	} else if (!strcmp(fmt, "P286 DX c=%d reason=nolast")) {
		type = 4; n = 1; v[0] = (u64)(s64)va_arg(ap, int);
	} else if (!strcmp(fmt, "P286 E c=%d k=slave")) {
		type = 5; n = 1; v[0] = (u64)(s64)va_arg(ap, int);
	} else if (!strcmp(fmt, "P286 E c=%d k=sched cl=%u sl=%u lb=%u")) {
		type = 6; n = 4;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P286 E c=%d k=master")) {
		type = 7; n = 1; v[0] = (u64)(s64)va_arg(ap, int);
	} else if (!strcmp(fmt, "P286 W c=%d r=%d irq=%d")) {
		type = 8; n = 3;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = (u64)(s64)va_arg(ap, int);
		v[2] = (u64)(s64)va_arg(ap, int);
	} else if (!strcmp(fmt, "P286 T c=%d st=%x done=%d irq=%d")) {
		type = 9; n = 4;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned int);
		v[2] = (u64)(s64)va_arg(ap, int); v[3] = (u64)(s64)va_arg(ap, int);
	} else if (!strcmp(fmt, "P286 G c=%d st=%x irq0=%d")) {
		type = 10; n = 3;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned int);
		v[2] = (u64)(s64)va_arg(ap, int);
	} else if (!strcmp(fmt, "P286 HK c=%d o=%x l=%x f=%x sw=1")) {
		type = 11; n = 4;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P286 HK c=%d o=%x l=%x f=%x sw=0")) {
		type = 12; n = 4;
		v[0] = (u64)(s64)va_arg(ap, int); v[1] = va_arg(ap, unsigned int);
		v[2] = va_arg(ap, unsigned int); v[3] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P286 HT c=%d sw=1")) {
		type = 13; n = 1; v[0] = (u64)(s64)va_arg(ap, int);
	}
	va_end(ap);
	if (type)
		a52_p286_store(type, n, v);
}

void a52_p286_flush_timeout_chain(void)
{
	u64 total = (u64)atomic64_read(&a52_p286_seq);
	u64 first = total > A52_P286_TAIL ? total - A52_P286_TAIL + 1 : 1;
	u64 q;

	a52_ackfr_record("P286 RH n=%llx first=%llx", (unsigned long long)total,
		(unsigned long long)first);
	for (q = first; q <= total; q++) {
		struct a52_p286_sample s;
		unsigned long flags;

		memset(&s, 0, sizeof(s));
		spin_lock_irqsave(&a52_p286_lock, flags);
		s = a52_p286_tail[(q - 1) % A52_P286_TAIL];
		spin_unlock_irqrestore(&a52_p286_lock, flags);
		if (s.seq != q || !s.type || !s.n)
			continue;
		a52_ackfr_record("P286 R0 q=%llx t=%x n=%x a=%llx b=%llx",
			(unsigned long long)s.seq, s.type, s.n,
			(unsigned long long)s.v[0], (unsigned long long)s.v[1]);
		if (s.n > 2)
			a52_ackfr_record("P286 R1 q=%llx c=%llx d=%llx e=%llx",
				(unsigned long long)s.seq, (unsigned long long)s.v[2],
				(unsigned long long)s.v[3], (unsigned long long)s.v[4]);
	}
}
EXPORT_SYMBOL_GPL(a52_p286_flush_timeout_chain);
'''


def patch_rec(text: str) -> str:
    if MARK in text:
        return text
    for token in ['A52_PHASE285_LATCHED_CLOCK_CHAIN_VALUES_V1',
                  'a52_p285_capture_fmt(fmt, args);',
                  'strncmp(fmt, "P285", 4)',
                  'strncmp(message, "P285 ", 5)']:
        if token not in text:
            raise SystemExit('Phase286B recorder prerequisite missing: ' + token)
    text = one(text, 'static struct rs_control *a52_r179_rs;\n',
               'static struct rs_control *a52_r179_rs;\n' + BLOCK + '\n',
               'typed-tail insertion')
    text = one(text,
               'return !strncmp(message, "P285 ", 5) ||\n       !strncmp(message, "P276 ", 5) ||',
               'return !strncmp(message, "P286 ", 5) ||\n       !strncmp(message, "P285 ", 5) ||\n       !strncmp(message, "P276 ", 5) ||',
               'critical P286 admission')
    text = one(text,
               'if (strncmp(fmt, "P285", 4) &&\n    strncmp(fmt, "P276", 4) &&',
               'if (strncmp(fmt, "P286", 4) &&\n    strncmp(fmt, "P285", 4) &&\n    strncmp(fmt, "P276", 4) &&',
               'focused P286 admission')
    text = one(text,
               '\tva_start(args, fmt);\n\ta52_p285_capture_fmt(fmt, args);\n\tvscnprintf(event.message, sizeof(event.message), fmt, args);',
               '\tva_start(args, fmt);\n\ta52_p286_capture_fmt(fmt, args);\n\ta52_p285_capture_fmt(fmt, args);\n\tvscnprintf(event.message, sizeof(event.message), fmt, args);',
               'typed capture before packing')
    return text


def patch_dsi(text: str) -> str:
    if 'a52_p286_flush_timeout_chain();' in text:
        return text
    for token in ['A52_PHASE286_GOLDEN_FDR_DMA_CHAIN_V1',
                  'extern void a52_ackfr_retain_timeout_snapshot(void);',
                  'P276 280Z q=2']:
        if token not in text:
            raise SystemExit('Phase286B DSI prerequisite missing: ' + token)
    # Phase286A's original generic status anchor matched the 50us slave-status
    # polling loop instead of the real 200ms completion-timeout path. Move T
    # to the timeout status read before retaining the causal tail.
    misplaced = '''\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 T c=%d st=%x done=%d irq=%d",\n\t\t\t\tdsi_ctrl->cell_index, status, !!(status & mask),\n\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\tif (status & mask) {\n'''
    clean_poll = '''\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n\t\tif (status & mask) {\n'''
    if misplaced in text:
        text = one(text, misplaced, clean_poll, 'remove misplaced timeout marker')
    wait_fn = text.index('static void dsi_ctrl_dma_cmd_wait_for_done(')
    freeze_fn = text.index('P276 280Z q=2', wait_fn)
    timeout_fmt = 'P286 T c=%d st=%x done=%d irq=%d'
    if timeout_fmt not in text[wait_fn:freeze_fn]:
        real_old = '''\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P276 P S st=%x dn=%u a=%d im=%x ir=%u",\n'''
        real_new = '''\t\tstatus = dsi_hw_ops.get_interrupt_status(&dsi_ctrl->hw);\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P286 T c=%d st=%x done=%d irq=%d",\n\t\t\t\tdsi_ctrl->cell_index, status, !!(status & mask),\n\t\t\t\tatomic_read(&dsi_ctrl->dma_irq_trig));\n\t\tif (a52_p276r_deep_active())\n\t\t\ta52_ackfr_record("P276 P S st=%x dn=%u a=%d im=%x ir=%u",\n'''
        text = one(text, real_old, real_new, 'real 200ms timeout marker')
    text = one(text,
               'extern void a52_ackfr_retain_timeout_snapshot(void);\n',
               'extern void a52_ackfr_retain_timeout_snapshot(void);\nextern void a52_p286_flush_timeout_chain(void); /* ' + MARK + ' */\n',
               'flush declaration')
    text = one(text,
               '\t\t\ta52_ackfr_record("P276 282Z q=2");\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n',
               '\t\t\ta52_ackfr_record("P276 282Z q=2");\n\t\t\ta52_p286_flush_timeout_chain();\n\t\t\ta52_ackfr_record("P276 280Z q=2");\n\t\t\ta52_ackfr_retain_timeout_snapshot();\n',
               'flush immediately before retention freeze')
    return text


def validate(rec: str, dsi: str) -> None:
    required_rec = [
        MARK, '#define A52_P286_TAIL 32U', 'a52_p286_capture_fmt(fmt, args);',
        'return !strncmp(message, "P286 ", 5)', 'strncmp(fmt, "P286", 4)',
        'P286 RH n=%llx first=%llx', 'P286 R0 q=%llx t=%x n=%x a=%llx b=%llx',
        'P286 R1 q=%llx c=%llx d=%llx e=%llx',
        'EXPORT_SYMBOL_GPL(a52_p286_flush_timeout_chain);'
    ]
    for token in required_rec:
        if token not in rec:
            raise SystemExit('Phase286B recorder marker missing: ' + token)
    for fmt in [
        'P286 A c=%d mf=%x f=%x t=%u l=%u', 'P286 B c=%d f=%x h=%x pm=%d ve=%d',
        'P286 D c=%d f=%x last=%d bm=%d b=%d', 'P286 DX c=%d reason=nolast',
        'P286 E c=%d k=slave', 'P286 E c=%d k=sched cl=%u sl=%u lb=%u',
        'P286 E c=%d k=master', 'P286 W c=%d r=%d irq=%d',
        'P286 T c=%d st=%x done=%d irq=%d', 'P286 G c=%d st=%x irq0=%d',
        'P286 HK c=%d o=%x l=%x f=%x sw=1', 'P286 HK c=%d o=%x l=%x f=%x sw=0',
        'P286 HT c=%d sw=1']:
        if fmt not in rec:
            raise SystemExit('Phase286B exact format not captured: ' + fmt)
    if 'a52_p286_flush_timeout_chain();' not in dsi:
        raise SystemExit('Phase286B timeout flush missing')
    if not (dsi.index('P286 T c=%d st=%x done=%d irq=%d') <
            dsi.index('a52_p286_flush_timeout_chain();') <
            dsi.index('P276 280Z q=2') <
            dsi.index('a52_ackfr_retain_timeout_snapshot();')):
        raise SystemExit('Phase286B replay/freeze ordering invalid')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    rp, dp = args.root / REC, args.root / DSI
    if not rp.is_file() or not dp.is_file():
        raise SystemExit('Phase286B source files missing')
    r, d = rp.read_text(), dp.read_text()
    if not args.check_only:
        r, d = patch_rec(r), patch_dsi(d)
        rp.write_text(r); dp.write_text(d)
    validate(r, d)
    print('Phase286B typed DMA-chain retention + pre-freeze replay: PASS')


if __name__ == '__main__':
    main()
