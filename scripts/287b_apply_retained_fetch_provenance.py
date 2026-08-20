#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path

REC = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
MARK = 'A52_PHASE287B_RETAINED_FETCH_PROVENANCE_V1'


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f'{label}: expected 1 match, found {n}')
    return text.replace(old, new, 1)


BLOCK = r'''
/* A52_PHASE287B_RETAINED_FETCH_PROVENANCE_V1
 * Fold Phase287 provenance into the already-retained Phase286 causal tail.
 * Types 14..17 extend the Phase286 replay type map. No second retention path
 * is introduced, so Phase287 inherits the same pre-freeze overwrite guarantee.
 */
static void a52_p287_capture_fmt(const char *fmt, va_list src)
{
	va_list ap;
	u64 v[A52_P286_VALUES] = { 0 };
	u8 type = 0, n = 0;
	unsigned int i;

	if (!fmt || strncmp(fmt, "P287 ", 5))
		return;
	va_copy(ap, src);
	if (!strcmp(fmt, "P287 M0 c=%d i=%llx va=%llx pre=%u add=%u")) {
		type = 14; n = 5;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned long long);
		v[2] = va_arg(ap, unsigned long long);
		v[3] = va_arg(ap, unsigned int); v[4] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P287 M1 c=%d i=%llx len=%u last=1")) {
		type = 15; n = 3;
		v[0] = (u64)(s64)va_arg(ap, int);
		v[1] = va_arg(ap, unsigned long long); v[2] = va_arg(ap, unsigned int);
	} else if (!strcmp(fmt, "P287 M2 c=%d b=%02x%02x%02x%02x%02x%02x%02x%02x")) {
		type = 16; n = 2;
		v[0] = (u64)(s64)va_arg(ap, int);
		for (i = 0; i < 8; i++)
			v[1] |= ((u64)(u8)va_arg(ap, int)) << (i * 8);
	} else if (!strcmp(fmt, "P287 R c=%d ro=%x rl=%x ax=%x vb=%x")) {
		type = 17; n = 5;
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
    for token in ['A52_PHASE286B_DMA_CHAIN_TYPED_RETENTION_V1',
                  'A52_PHASE286C_PACKED_REPLAY_WIDTH_V1',
                  'a52_p286_store(type, n, v);',
                  'a52_p286_capture_fmt(fmt, args);']:
        if token not in text:
            raise SystemExit('Phase287B requires retained Phase286C recorder: ' + token)
    anchor = '''\tif (type)\n\t\ta52_p286_store(type, n, v);\n}\n\nvoid a52_p286_flush_timeout_chain(void)\n'''
    repl = '''\tif (type)\n\t\ta52_p286_store(type, n, v);\n}\n''' + BLOCK + '''\nvoid a52_p286_flush_timeout_chain(void)\n'''
    text = one(text, anchor, repl, 'Phase287 typed capture insertion')
    text = one(text,
               'return !strncmp(message, "P286 ", 5) ||\n       !strncmp(message, "P285 ", 5) ||',
               'return !strncmp(message, "P287 ", 5) ||\n       !strncmp(message, "P286 ", 5) ||\n       !strncmp(message, "P285 ", 5) ||',
               'critical P287 admission')
    text = one(text,
               'if (strncmp(fmt, "P286", 4) &&\n    strncmp(fmt, "P285", 4) &&',
               'if (strncmp(fmt, "P287", 4) &&\n    strncmp(fmt, "P286", 4) &&\n    strncmp(fmt, "P285", 4) &&',
               'focused P287 admission')
    text = one(text,
               '\tva_start(args, fmt);\n\ta52_p286_capture_fmt(fmt, args);\n\ta52_p285_capture_fmt(fmt, args);',
               '\tva_start(args, fmt);\n\ta52_p287_capture_fmt(fmt, args);\n\ta52_p286_capture_fmt(fmt, args);\n\ta52_p285_capture_fmt(fmt, args);',
               'P287 capture before packing')
    return text


def validate(text: str) -> None:
    for token in [
        MARK, 'a52_p287_capture_fmt(fmt, args);',
        'return !strncmp(message, "P287 ", 5)', 'strncmp(fmt, "P287", 4)',
        'type = 14; n = 5;', 'type = 15; n = 3;', 'type = 16; n = 2;',
        'type = 17; n = 5;', 'a52_p286_store(type, n, v);',
        'P286 R0 %llx %x %x %llx %llx', 'P286 R1 %llx %llx %llx'
    ]:
        if token not in text:
            raise SystemExit('Phase287B marker missing: ' + token)
    for fmt in [
        'P287 M0 c=%d i=%llx va=%llx pre=%u add=%u',
        'P287 M1 c=%d i=%llx len=%u last=1',
        'P287 M2 c=%d b=%02x%02x%02x%02x%02x%02x%02x%02x',
        'P287 R c=%d ro=%x rl=%x ax=%x vb=%x']:
        if fmt not in text:
            raise SystemExit('Phase287B format capture missing: ' + fmt)


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
    print('Phase287B retained DMA fetch provenance: PASS')


if __name__ == '__main__':
    main()
