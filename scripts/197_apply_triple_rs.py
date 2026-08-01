#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

RECORDER = Path('drivers/a52_secure/a52_ack_secure_flight_recorder.c')
RAMOOPS = Path('fs/pstore/ram.c')
MAIN = Path('init/main.c')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one match, found {count}')
    return text.replace(old, new, 1)


def insert_before_once(text: str, marker: str, insertion: str, label: str) -> str:
    count = text.count(marker)
    if count != 1:
        raise SystemExit(f'{label}: expected exactly one marker, found {count}')
    return text.replace(marker, insertion + marker, 1)


def patch_recorder(text: str) -> str:
    text = replace_once(
        text,
        ' * to the console and ftrace RAMOOPS banks. CRC is intentionally not used in\n',
        ' * to the record, console and ftrace RAMOOPS banks. CRC is intentionally not used in\n',
        'recorder bank comment',
    )
    text = replace_once(
        text,
        '#define A52_R179_BANK_CONSOLE BIT(0)\n'
        '#define A52_R179_BANK_FTRACE BIT(1)\n'
        '#define A52_R179_BANK_BOTH (A52_R179_BANK_CONSOLE | A52_R179_BANK_FTRACE)\n',
        '#define A52_R179_BANK_CONSOLE BIT(0)\n'
        '#define A52_R179_BANK_FTRACE BIT(1)\n'
        '#define A52_R179_BANK_RECORD BIT(2)\n'
        '#define A52_R179_BANK_ALL (A52_R179_BANK_CONSOLE | \\\n'
        '\t\t\t   A52_R179_BANK_FTRACE | A52_R179_BANK_RECORD)\n',
        'recorder bank masks',
    )
    count = text.count('A52_R179_BANK_BOTH')
    if count != 2:
        raise SystemExit(f'recorder all-bank uses: expected 2, found {count}')
    text = text.replace('A52_R179_BANK_BOTH', 'A52_R179_BANK_ALL')
    text = replace_once(
        text,
        'a52_ackfr_record("BOOT ctl=%s rel=%s cap=%u rs=%u copies=2 crc=0",',
        'a52_ackfr_record("BOOT ctl=%s rel=%s cap=%u rs=%u copies=3 crc=0",',
        'recorder control copy count',
    )
    text = replace_once(
        text,
        'a52_ackfr_record("BOOT rs=ready phase=179 roots=%u copies=2 crc=0",',
        'a52_ackfr_record("BOOT rs=ready phase=197 roots=%u copies=3 crc=0",',
        'recorder ready marker',
    )
    text = replace_once(
        text,
        'pr_info("phase179 recorder enabled stored=%llu dropped=%llu\\n",',
        'pr_info("phase197 triple-copy recorder enabled stored=%llu dropped=%llu\\n",',
        'recorder printk profile',
    )
    return text


