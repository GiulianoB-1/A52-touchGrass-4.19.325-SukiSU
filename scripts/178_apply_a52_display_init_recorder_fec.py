#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

OLD_PROFILE = "display-bindcore-fec-prz-v2"
NEW_PROFILE = "heap19-display-init-fec-single-map-v2"
RAM_REL = Path("fs/pstore/ram.c")
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
REPORT = "phase36-a52-display-init-fec-report.json"

OLD_DECL = '''static struct persistent_ram_zone *a52_diag_prz[A52_DIAG_BANK_COUNT];
static u8 __iomem *a52_diag_banks[A52_DIAG_BANK_COUNT];
static struct rs_control *a52_diag_rs;
static DEFINE_RAW_SPINLOCK(a52_diag_lock);
'''

NEW_DECL = '''#define A52_DIAG_TOTAL_SIZE \
\t(A52_DIAG_BANK_SIZE * A52_DIAG_BANK_COUNT)

static struct persistent_ram_zone *a52_diag_prz;
static u8 __iomem *a52_diag_banks[A52_DIAG_BANK_COUNT];
static struct rs_control *a52_diag_rs;
static bool a52_diag_preserve_recovery;
static bool a52_diag_first_write_done;
static u32 a52_diag_status;
static DEFINE_RAW_SPINLOCK(a52_diag_lock);
'''

NEW_MAP = r'''static bool __init a52_diag_is_recovery_boot(void)
{
\treturn saved_command_line &&
\t\tstrstr(saved_command_line, "androidboot.boot_recovery=1");
}

#define A52_DIAG_STATUS_MAP_OK BIT(0)
#define A52_DIAG_STATUS_FIRST_WRITE_ENTER BIT(1)
#define A52_DIAG_STATUS_FIRST_WRITE_OK BIT(2)
#define A52_DIAG_STATUS_RS_INIT_ENTER BIT(3)
#define A52_DIAG_STATUS_RS_READY BIT(4)
#define A52_DIAG_STATUS_RS_FAILED BIT(5)
#define A52_DIAG_STATUS_RS_ENCODE_FAILED BIT(6)

static void a52_diag_status_set(u32 bits)
{
\tunsigned int bank;

\ta52_diag_status |= bits;
\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
\t\tif (a52_diag_banks[bank])
\t\t\twritel_relaxed(a52_diag_status,
\t\t\t\t       a52_diag_banks[bank] + 12);
\t}
\twmb();
}

static int a52_diag_map_all_banks(void)
{
\tstruct persistent_ram_ecc_info ecc = { };
\tu8 __iomem *base;
\tu32 flags;
\tunsigned int bank;

\tif (a52_diag_prz && a52_diag_banks[0])
\t\treturn 0;

\t/*
\t * A normal Android boot starts a fresh capture. Recovery must instead
\t * attach without zapping so the failed boot remains available to the raw
\t * exporter. The collector boots recovery before it copies reserved RAM.
\t */
\tflags = a52_diag_preserve_recovery ? 0 : PRZ_FLAG_ZAP_OLD;
\ta52_diag_prz = persistent_ram_new(a52_diag_phys[0],
\t\t\t\t\t A52_DIAG_TOTAL_SIZE, 0, &ecc,
\t\t\t\t\t 1, flags,
\t\t\t\t\t "a52-rec3-all-banks");
\tif (IS_ERR(a52_diag_prz)) {
\t\tint ret = PTR_ERR(a52_diag_prz);

\t\ta52_diag_prz = NULL;
\t\treturn ret;
\t}

\ta52_diag_prz->type = PSTORE_TYPE_DMESG;
\tbase = (u8 __iomem *)a52_diag_prz->vaddr;
\tif (!base)
\t\treturn -ENOMEM;

\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++)
\t\ta52_diag_banks[bank] = base + bank * A52_DIAG_BANK_SIZE;

\tif (a52_diag_preserve_recovery) {
\t\twmb();
\t\treturn 0;
\t}

\tmemset_io(base, 0, A52_DIAG_TOTAL_SIZE);
\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
\t\twritel_relaxed(A52_DIAG_PERSISTENT_RAM_SIG,
\t\t\t       a52_diag_banks[bank]);
\t\twritel_relaxed(0, a52_diag_banks[bank] + 4);
\t\twritel_relaxed(0, a52_diag_banks[bank] + 8);
\t\twritel_relaxed(0, a52_diag_banks[bank] + 12);
\t\ta52_diag_write_super(bank);
\t}
\twmb();
\ta52_diag_status_set(A52_DIAG_STATUS_MAP_OK);
\treturn 0;
}

'''

