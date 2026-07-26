#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

MAIN_REL = Path("init/main.c")
RAMOOPS_REL = Path("fs/pstore/ram.c")
GENERATOR_NAME = "140_apply_a52xq_unified_secure_startup_recorder.py"
DECODER_NAME = "140_decode_a52xq_unified_secure_recorder.py"
REPORT = "phase19-ack-early-mirrored-boot-probe-report.json"
EARLY_MARKER = "A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored"

BACKEND_PATTERN = re.compile(
    r"unsigned int a52_ackfr_ramoops_write\(const char \*buf, size_t len,\n"
    r"[\s\S]*?EXPORT_SYMBOL_GPL\(a52_ackfr_ramoops_write\);"
)
BACKEND_NEW = r'''/* A52_ACKFR_EARLY_MIRRORED_BACKEND */
void a52_persistent_diag_mark(const char *fmt, ...);
void a52_persistent_diag_mark_ftrace(const char *fmt, ...);

unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,
                                     unsigned int targets)
{
\tunsigned int written = 0;

\tif (!buf || !len)
\t\treturn 0;
\tif (len > A52_ACKFR_RAMOOPS_MAX_WRITE)
\t\tlen = A52_ACKFR_RAMOOPS_MAX_WRITE;
\tif (targets & A52_ACKFR_BANK_CONSOLE) {
\t\ta52_persistent_diag_mark("%.*s", (int)len, buf);
\t\twritten |= A52_ACKFR_BANK_CONSOLE;
\t}
\tif (targets & A52_ACKFR_BANK_FTRACE) {
\t\ta52_persistent_diag_mark_ftrace("%.*s", (int)len, buf);
\t\twritten |= A52_ACKFR_BANK_FTRACE;
\t}
\twmb();
\treturn written;
}
EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write);'''

RECORDER_TAIL_OLD = r'''EXPORT_SYMBOL_GPL(a52_ackfr_record);

static int __init a52_usr2_device_retry(void)
{
\ta52_usr2_flush_missing((u64)atomic64_read(&a52_usr2_sequence));
\treturn 0;
}
device_initcall(a52_usr2_device_retry);

static int __init a52_usr2_late_retry(void)
{
\tu64 stored = (u64)atomic64_read(&a52_usr2_sequence);

\ta52_usr2_flush_missing(stored);
\ta52_usr2_write_control("BOOT_READY");
\tpr_info("unified secure recorder enabled stored=%llu dropped=%llu\n",
\t\t(unsigned long long)min_t(u64, stored, A52_USR2_CAPACITY),
\t\t(unsigned long long)atomic64_read(&a52_usr2_dropped));
\treturn 0;
}
late_initcall(a52_usr2_late_retry);'''

RECORDER_TAIL_NEW = r'''EXPORT_SYMBOL_GPL(a52_ackfr_record);

/* A52_ACKFR_BOOT_PHASE_HEARTBEATS */
static int __init a52_usr2_early_heartbeat(void)
{
\ta52_ackfr_record("BOOT phase=pre_smp");
\treturn 0;
}
early_initcall(a52_usr2_early_heartbeat);

static int __init a52_usr2_pure_heartbeat(void)
{
\ta52_ackfr_record("BOOT phase=pure");
\treturn 0;
}
pure_initcall(a52_usr2_pure_heartbeat);

static int __init a52_usr2_core_heartbeat(void)
{
\ta52_ackfr_record("BOOT phase=core");
\treturn 0;
}
core_initcall(a52_usr2_core_heartbeat);

static int __init a52_usr2_postcore_heartbeat(void)
{
\ta52_ackfr_record("BOOT phase=postcore");
\treturn 0;
}
postcore_initcall(a52_usr2_postcore_heartbeat);

static int __init a52_usr2_arch_heartbeat(void)
{
\ta52_ackfr_record("BOOT phase=arch");
\treturn 0;
}
arch_initcall(a52_usr2_arch_heartbeat);

static int __init a52_usr2_subsys_heartbeat(void)
{
\ta52_ackfr_record("BOOT phase=subsys");
\treturn 0;
}
subsys_initcall(a52_usr2_subsys_heartbeat);

static int __init a52_usr2_fs_heartbeat(void)
{
\ta52_ackfr_record("BOOT phase=fs");
\treturn 0;
}
fs_initcall(a52_usr2_fs_heartbeat);

static int __init a52_usr2_device_retry(void)
{
\ta52_ackfr_record("BOOT phase=device");
\ta52_usr2_flush_missing((u64)atomic64_read(&a52_usr2_sequence));
\treturn 0;
}
device_initcall(a52_usr2_device_retry);

static int __init a52_usr2_late_retry(void)
{
\tu64 stored;

\ta52_ackfr_record("BOOT phase=late");
\tstored = (u64)atomic64_read(&a52_usr2_sequence);
\ta52_usr2_flush_missing(stored);
\ta52_usr2_write_control("BOOT_READY");
\tpr_info("unified secure recorder enabled stored=%llu dropped=%llu\n",
\t\t(unsigned long long)min_t(u64, stored, A52_USR2_CAPACITY),
\t\t(unsigned long long)atomic64_read(&a52_usr2_dropped));
\treturn 0;
}
late_initcall(a52_usr2_late_retry);'''