def patch_ramoops(text: str) -> str:
    text = replace_once(
        text,
        '#define A52_ACKFR_BANK_CONSOLE BIT(0)\n'
        '#define A52_ACKFR_BANK_FTRACE BIT(1)\n',
        '#define A52_ACKFR_BANK_CONSOLE BIT(0)\n'
        '#define A52_ACKFR_BANK_FTRACE BIT(1)\n'
        '#define A52_ACKFR_BANK_RECORD BIT(2)\n',
        'ramoops writer bank masks',
    )
    text = replace_once(
        text,
        'void a52_persistent_diag_mark(const char *fmt, ...);\n'
        'void a52_persistent_diag_mark_ftrace(const char *fmt, ...);\n',
        'void a52_persistent_diag_mark(const char *fmt, ...);\n'
        'void a52_persistent_diag_mark_ftrace(const char *fmt, ...);\n'
        'void a52_persistent_diag_mark_record(const char *fmt, ...);\n',
        'ramoops mark declarations',
    )
    text = replace_once(
        text,
        '\tif (targets & A52_ACKFR_BANK_FTRACE) {\n'
        '\t\ta52_persistent_diag_mark_ftrace("%.*s", (int)len, buf);\n'
        '\t\twritten |= A52_ACKFR_BANK_FTRACE;\n'
        '\t}\n'
        '\twmb();\n',
        '\tif (targets & A52_ACKFR_BANK_FTRACE) {\n'
        '\t\ta52_persistent_diag_mark_ftrace("%.*s", (int)len, buf);\n'
        '\t\twritten |= A52_ACKFR_BANK_FTRACE;\n'
        '\t}\n'
        '\tif (targets & A52_ACKFR_BANK_RECORD) {\n'
        '\t\ta52_persistent_diag_mark_record("%.*s", (int)len, buf);\n'
        '\t\twritten |= A52_ACKFR_BANK_RECORD;\n'
        '\t}\n'
        '\twmb();\n',
        'ramoops third writer branch',
    )
    text = replace_once(
        text,
        '#define A52_DIAG_CONSOLE_PHYS 0xB1B40000ULL\n'
        '#define A52_DIAG_CONSOLE_SIZE 0x00040000UL\n',
        '#define A52_DIAG_RECORD_PHYS 0xB1B00000ULL\n'
        '#define A52_DIAG_RECORD_SIZE 0x00040000UL\n'
        '#define A52_DIAG_CONSOLE_PHYS 0xB1B40000ULL\n'
        '#define A52_DIAG_CONSOLE_SIZE 0x00040000UL\n',
        'record bank physical range',
    )
    text = replace_once(
        text,
        'static u32 a52_diag_ftrace_raw_start;\n'
        'static u32 a52_diag_ftrace_raw_size;\n',
        'static u32 a52_diag_ftrace_raw_start;\n'
        'static u32 a52_diag_ftrace_raw_size;\n'
        'static struct persistent_ram_zone *a52_diag_record_prz;\n'
        'static u8 __iomem *a52_diag_record_raw;\n'
        'static u32 a52_diag_record_raw_start;\n'
        'static u32 a52_diag_record_raw_size;\n',
        'record bank globals',
    )

    record_raw = r'''/* A52_DIAG_RECORD_MIRROR */
static void a52_diag_record_raw_write(const char *s, unsigned int count)
{
	unsigned int first;

	if (!a52_diag_record_raw || !count)
		return;
	if (count > A52_DIAG_DATA_SIZE) {
		s += count - A52_DIAG_DATA_SIZE;
		count = A52_DIAG_DATA_SIZE;
	}
	first = min_t(unsigned int, count,
		      A52_DIAG_DATA_SIZE - a52_diag_record_raw_start);
	memcpy_toio(a52_diag_record_raw + A52_DIAG_HEADER_SIZE +
		    a52_diag_record_raw_start, s, first);
	if (count > first)
		memcpy_toio(a52_diag_record_raw + A52_DIAG_HEADER_SIZE,
			    s + first, count - first);
	a52_diag_record_raw_start += count;
	while (a52_diag_record_raw_start >= A52_DIAG_DATA_SIZE)
		a52_diag_record_raw_start -= A52_DIAG_DATA_SIZE;
	a52_diag_record_raw_size = min_t(u32, A52_DIAG_DATA_SIZE,
				      a52_diag_record_raw_size + count);
	wmb();
	writel_relaxed(a52_diag_record_raw_start, a52_diag_record_raw + 4);
	writel_relaxed(a52_diag_record_raw_size, a52_diag_record_raw + 8);
	wmb();
}

'''
    text = insert_before_once(
        text,
        '/* A52_DIAG_FTRACE_MIRROR */\n',
        record_raw,
        'record raw writer insertion',
    )

    record_mark = r'''void a52_persistent_diag_mark_record(const char *fmt, ...)
{
	char line[A52_DIAG_LINE_SIZE];
	va_list args;
	int len;

	if (IS_ERR_OR_NULL(a52_diag_record_prz) && !a52_diag_record_raw)
		return;
	va_start(args, fmt);
	len = vscnprintf(line, sizeof(line), fmt, args);
	va_end(args);
	if (len <= 0)
		return;
	if (a52_diag_record_prz)
		persistent_ram_write(a52_diag_record_prz, line, len);
	else
		a52_diag_record_raw_write(line, len);
	wmb();
}

'''
    text = insert_before_once(
        text,
        'void a52_persistent_diag_mark_ftrace(const char *fmt, ...)\n',
        record_mark,
        'record mark function insertion',
    )

    record_init = r'''static int __init a52_persistent_diag_record_init(void)
{
	struct persistent_ram_ecc_info ecc = { };
	int prz_ret;

	if (a52_diag_record_prz || a52_diag_record_raw)
		return 0;
	a52_diag_record_prz = persistent_ram_new(A52_DIAG_RECORD_PHYS,
					 A52_DIAG_RECORD_SIZE, 0, &ecc,
					 1, PRZ_FLAG_ZAP_OLD,
					 "a52-early-record");
	if (!IS_ERR(a52_diag_record_prz)) {
		a52_diag_record_prz->type = PSTORE_TYPE_DMESG;
		return 0;
	}
	prz_ret = PTR_ERR(a52_diag_record_prz);
	a52_diag_record_prz = NULL;
	a52_diag_record_raw = ioremap_wc(A52_DIAG_RECORD_PHYS,
					 A52_DIAG_RECORD_SIZE);
	if (!a52_diag_record_raw)
		a52_diag_record_raw = ioremap(A52_DIAG_RECORD_PHYS,
					      A52_DIAG_RECORD_SIZE);
	if (!a52_diag_record_raw)
		return prz_ret ? prz_ret : -ENOMEM;
	memset_io(a52_diag_record_raw, 0, A52_DIAG_RECORD_SIZE);
	writel_relaxed(A52_DIAG_PERSISTENT_RAM_SIG, a52_diag_record_raw);
	writel_relaxed(0, a52_diag_record_raw + 4);
	writel_relaxed(0, a52_diag_record_raw + 8);
	a52_diag_record_raw_start = 0;
	a52_diag_record_raw_size = 0;
	wmb();
	return 0;
}

'''
    text = insert_before_once(
        text,
        'static int __init a52_persistent_diag_ftrace_init(void)\n',
        record_init,
        'record bank init insertion',
    )

    return_count = text.count('return a52_persistent_diag_ftrace_init();')
    if return_count != 3:
        raise SystemExit(
            f'ramoops mirror-init returns: expected 3, found {return_count}'
        )
    text = text.replace(
        'return a52_persistent_diag_ftrace_init();',
        'return a52_persistent_diag_all_mirrors_init();',
    )

    all_init = r'''static int __init a52_persistent_diag_all_mirrors_init(void)
{
	int ret;

	ret = a52_persistent_diag_ftrace_init();
	if (ret)
		return ret;
	return a52_persistent_diag_record_init();
}

'''
    text = insert_before_once(
        text,
        'int __init a52_persistent_diag_init(void)\n',
        all_init,
        'all-mirror init insertion',
    )
    return text