OLD_INIT = r'''int __init a52_persistent_diag_init(void)
{
\tunsigned int bank;
\tunsigned int mapped = 0;
\tint ret;

\tif (a52_diag_rs)
\t\treturn 0;
\ta52_diag_rs = init_rs(8, 0x11d, 0, 1, A52_ACKFR_PARITY_BYTES);
\tif (!a52_diag_rs)
\t\treturn -ENOMEM;

\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
\t\tret = a52_diag_map_bank(bank);
\t\tif (!ret)
\t\t\tmapped++;
\t\telse
\t\t\tpr_err("A52 recorder PRZ bank %u mapping failed: %d\n",
\t\t\t       bank, ret);
\t}
\tif (mapped < 2) {
\t\tfree_rs(a52_diag_rs);
\t\ta52_diag_rs = NULL;
\t\treturn -ENOMEM;
\t}
\tpr_info("A52 recorder v3 PRZ-mapped %u banks, slots=%u, RS parity=%u\n",
\t\tmapped, A52_DIAG_SLOT_COUNT, A52_ACKFR_PARITY_BYTES);
\treturn 0;
}
'''

NEW_INIT = r'''int __init a52_persistent_diag_init(void)
{
\tint ret;

\tif (a52_diag_banks[0])
\t\treturn 0;

\ta52_diag_preserve_recovery = a52_diag_is_recovery_boot();
\tret = a52_diag_map_all_banks();
\tif (ret) {
\t\tpr_err("A52 recorder contiguous mapping failed: %d\n", ret);
\t\treturn ret;
\t}

\tif (a52_diag_preserve_recovery) {
\t\tpr_info("A52 recorder v3 recovery preserve-only mode; previous boot retained\n");
\t\treturn 0;
\t}

\tpr_info("A52 recorder v3 single-mapped %u banks, slots=%u, RS deferred\n",
\t\tA52_DIAG_BANK_COUNT, A52_DIAG_SLOT_COUNT);
\treturn 0;
}
'''