RAW_FTRACE_BLOCK = r'''/* A52_DIAG_FTRACE_MIRROR */
static void a52_diag_ftrace_raw_write(const char *s, unsigned int count)
{
\tunsigned int first;

\tif (!a52_diag_ftrace_raw || !count)
\t\treturn;
\tif (count > A52_DIAG_DATA_SIZE) {
\t\ts += count - A52_DIAG_DATA_SIZE;
\t\tcount = A52_DIAG_DATA_SIZE;
\t}
\tfirst = min_t(unsigned int, count,
\t\t      A52_DIAG_DATA_SIZE - a52_diag_ftrace_raw_start);
\tmemcpy_toio(a52_diag_ftrace_raw + A52_DIAG_HEADER_SIZE +
\t\t    a52_diag_ftrace_raw_start, s, first);
\tif (count > first)
\t\tmemcpy_toio(a52_diag_ftrace_raw + A52_DIAG_HEADER_SIZE,
\t\t\t    s + first, count - first);
\ta52_diag_ftrace_raw_start += count;
\twhile (a52_diag_ftrace_raw_start >= A52_DIAG_DATA_SIZE)
\t\ta52_diag_ftrace_raw_start -= A52_DIAG_DATA_SIZE;
\ta52_diag_ftrace_raw_size = min_t(u32, A52_DIAG_DATA_SIZE,
\t\t\t\t       a52_diag_ftrace_raw_size + count);
\twmb();
\twritel_relaxed(a52_diag_ftrace_raw_start, a52_diag_ftrace_raw + 4);
\twritel_relaxed(a52_diag_ftrace_raw_size, a52_diag_ftrace_raw + 8);
\twmb();
}

'''

FTRACE_API_BLOCK = r'''void a52_persistent_diag_mark_ftrace(const char *fmt, ...)
{
\tchar line[A52_DIAG_LINE_SIZE];
\tva_list args;
\tint len;

\tif (IS_ERR_OR_NULL(a52_diag_ftrace_prz) && !a52_diag_ftrace_raw)
\t\treturn;
\tva_start(args, fmt);
\tlen = vscnprintf(line, sizeof(line), fmt, args);
\tva_end(args);
\tif (len <= 0)
\t\treturn;
\tif (a52_diag_ftrace_prz)
\t\tpersistent_ram_write(a52_diag_ftrace_prz, line, len);
\telse
\t\ta52_diag_ftrace_raw_write(line, len);
\twmb();
}

static int __init a52_persistent_diag_ftrace_init(void)
{
\tstruct persistent_ram_ecc_info ecc = { };
\tint prz_ret;

\tif (a52_diag_ftrace_prz || a52_diag_ftrace_raw)
\t\treturn 0;
\ta52_diag_ftrace_prz = persistent_ram_new(A52_DIAG_FTRACE_PHYS,
\t\t\t\t\t  A52_DIAG_FTRACE_SIZE, 0, &ecc,
\t\t\t\t\t  1, PRZ_FLAG_ZAP_OLD,
\t\t\t\t\t  "a52-early-ftrace");
\tif (!IS_ERR(a52_diag_ftrace_prz)) {
\t\ta52_diag_ftrace_prz->type = PSTORE_TYPE_FTRACE;
\t\treturn 0;
\t}
\tprz_ret = PTR_ERR(a52_diag_ftrace_prz);
\ta52_diag_ftrace_prz = NULL;
\ta52_diag_ftrace_raw = ioremap_wc(A52_DIAG_FTRACE_PHYS,
\t\t\t\t\t  A52_DIAG_FTRACE_SIZE);
\tif (!a52_diag_ftrace_raw)
\t\ta52_diag_ftrace_raw = ioremap(A52_DIAG_FTRACE_PHYS,
\t\t\t\t\t      A52_DIAG_FTRACE_SIZE);
\tif (!a52_diag_ftrace_raw)
\t\treturn prz_ret ? prz_ret : -ENOMEM;
\tmemset_io(a52_diag_ftrace_raw, 0, A52_DIAG_FTRACE_SIZE);
\twritel_relaxed(A52_DIAG_PERSISTENT_RAM_SIG, a52_diag_ftrace_raw);
\twritel_relaxed(0, a52_diag_ftrace_raw + 4);
\twritel_relaxed(0, a52_diag_ftrace_raw + 8);
\ta52_diag_ftrace_raw_start = 0;
\ta52_diag_ftrace_raw_size = 0;
\twmb();
\treturn 0;
}

'''