def patch_main(text: str) -> str:
    text = replace_once(
        text,
        'extern void a52_persistent_diag_mark(const char *fmt, ...);\n'
        'extern void a52_persistent_diag_mark_ftrace(const char *fmt, ...);\n',
        'extern void a52_persistent_diag_mark(const char *fmt, ...);\n'
        'extern void a52_persistent_diag_mark_ftrace(const char *fmt, ...);\n'
        'extern void a52_persistent_diag_mark_record(const char *fmt, ...);\n',
        'main record declaration',
    )
    text = replace_once(
        text,
        'static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n'
        'static inline void a52_persistent_diag_mark_ftrace(const char *fmt, ...) { }\n',
        'static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n'
        'static inline void a52_persistent_diag_mark_ftrace(const char *fmt, ...) { }\n'
        'static inline void a52_persistent_diag_mark_record(const char *fmt, ...) { }\n',
        'main record stub',
    )
    text = replace_once(
        text,
        '\t\ta52_persistent_diag_mark_ftrace(\n'
        '\t\t\t"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "\n'
        '\t\t\t"metadata_only=1 commit=5a52c0de\\n");\n',
        '\t\ta52_persistent_diag_mark_ftrace(\n'
        '\t\t\t"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "\n'
        '\t\t\t"metadata_only=1 commit=5a52c0de\\n");\n'
        '\t\ta52_persistent_diag_mark_record(\n'
        '\t\t\t"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "\n'
        '\t\t\t"metadata_only=1 commit=5a52c0de\\n");\n',
        'main third early marker',
    )
    return text


