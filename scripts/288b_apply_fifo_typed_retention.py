#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE288B_RETAINED_FIFO_CHAIN_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected exactly 1 match, found {n}')
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE288B_RETAINED_FIFO_CHAIN_V1
 * Fold exact Phase288 FIFO causal events into the same final-32 typed tail
 * that Phase286C replays immediately before the Phase280 ramoops freeze.
 * Types 18..22 extend the existing causal type map without adding another ring.
 */
static void a52_p288_capture_fmt(const char *fmt, va_list src)
{
	va_list ap;
	u64 v[A52_P286_VALUES] = { 0 };
	u8 type = 0, n = 0;

	if (!fmt || strncmp(fmt, "P288 ", 5))
		return;
	va_copy(ap, src);
	if (!strcmp(fmt, "P288 F0 c=%d s=%u f=%x cfg=%x")) {
		type = 18; n = 4;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned int); v[2] = va_arg(ap, unsigned int);
		v[3] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P288 F1 c=%d tg=%x w0=%x w1=%x")) {
		type = 19; n = 4;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned int); v[2] = va_arg(ap, unsigned int);
		v[3] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P288 F2 c=%d st=%x fs=%x tg=%x")) {
		type = 20; n = 4;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned int); v[2] = va_arg(ap, unsigned int);
		v[3] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P288 F3 c=%d dc=%x dl=%x fs=%x in=%x")) {
		type = 21; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned int); v[2] = va_arg(ap, unsigned int);
		v[3] = va_arg(ap, unsigned int); v[4] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P288 F4 c=%d sw=%u st=%x fs=%x in=%x")) {
		type = 22; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned int); v[2] = va_arg(ap, unsigned int);
		v[3] = va_arg(ap, unsigned int); v[4] = va_arg(ap, unsigned int);
	}
	va_end(ap);
	if (type)
		a52_p286_store(type, n, v);
}
'''


def patch(text: str) -> str:
    if MARK in text:
        return text
    for token in [
        'A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1',
        'A52_PHASE286C_PACKED_REPLAY_WIDTH_V1',
        'A52_PHASE287B_RETAINED_FETCH_PROVENANCE_V1',
        'static void a52_p287_capture_fmt(const char *fmt, va_list src)',
        'a52_p286_store(type, n, v);',
        'P286 R0 %llx %x %x %llx %llx',
        'P286 R1 %llx %llx %llx',
    ]:
        if token not in text:
            raise SystemExit('Phase288B retained-recorder prerequisite missing: ' + token)

    anchor = 'static void a52_p287_capture_fmt(const char *fmt, va_list src)\n'
    text = one(text, anchor, BLOCK + '\n' + anchor, 'Phase288 typed capture insertion')
    text = one(text,
        'return !strncmp(message, "P287 ", 5) ||\n       !strncmp(message, "P286 ", 5) ||',
        'return !strncmp(message, "P288 ", 5) ||\n       !strncmp(message, "P287 ", 5) ||\n       !strncmp(message, "P286 ", 5) ||',
        'critical P288 admission')
    text = one(text,
        'if (strncmp(fmt, "P287", 4) &&\n    strncmp(fmt, "P286", 4) &&',
        'if (strncmp(fmt, "P288", 4) &&\n    strncmp(fmt, "P287", 4) &&\n    strncmp(fmt, "P286", 4) &&',
        'focused P288 admission')
    text = one(text,
        '\tva_start(args, fmt);\n\ta52_p287_capture_fmt(fmt, args);\n\ta52_p286_capture_fmt(fmt, args);',
        '\tva_start(args, fmt);\n\ta52_p288_capture_fmt(fmt, args);\n\ta52_p287_capture_fmt(fmt, args);\n\ta52_p286_capture_fmt(fmt, args);',
        'P288 typed capture before packing')
    return text


def validate(text: str) -> None:
    for token in [
        MARK,
        'a52_p288_capture_fmt(fmt, args);',
        'return !strncmp(message, "P288 ", 5)',
        'strncmp(fmt, "P288", 4)',
        'type = 18; n = 4;', 'type = 19; n = 4;', 'type = 20; n = 4;',
        'type = 21; n = 5;', 'type = 22; n = 5;',
        'P286 R0 %llx %x %x %llx %llx', 'P286 R1 %llx %llx %llx',
    ]:
        if token not in text:
            raise SystemExit('Phase288B retention marker missing: ' + token)
    for fmt in [
        'P288 F0 c=%d s=%u f=%x cfg=%x',
        'P288 F1 c=%d tg=%x w0=%x w1=%x',
        'P288 F2 c=%d st=%x fs=%x tg=%x',
        'P288 F3 c=%d dc=%x dl=%x fs=%x in=%x',
        'P288 F4 c=%d sw=%u st=%x fs=%x in=%x',
    ]:
        if fmt not in text:
            raise SystemExit('Phase288B format capture missing: ' + fmt)
    cap = text.index('a52_p288_capture_fmt(fmt, args);')
    pack = text.find('vscnprintf', cap)
    if pack < 0 or cap > pack:
        raise SystemExit('Phase288 typed capture is not before recorder text packing')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', type=Path, required=True)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    path = args.root / REC
    if not path.is_file():
        raise SystemExit('missing recorder source')
    text = path.read_text()
    if not args.check_only:
        text = patch(text)
        path.write_text(text)
    validate(text)
    print('Phase288B retained FIFO causal-chain capture: PASS')


if __name__ == '__main__':
    main()