MAIN_INIT_RE = re.compile(
    r"int __init a52_persistent_diag_init\(void\)\n\{[\s\S]*?\n\}\n\n"
    r"static int __init __maybe_unused ramoops_init"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, marker: str, label: str) -> tuple[str, str]:
    if marker in text:
        if text.count(marker) != 1:
            raise SystemExit(f"{label}: staged marker count is not one")
        return text, "already-present"
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1), "inserted"


def patch_ramoops_early_mirror(path: Path) -> dict[str, object]:
    text = read(path)
    if "A52_DIAG_FTRACE_MIRROR" in text:
        return {"source": str(RAMOOPS_REL), "state": "already-present"}

    text, constants_state = replace_once(
        text,
        "#define A52_DIAG_CONSOLE_SIZE 0x00040000UL\n",
        "#define A52_DIAG_CONSOLE_SIZE 0x00040000UL\n"
        "#define A52_DIAG_FTRACE_PHYS 0xB1B80000ULL\n"
        "#define A52_DIAG_FTRACE_SIZE 0x00040000UL\n",
        "A52_DIAG_FTRACE_PHYS",
        "early ftrace constants",
    )
    text, vars_state = replace_once(
        text,
        "static u32 a52_diag_raw_size;\n",
        "static u32 a52_diag_raw_size;\n"
        "static struct persistent_ram_zone *a52_diag_ftrace_prz;\n"
        "static u8 __iomem *a52_diag_ftrace_raw;\n"
        "static u32 a52_diag_ftrace_raw_start;\n"
        "static u32 a52_diag_ftrace_raw_size;\n",
        "a52_diag_ftrace_raw_size",
        "early ftrace state",
    )
    text, raw_state = replace_once(
        text,
        "void a52_persistent_diag_mark(const char *fmt, ...)\n",
        RAW_FTRACE_BLOCK + "void a52_persistent_diag_mark(const char *fmt, ...)\n",
        "A52_DIAG_FTRACE_MIRROR",
        "early ftrace raw writer",
    )
    text, api_state = replace_once(
        text,
        "int __init a52_persistent_diag_init(void)\n",
        FTRACE_API_BLOCK + "int __init a52_persistent_diag_init(void)\n",
        "a52_persistent_diag_ftrace_init",
        "early ftrace API",
    )

    matches = list(MAIN_INIT_RE.finditer(text))
    if len(matches) != 1:
        raise SystemExit(
            f"early diagnostic init anchor mismatch: expected 1, found {len(matches)}"
        )
    match = matches[0]
    function_and_next = match.group(0)
    function = function_and_next[: function_and_next.rfind("\n\nstatic int")]
    suffix = function_and_next[len(function) :]
    count = function.count("return 0;")
    if count != 3:
        raise SystemExit(
            f"early diagnostic init return count mismatch: expected 3, found {count}"
        )
    function = function.replace(
        "return 0;", "return a52_persistent_diag_ftrace_init();"
    )
    text = text[: match.start()] + function + suffix + text[match.end() :]

    required = (
        "A52_DIAG_FTRACE_MIRROR",
        "A52_DIAG_FTRACE_PHYS 0xB1B80000ULL",
        "a52_persistent_diag_mark_ftrace",
        "a52_persistent_diag_ftrace_init",
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit("early ftrace mirror audit failed: " + ", ".join(missing))
    write(path, text)
    return {
        "source": str(RAMOOPS_REL),
        "state": "inserted",
        "constants": constants_state,
        "variables": vars_state,
        "raw_writer": raw_state,
        "api": api_state,
    }


def patch_main(path: Path) -> dict[str, object]:
    text = read(path)
    if EARLY_MARKER in text:
        return {"source": str(MAIN_REL), "state": "already-present"}

    text, extern_state = replace_once(
        text,
        "extern void a52_persistent_diag_mark(const char *fmt, ...);\n",
        "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
        "extern void a52_persistent_diag_mark_ftrace(const char *fmt, ...);\n",
        "extern void a52_persistent_diag_mark_ftrace",
        "early ftrace declaration",
    )
    text, stub_state = replace_once(
        text,
        "static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n",
        "static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n"
        "static inline void a52_persistent_diag_mark_ftrace(const char *fmt, ...) { }\n",
        "inline void a52_persistent_diag_mark_ftrace",
        "early ftrace stub",
    )
    old = '''\tif (a52_persistent_diag_init())
\t\tpr_err("A52 early persistent diagnostic initialization failed\\n");
\telse
\t\tdo { } while (0);
'''
    new = '''\tif (a52_persistent_diag_init())
\t\tpr_err("A52 early persistent diagnostic initialization failed\\n");
\telse {
\t\ta52_persistent_diag_mark(
\t\t\t"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "
\t\t\t"metadata_only=1 commit=5a52c0de\\n");
\t\ta52_persistent_diag_mark_ftrace(
\t\t\t"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "
\t\t\t"metadata_only=1 commit=5a52c0de\\n");
\t}
'''
    text, marker_state = replace_once(
        text, old, new, EARLY_MARKER, "mm_init boot marker"
    )
    write(path, text)
    return {
        "source": str(MAIN_REL),
        "state": marker_state,
        "extern": extern_state,
        "stub": stub_state,
    }


def patch_generator(path: Path) -> dict[str, object]:
    text = read(path)
    if "A52_ACKFR_EARLY_MIRRORED_BACKEND" in text:
        backend_state = "already-present"
    else:
        matches = list(BACKEND_PATTERN.finditer(text))
        if len(matches) != 1:
            raise SystemExit(
                f"unified backend anchor mismatch: expected 1, found {len(matches)}"
            )
        match = matches[0]
        text = text[: match.start()] + BACKEND_NEW + text[match.end() :]
        backend_state = "inserted"

    text, heartbeat_state = replace_once(
        text,
        RECORDER_TAIL_OLD,
        RECORDER_TAIL_NEW,
        "/* A52_ACKFR_BOOT_PHASE_HEARTBEATS */",
        "boot phase heartbeats",
    )
    text, audit_state = replace_once(
        text,
        '        "direct_writer_exported": "EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write);" in text,\n',
        '        "direct_writer_exported": "EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write);" in text,\n'
        '        "early_console_backend": "a52_persistent_diag_mark(\\\"%.*s\\\"" in text,\n'
        '        "early_ftrace_backend": "a52_persistent_diag_mark_ftrace(\\\"%.*s\\\"" in text,\n',
        '"early_ftrace_backend"',
        "unified backend audit",
    )
    required = (
        "A52_ACKFR_EARLY_MIRRORED_BACKEND",
        "A52_ACKFR_BOOT_PHASE_HEARTBEATS",
        '"early_console_backend"',
        '"early_ftrace_backend"',
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit("unified generator mirror audit failed: " + ", ".join(missing))
    write(path, text)
    return {
        "source": path.name,
        "backend": backend_state,
        "heartbeats": heartbeat_state,
        "audit": audit_state,
    }


def patch_decoder(path: Path) -> dict[str, object]:
    text = read(path)
    old = "BOOT_BEGIN|BOOT_READY"
    new = "BOOT_EARLY|BOOT_BEGIN|BOOT_READY"
    if new in text:
        state = "already-present"
    else:
        count = text.count(old)
        if count != 1:
            raise SystemExit(
                f"decoder control anchor mismatch: expected 1, found {count}"
            )
        text = text.replace(old, new, 1)
        state = "inserted"
    write(path, text)
    return {"source": path.name, "state": state}


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ram = root / RAMOOPS_REL
        main = root / MAIN_REL
        gen = root / GENERATOR_NAME
        dec = root / DECODER_NAME
        ram.parent.mkdir(parents=True, exist_ok=True)
        main.parent.mkdir(parents=True, exist_ok=True)
        ram.write_text(
            '''#define A52_DIAG_CONSOLE_SIZE 0x00040000UL
#define A52_DIAG_HEADER_SIZE 12U
#define A52_DIAG_DATA_SIZE (A52_DIAG_CONSOLE_SIZE - A52_DIAG_HEADER_SIZE)
#define A52_DIAG_PERSISTENT_RAM_SIG 0x43474244U
#define A52_DIAG_LINE_SIZE 256
static struct persistent_ram_zone *a52_diag_prz;
static u8 __iomem *a52_diag_raw;
static u32 a52_diag_raw_start;
static u32 a52_diag_raw_size;
static void a52_diag_raw_write(const char *s, unsigned int count) { }
void a52_persistent_diag_mark(const char *fmt, ...)
{
}
int __init a52_persistent_diag_init(void)
{
\tif (a52_diag_prz || a52_diag_raw)
\t\treturn 0;
\tif (!IS_ERR(a52_diag_prz)) {
\t\ta52_diag_prz->type = PSTORE_TYPE_CONSOLE;
\t\treturn 0;
\t}
\treturn 0;
}

static int __init __maybe_unused ramoops_init(void)
{
\treturn 0;
}
''',
            encoding="utf-8",
        )
        main.write_text(
            '''extern void a52_persistent_diag_mark(const char *fmt, ...);
static inline void a52_persistent_diag_mark(const char *fmt, ...) { }
\tif (a52_persistent_diag_init())
\t\tpr_err("A52 early persistent diagnostic initialization failed\\n");
\telse
\t\tdo { } while (0);
''',
            encoding="utf-8",
        )
        backend_old = r'''unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,
                                     unsigned int targets)
{
\treturn 0;
}
EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write);'''
        gen.write_text(
            "SOURCE = r'''\n" + RECORDER_TAIL_OLD + "\n'''\n"
            + "writer = r'''\n" + backend_old + "\n'''\n"
            + "checks = {\n"
            + '        "direct_writer_exported": "EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write);" in text,\n'
            + "}\n",
            encoding="utf-8",
        )
        dec.write_text(
            'rb"A52USR2 (?P<kind>BOOT_BEGIN|BOOT_READY) "\n',
            encoding="utf-8",
        )
        first = (
            patch_ramoops_early_mirror(ram),
            patch_main(main),
            patch_generator(gen),
            patch_decoder(dec),
        )
        second = (
            patch_ramoops_early_mirror(ram),
            patch_main(main),
            patch_generator(gen),
            patch_decoder(dec),
        )
        if any(item["state"] != "inserted" for item in (first[0], first[1], first[3])):
            raise SystemExit("early mirrored probe self-test did not insert")
        if first[2]["backend"] != "inserted" or first[2]["heartbeats"] != "inserted":
            raise SystemExit("generator mirror self-test did not insert")
        if any(item["state"] != "already-present" for item in (second[0], second[1], second[3])):
            raise SystemExit("early mirrored probe is not idempotent")
        if second[2]["backend"] != "already-present" or second[2]["heartbeats"] != "already-present":
            raise SystemExit("generator mirror patch is not idempotent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    required = (root / MAIN_REL, root / RAMOOPS_REL)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("missing early mirrored probe sources: " + ", ".join(missing))

    generator = Path(__file__).with_name(GENERATOR_NAME)
    decoder = Path(__file__).with_name(DECODER_NAME)
    report = {
        "status": "ack-early-mirrored-boot-probe-142-staged",
        "hardware_validated": False,
        "payload_capture": False,
        "mirroring": True,
        "banks": ["ramoops-console", "ramoops-ftrace"],
        "early_marker": EARLY_MARKER,
        "ramoops": patch_ramoops_early_mirror(root / RAMOOPS_REL),
        "main": patch_main(root / MAIN_REL),
        "generator": patch_generator(generator),
        "decoder": patch_decoder(decoder),
        "boot_phases": [
            "mm_init",
            "pre_smp",
            "pure",
            "core",
            "postcore",
            "arch",
            "subsys",
            "fs",
            "device",
            "late",
        ],
        "scope": (
            "prove the 5.10 kernel reaches mm_init and identify the final completed "
            "initcall level before secure-driver metadata begins"
        ),
    }
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