NEW_WRITE = r'''unsigned int a52_ackfr_ramoops_write_record(const void *data,
\t\t\t\t\t    size_t len, u64 seq)
{
\tstruct a52_ackfr_footer footer;
\tu16 parity[A52_ACKFR_PARITY_BYTES];
\tu8 record[A52_ACKFR_RECORD_BYTES];
\tunsigned long irq_flags;
\tunsigned int bank;
\tunsigned int index;
\tunsigned int slot;
\tunsigned int written = 0;
\tu32 valid_bytes;
\tbool first_write;

\tif (a52_diag_preserve_recovery || !data ||
\t    len != A52_ACKFR_DATA_BYTES || !seq || !a52_diag_banks[0])
\t\treturn 0;

\tfirst_write = !a52_diag_first_write_done;
\tif (first_write)
\t\ta52_diag_status_set(A52_DIAG_STATUS_FIRST_WRITE_ENTER);

\tmemset(record, 0, sizeof(record));
\tmemcpy(record, data, len);
\tmemset(parity, 0, sizeof(parity));
\tif (a52_diag_rs) {
\t\tif (encode_rs8(a52_diag_rs, record, A52_ACKFR_DATA_BYTES,
\t\t\t       parity, 0)) {
\t\t\ta52_diag_status_set(A52_DIAG_STATUS_RS_ENCODE_FAILED);
\t\t} else {
\t\t\tfor (index = 0; index < A52_ACKFR_PARITY_BYTES; index++)
\t\t\t\trecord[A52_ACKFR_DATA_BYTES + index] =
\t\t\t\t\t(u8)parity[index];
\t\t}
\t}

\tfooter.commit = cpu_to_le32(A52_ACKFR_COMMIT);
\tfooter.commit_inv = cpu_to_le32(~A52_ACKFR_COMMIT);
\tfooter.seq_low = cpu_to_le32((u32)seq);
\tfooter.seq_low_inv = cpu_to_le32(~(u32)seq);
\tslot = (unsigned int)((seq - 1) % A52_DIAG_SLOT_COUNT);

\traw_spin_lock_irqsave(&a52_diag_lock, irq_flags);
\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
\t\tu8 __iomem *destination;

\t\tif (!a52_diag_banks[bank])
\t\t\tcontinue;
\t\tdestination = a52_diag_banks[bank] + A52_DIAG_BANK_HEADER +
\t\t\t      slot * A52_DIAG_RECORD_SIZE;
\t\tmemset_io(destination + A52_ACKFR_CODEWORD_BYTES, 0,
\t\t\t  sizeof(footer));
\t\tmemcpy_toio(destination, record, A52_ACKFR_CODEWORD_BYTES);
\t}
\twmb();
\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
\t\tu8 __iomem *destination;

\t\tif (!a52_diag_banks[bank])
\t\t\tcontinue;
\t\tdestination = a52_diag_banks[bank] + A52_DIAG_BANK_HEADER +
\t\t\t      slot * A52_DIAG_RECORD_SIZE;
\t\tmemcpy_toio(destination + A52_ACKFR_CODEWORD_BYTES,
\t\t\t    &footer, sizeof(footer));
\t\twritten |= BIT(bank);
\t}
\twmb();
\tvalid_bytes = min_t(u64, seq, A52_DIAG_SLOT_COUNT) *
\t\t      A52_DIAG_RECORD_SIZE;
\tfor (bank = 0; bank < A52_DIAG_BANK_COUNT; bank++) {
\t\tif (!a52_diag_banks[bank])
\t\t\tcontinue;
\t\twritel_relaxed(((slot + 1) % A52_DIAG_SLOT_COUNT) *
\t\t\t       A52_DIAG_RECORD_SIZE,
\t\t\t       a52_diag_banks[bank] + 4);
\t\twritel_relaxed(valid_bytes, a52_diag_banks[bank] + 8);
\t}
\twmb();
\traw_spin_unlock_irqrestore(&a52_diag_lock, irq_flags);

\tif (first_write && written) {
\t\ta52_diag_first_write_done = true;
\t\ta52_diag_status_set(A52_DIAG_STATUS_FIRST_WRITE_OK);
\t}
\treturn written;
}

int __init a52_ackfr_ramoops_enable_rs(void)
{
\tif (a52_diag_preserve_recovery)
\t\treturn -EROFS;
\tif (!a52_diag_banks[0])
\t\treturn -ENODEV;
\tif (a52_diag_rs)
\t\treturn 0;

\ta52_diag_status_set(A52_DIAG_STATUS_RS_INIT_ENTER);
\ta52_diag_rs = init_rs(8, 0x11d, 0, 1,
\t\t\t      A52_ACKFR_PARITY_BYTES);
\tif (!a52_diag_rs) {
\t\ta52_diag_status_set(A52_DIAG_STATUS_RS_FAILED);
\t\treturn -ENOMEM;
\t}
\ta52_diag_status_set(A52_DIAG_STATUS_RS_READY);
\treturn 0;
}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_function(text: str, signature: str, replacement: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"function signature missing: {signature}")
    brace = text.find("{", start + len(signature))
    if brace < 0:
        raise SystemExit(f"function opening brace missing: {signature}")
    depth = 0
    end = None
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise SystemExit(f"function closing brace missing: {signature}")
    while end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def patch_ram(text: str) -> str:
    if NEW_PROFILE in text and "a52_ackfr_ramoops_enable_rs" in text:
        return text
    if NEW_PROFILE in text:
        raise SystemExit("partial non-blocking RS patch detected in ram.c")
    text = replace_once(
        text,
        "static const char * const a52_diag_labels",
        "static const char * const __maybe_unused a52_diag_labels",
        "bank label declaration",
    )
    text = replace_once(
        text,
        "static const enum pstore_type_id a52_diag_types",
        "static const enum pstore_type_id __maybe_unused a52_diag_types",
        "bank type declaration",
    )
    text = replace_once(text, OLD_DECL, NEW_DECL, "mapping declarations")
    start = text.index("static int a52_diag_map_bank(unsigned int bank)")
    end = text.index("unsigned int a52_ackfr_ramoops_write_record", start)
    map_source = NEW_MAP.replace("\\t", "\t")
    init_source = NEW_INIT.replace("\\t", "\t")
    write_source = NEW_WRITE.replace("\\t", "\t")
    text = text[:start] + map_source + text[end:]
    text = replace_function(
        text, "unsigned int a52_ackfr_ramoops_write_record", write_source
    )
    text = replace_function(
        text, "int __init a52_persistent_diag_init(void)", init_source
    )
    if OLD_PROFILE not in text:
        raise SystemExit("old recorder profile marker missing from ram.c")
    return text.replace(OLD_PROFILE, NEW_PROFILE)


def patch_recorder(text: str) -> str:
    if NEW_PROFILE in text and "a52_ackfr_ramoops_enable_rs" in text:
        return text
    if NEW_PROFILE in text:
        raise SystemExit("partial non-blocking RS patch detected in recorder source")
    if OLD_PROFILE not in text:
        raise SystemExit("old recorder profile marker missing from recorder source")
    declaration = """extern unsigned int a52_ackfr_ramoops_write_record(const void *data,
                                                    size_t len, u64 seq);
"""
    declaration_new = declaration + \
        "extern int __init a52_ackfr_ramoops_enable_rs(void);\n"
    text = replace_once(
        text, declaration, declaration_new, "deferred RS declaration"
    )
    core = r"""static int __init a52_rec3_core(void)
{
\tint rs_ret;

\trs_ret = a52_ackfr_ramoops_enable_rs();
\ta52_ackfr_record("BOOT phase=core rs=%d", rs_ret);
\treturn 0;
}
""".replace("\\t", "\t")
    text = replace_function(text, "static int __init a52_rec3_core(void)", core)
    return text.replace(OLD_PROFILE, NEW_PROFILE)


def run(gki: Path, output: Path) -> dict[str, object]:
    ram = gki / RAM_REL
    recorder = gki / RECORDER_REL
    if not ram.is_file() or not recorder.is_file():
        raise SystemExit("run the recorder-v3 patcher before this single-map patch")
    ram.write_text(patch_ram(ram.read_text()), encoding="utf-8")
    recorder.write_text(patch_recorder(recorder.read_text()), encoding="utf-8")
    final_ram = ram.read_text()
    required = [
        "A52_DIAG_TOTAL_SIZE",
        "a52_diag_map_all_banks",
        "a52-rec3-all-banks",
        "A52 recorder v3 single-mapped %u banks, slots=%u, RS deferred",
        "A52 recorder v3 recovery preserve-only mode",
        "androidboot.boot_recovery=1",
        "a52_diag_preserve_recovery || !data",
        "a52_ackfr_ramoops_enable_rs",
        "A52_DIAG_STATUS_FIRST_WRITE_OK",
        "A52_DIAG_STATUS_RS_INIT_ENTER",
        "flags = a52_diag_preserve_recovery ? 0 : PRZ_FLAG_ZAP_OLD",
        NEW_PROFILE,
        "A52_ACKFR_PARITY_BYTES 32U",
        "A52_DIAG_BANK_COUNT 3U",
        "__maybe_unused a52_diag_labels",
        "__maybe_unused a52_diag_types",
    ]
    for marker in required:
        if marker not in final_ram:
            raise SystemExit(f"post-patch marker missing: {marker}")
    if "a52_diag_map_bank(unsigned int bank)" in final_ram:
        raise SystemExit("legacy per-bank mapping function remains")
    if "\n\\t" in final_ram:
        raise SystemExit("literal backslash-t indentation remains in ram.c")
    report = {
        "status": "a52-display-init-recorder-fec-staged",
        "hardware_validated": False,
        "persistent_profile": NEW_PROFILE,
        "mapping_backend": "one-persistent_ram_new-vmap-three-fixed-banks",
        "recovery_policy": "attach-preserve-disable-writes-on-androidboot.boot_recovery=1",
        "record_bytes": 256,
        "protected_data_bytes": 208,
        "reed_solomon_parity_bytes": 32,
        "copies": 3,
        "physical_bank_spacing_bytes": 262144,
        "capture_finding": (
            "capture 20260801_001830 preserved initialized headers but every "
            "record slot remained zero; early writes no longer depend on RS, "
            "RS initialization is deferred to core init, and header status "
            "bits distinguish mapping, first-write, and RS stages"
        ),
        "write_policy": "triple-copy-crc-commit-never-blocked-by-rs",
        "rs_policy": "deferred-core-init-zero-parity-fallback",
        "files": [str(RAM_REL), str(RECORDER_REL)],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT).write_text(json.dumps(report, indent=2) + "\n")
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-single-map-") as td:
        root = Path(td)
        ram = root / RAM_REL
        rec = root / RECORDER_REL
        ram.parent.mkdir(parents=True)
        rec.parent.mkdir(parents=True)
        old_arrays = '''static const char * const a52_diag_labels[3] = { "a", "b", "c" };