def patch_tree(root: Path) -> None:
    recorder = root / RECORDER
    ramoops = root / RAMOOPS
    main = root / MAIN
    recorder.write_text(patch_recorder(recorder.read_text()), encoding='utf-8')
    ramoops.write_text(patch_ramoops(ramoops.read_text()), encoding='utf-8')
    main.write_text(patch_main(main.read_text()), encoding='utf-8')


def self_test() -> None:
    recorder = ''' * to the console and ftrace RAMOOPS banks. CRC is intentionally not used in\n#define A52_R179_BANK_CONSOLE BIT(0)\n#define A52_R179_BANK_FTRACE BIT(1)\n#define A52_R179_BANK_BOTH (A52_R179_BANK_CONSOLE | A52_R179_BANK_FTRACE)\nmissing = A52_R179_BANK_BOTH & ~event.persisted_mask;\nwritten = a52_r179_persist_event(&event, A52_R179_BANK_BOTH);\na52_ackfr_record("BOOT ctl=%s rel=%s cap=%u rs=%u copies=2 crc=0",\na52_ackfr_record("BOOT rs=ready phase=179 roots=%u copies=2 crc=0",\npr_info("phase179 recorder enabled stored=%llu dropped=%llu\\n",\n'''
    patched_recorder = patch_recorder(recorder)
    assert 'A52_R179_BANK_RECORD BIT(2)' in patched_recorder
    assert patched_recorder.count('copies=3') == 2
    assert 'phase=197' in patched_recorder

    ramoops = '''#define A52_ACKFR_BANK_CONSOLE BIT(0)\n#define A52_ACKFR_BANK_FTRACE BIT(1)\nvoid a52_persistent_diag_mark(const char *fmt, ...);\nvoid a52_persistent_diag_mark_ftrace(const char *fmt, ...);\n\tif (targets & A52_ACKFR_BANK_FTRACE) {\n\t\ta52_persistent_diag_mark_ftrace("%.*s", (int)len, buf);\n\t\twritten |= A52_ACKFR_BANK_FTRACE;\n\t}\n\twmb();\n#define A52_DIAG_CONSOLE_PHYS 0xB1B40000ULL\n#define A52_DIAG_CONSOLE_SIZE 0x00040000UL\nstatic u32 a52_diag_ftrace_raw_start;\nstatic u32 a52_diag_ftrace_raw_size;\n/* A52_DIAG_FTRACE_MIRROR */\nvoid a52_persistent_diag_mark_ftrace(const char *fmt, ...)\nstatic int __init a52_persistent_diag_ftrace_init(void)\nreturn a52_persistent_diag_ftrace_init();\nreturn a52_persistent_diag_ftrace_init();\nreturn a52_persistent_diag_ftrace_init();\nint __init a52_persistent_diag_init(void)\n'''
    patched_ramoops = patch_ramoops(ramoops)
    assert 'A52_DIAG_RECORD_PHYS 0xB1B00000ULL' in patched_ramoops
    assert 'a52-early-record' in patched_ramoops
    assert 'A52_ACKFR_BANK_RECORD' in patched_ramoops
    assert patched_ramoops.count('a52_persistent_diag_all_mirrors_init();') == 3

    main = '''extern void a52_persistent_diag_mark(const char *fmt, ...);\nextern void a52_persistent_diag_mark_ftrace(const char *fmt, ...);\nstatic inline void a52_persistent_diag_mark(const char *fmt, ...) { }\nstatic inline void a52_persistent_diag_mark_ftrace(const char *fmt, ...) { }\n\t\ta52_persistent_diag_mark_ftrace(\n\t\t\t"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "\n\t\t\t"metadata_only=1 commit=5a52c0de\\n");\n'''
    patched_main = patch_main(main)
    assert 'a52_persistent_diag_mark_record' in patched_main
    assert patched_main.count('BOOT_EARLY') == 2

    with tempfile.TemporaryDirectory(prefix='a52-r197-selftest-') as td:
        root = Path(td)
        for rel, content in ((RECORDER, recorder), (RAMOOPS, ramoops), (MAIN, main)):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        patch_tree(root)
    print('phase197 triple-copy source patcher self-test: PASS')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.root is None:
        parser.error('--root is required unless --self-test is used')
    patch_tree(args.root)
    print('phase197 triple-copy recorder source patch applied')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