static const enum pstore_type_id a52_diag_types[3] = { 0, 1, 2 };
'''
        old_map = '''static int a52_diag_map_bank(unsigned int bank)\n{\n\treturn bank;\n}\n\n'''
        old_write = '''unsigned int a52_ackfr_ramoops_write_record(const void *data, size_t len, u64 seq)
{
\tif (!data || len != A52_ACKFR_DATA_BYTES || !seq || !a52_diag_rs)
\t\treturn 0;
\treturn 7;
}
'''
        ram.write_text(
            old_arrays + OLD_DECL + old_map + old_write +
            OLD_INIT + f'const char *profile = "{OLD_PROFILE}";\n' +
            '#define A52_ACKFR_PARITY_BYTES 32U\n#define A52_DIAG_BANK_COUNT 3U\n'
        )
        rec.write_text(
            '#include <linux/init.h>\n'
            'extern unsigned int a52_ackfr_ramoops_write_record(const void *data,\n'
            '                                                    size_t len, u64 seq);\n'
            'static int __init a52_rec3_core(void)\n{\n'
            '\ta52_ackfr_record("BOOT phase=core");\n'
            '\treturn 0;\n}\n'
            f'#define A52_REC3_PROFILE "{OLD_PROFILE}"\n'
        )
        first = run(root, root / "out")
        assert first["copies"] == 3
        patched = ram.read_text()
        assert "\n\\t" not in patched
        assert "\n\tstruct persistent_ram_ecc_info" in patched
        assert "__maybe_unused a52_diag_labels" in patched
        assert "__maybe_unused a52_diag_types" in patched
        assert "androidboot.boot_recovery=1" in patched
        assert "a52_diag_preserve_recovery || !data" in patched
        assert "a52_ackfr_ramoops_enable_rs" in patched
        assert "A52_DIAG_STATUS_FIRST_WRITE_OK" in patched
        assert "!seq || !a52_diag_banks[0]" in patched
        assert "!seq || !a52_diag_rs" not in patched
        assert "a52_diag_preserve_recovery ? 0 : PRZ_FLAG_ZAP_OLD" in patched
        patched_rec = rec.read_text()
        assert "BOOT phase=core rs=%d" in patched_rec
        assert "a52_ackfr_ramoops_enable_rs" in patched_rec
        second = run(root, root / "out2")
        assert second["recovery_policy"].startswith("attach-preserve")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gki", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"status": "self-test-passed"}))
        return 0
    if args.gki is None or args.output is None:
        ap.error("--gki and --output are required")
    print(json.dumps(run(args.gki.resolve(), args.output.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
