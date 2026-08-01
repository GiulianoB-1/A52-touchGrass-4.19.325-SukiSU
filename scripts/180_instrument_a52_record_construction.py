#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

PATCHER = Path("scripts/178_apply_a52_display_init_recorder_fec.py")
CI = Path("scripts/178_ci_display_init_fec.sh")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_ci(text: str) -> str:
    text = replace_once(
        text,
        'make -k -C gki/common O="$BUILD"',
        'make -C gki/common O="$BUILD"',
        "fail-fast kernel make",
    )
    marker = "grep -Fxq 'CONFIG_REED_SOLOMON_DEC8=y' \"$BUILD/.config\"\n\nset +e\n"
    preflight = """grep -Fxq 'CONFIG_REED_SOLOMON_DEC8=y' "$BUILD/.config"

make -C gki/common O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \\
  KCFLAGS=-Wno-error=frame-larger-than \\
  fs/pstore/ram.o \\
  drivers/a52_secure/a52_ack_secure_flight_recorder.o \\
  > "$OUT/logs/recorder-object-preflight.log" 2>&1
grep -Fq 'CC      fs/pstore/ram.o' \\
  "$OUT/logs/recorder-object-preflight.log"
grep -Fq 'CC      drivers/a52_secure/a52_ack_secure_flight_recorder.o' \\
  "$OUT/logs/recorder-object-preflight.log"

set +e
"""
    text = replace_once(text, marker, preflight, "object preflight insertion")
    old_audit = '  grep -Fq "$object" "$OUT/logs/compile.log"\ndone\n'
    new_audit = '''  if ! grep -Fq "$object" "$OUT/logs/compile.log"; then
  grep -Fq "$object" "$OUT/logs/recorder-object-preflight.log"
fi
done
'''
    return replace_once(text, old_audit, new_audit, "object audit fallback")


def patch_patcher(patcher: str) -> str:
    start_marker = (
        '    declaration = """extern unsigned int '
        'a52_ackfr_ramoops_write_record(const void *data,'
    )
    start = patcher.find(start_marker)
    end = patcher.find('    core = r"""', start)
    if start < 0 or end < 0 or end <= start:
        raise SystemExit("fragile deferred RS declaration block not found")
    robust = (
        '    declaration_start = text.find(\n'
        '        "extern unsigned int a52_ackfr_ramoops_write_record(const void *data,"\n'
        '    )\n'
        '    if declaration_start < 0:\n'
        '        raise SystemExit("deferred RS declaration start missing")\n'
        '    declaration_end = text.find(";", declaration_start)\n'
        '    if declaration_end < 0:\n'
        '        raise SystemExit("deferred RS declaration terminator missing")\n'
        '    declaration_end += 1\n'
        '    text = (\n'
        '        text[:declaration_end]\n'
        '        + "\\nextern int __init a52_ackfr_ramoops_enable_rs(void);"\n'
        '        + "\\nextern void a52_ackfr_ramoops_mark_status(u32 bits);"\n'
        '        + text[declaration_end:]\n'
        '    )\n'
    )
    patcher = patcher[:start] + robust + patcher[end:]

    old_writer_matcher = (
        '        text, "unsigned int a52_ackfr_ramoops_write_record", '
        'write_source\n'
    )
    new_writer_matcher = (
        '        text, "unsigned int a52_ackfr_ramoops_write_record'
        '(const void *data, size_t len,", write_source\n'
    )
    patcher = replace_once(
        patcher,
        old_writer_matcher,
        new_writer_matcher,
        "specific writer definition matcher",
    )

    old_status = """#define A52_DIAG_STATUS_MAP_OK BIT(0)
#define A52_DIAG_STATUS_FIRST_WRITE_ENTER BIT(1)
#define A52_DIAG_STATUS_FIRST_WRITE_OK BIT(2)
#define A52_DIAG_STATUS_RS_INIT_ENTER BIT(3)
#define A52_DIAG_STATUS_RS_READY BIT(4)
#define A52_DIAG_STATUS_RS_FAILED BIT(5)
#define A52_DIAG_STATUS_RS_ENCODE_FAILED BIT(6)
"""
    new_status = """#define A52_DIAG_STATUS_MAP_OK BIT(0)
#define A52_DIAG_STATUS_INIT_AFTER_MAP BIT(1)
#define A52_DIAG_STATUS_INIT_AFTER_PRINT1 BIT(2)
#define A52_DIAG_STATUS_INIT_AFTER_PRINT2 BIT(3)
#define A52_DIAG_STATUS_INIT_RETURN BIT(4)
#define A52_DIAG_STATUS_RECORD_ENTER BIT(5)
#define A52_DIAG_STATUS_RECORD_CLASSIFIED BIT(6)
#define A52_DIAG_STATUS_RECORD_SEQ_OK BIT(7)
#define A52_DIAG_STATUS_RECORD_TIME_OK BIT(8)
#define A52_DIAG_STATUS_RECORD_LEN_OK BIT(9)
#define A52_DIAG_STATUS_RECORD_ZEROED BIT(10)
#define A52_DIAG_STATUS_RECORD_HEADER_OK BIT(11)
#define A52_DIAG_STATUS_RECORD_CURRENT_OK BIT(12)
#define A52_DIAG_STATUS_RECORD_CPU_OK BIT(13)
#define A52_DIAG_STATUS_RECORD_COMM_OK BIT(14)
#define A52_DIAG_STATUS_RECORD_MESSAGE_OK BIT(15)
#define A52_DIAG_STATUS_RECORD_CRC_OK BIT(16)
#define A52_DIAG_STATUS_RECORD_PREPARED_OK BIT(17)
#define A52_DIAG_STATUS_RECORD_BEFORE_WRITER BIT(18)
#define A52_DIAG_STATUS_FIRST_WRITE_ENTER BIT(19)
#define A52_DIAG_STATUS_FIRST_WRITE_OK BIT(20)
#define A52_DIAG_STATUS_RS_INIT_ENTER BIT(21)
#define A52_DIAG_STATUS_RS_READY BIT(22)
#define A52_DIAG_STATUS_RS_FAILED BIT(23)
#define A52_DIAG_STATUS_RS_ENCODE_FAILED BIT(24)
"""
    patcher = replace_once(patcher, old_status, new_status, "construction status map")

    map_marker = "static int a52_diag_map_all_banks(void)\n"
    mark_function = r'''void a52_ackfr_ramoops_mark_status(u32 bits)
{
\tif (!a52_diag_preserve_recovery)
\t\ta52_diag_status_set(bits);
}

'''
    patcher = replace_once(
        patcher,
        map_marker,
        mark_function + map_marker,
        "status marker function",
    )

    old_init = r'''\tpr_info("A52 recorder v3 single-mapped %u banks, slots=%u, RS parity=%u\n",
\t\tA52_DIAG_BANK_COUNT, A52_DIAG_SLOT_COUNT,
\t\tA52_ACKFR_PARITY_BYTES);
\tpr_info("A52 recorder v3 RS initialization deferred; zero-parity fallback active\n");
\treturn 0;
'''
    new_init = r'''\ta52_diag_status_set(A52_DIAG_STATUS_INIT_AFTER_MAP);
\tpr_info("A52 recorder v3 single-mapped %u banks, slots=%u, RS parity=%u\n",
\t\tA52_DIAG_BANK_COUNT, A52_DIAG_SLOT_COUNT,
\t\tA52_ACKFR_PARITY_BYTES);
\ta52_diag_status_set(A52_DIAG_STATUS_INIT_AFTER_PRINT1);
\tpr_info("A52 recorder v3 RS initialization deferred; zero-parity fallback active\n");
\ta52_diag_status_set(A52_DIAG_STATUS_INIT_AFTER_PRINT2);
\ta52_diag_status_set(A52_DIAG_STATUS_INIT_RETURN);
\treturn 0;
'''
    patcher = replace_once(patcher, old_init, new_init, "normal-init checkpoints")

    guard_marker = r'''\tif (a52_diag_preserve_recovery || !data ||
'''
    guard_prefix = r'''\tif (!a52_diag_preserve_recovery)
\t\ta52_diag_status_set(A52_DIAG_STATUS_FIRST_WRITE_ENTER);
'''
    patcher = replace_once(
        patcher,
        guard_marker,
        guard_prefix + guard_marker,
        "writer-entry checkpoint",
    )

    core_marker = '    core = r"""static int __init a52_rec3_core(void)\n'
    recorder_instrument = '''    if "#define A52_REC3_FLAG_CRITICAL BIT(0)\\n" in text:
        text = replace_once(
            text,
            "#define A52_REC3_FLAG_CRITICAL BIT(0)\\n",
            "#define A52_REC3_FLAG_CRITICAL BIT(0)\\n"
            "#define A52_REC3_STATUS_RECORD_ENTER BIT(5)\\n"
            "#define A52_REC3_STATUS_RECORD_CLASSIFIED BIT(6)\\n"
            "#define A52_REC3_STATUS_RECORD_SEQ_OK BIT(7)\\n"
            "#define A52_REC3_STATUS_RECORD_TIME_OK BIT(8)\\n"
            "#define A52_REC3_STATUS_RECORD_LEN_OK BIT(9)\\n"
            "#define A52_REC3_STATUS_RECORD_ZEROED BIT(10)\\n"
            "#define A52_REC3_STATUS_RECORD_HEADER_OK BIT(11)\\n"
            "#define A52_REC3_STATUS_RECORD_CURRENT_OK BIT(12)\\n"
            "#define A52_REC3_STATUS_RECORD_CPU_OK BIT(13)\\n"
            "#define A52_REC3_STATUS_RECORD_COMM_OK BIT(14)\\n"
            "#define A52_REC3_STATUS_RECORD_MESSAGE_OK BIT(15)\\n"
            "#define A52_REC3_STATUS_RECORD_CRC_OK BIT(16)\\n"
            "#define A52_REC3_STATUS_RECORD_PREPARED_OK BIT(17)\\n"
            "#define A52_REC3_STATUS_RECORD_BEFORE_WRITER BIT(18)\\n",
            "record-construction checkpoint definitions",
        )
        text = replace_once(
            text,
            "\\tBUILD_BUG_ON(sizeof(struct a52_rec3_data) != 208);\\n",
            "\\tBUILD_BUG_ON(sizeof(struct a52_rec3_data) != 208);\\n"
            "\\ta52_ackfr_ramoops_mark_status(A52_REC3_STATUS_RECORD_ENTER);\\n",
            "recorder entry checkpoint",
        )
        classified_old = (
            "\\tevent_id = a52_rec3_event_id(message);\\n"
            "\\tif (!event_id)\\n"
            "\\t\\treturn;\\n"
        )
        text = replace_once(
            text,
            classified_old,
            classified_old + "\\ta52_ackfr_ramoops_mark_status(A52_REC3_STATUS_RECORD_CLASSIFIED);\\n",
            "classification checkpoint",
        )
        checkpoints = [
            ("\\tseq = (u64)atomic64_inc_return(&a52_rec3_sequence);\\n",
             "A52_REC3_STATUS_RECORD_SEQ_OK", "sequence checkpoint"),
            ("\\tns = ktime_get_ns();\\n",
             "A52_REC3_STATUS_RECORD_TIME_OK", "time checkpoint"),
            ("\\tmessage_len = strnlen(message, sizeof(message));\\n",
             "A52_REC3_STATUS_RECORD_LEN_OK", "message-length checkpoint"),
            ("\\tmemset(&record, 0, sizeof(record));\\n",
             "A52_REC3_STATUS_RECORD_ZEROED", "record-zero checkpoint"),
            ("\\trecord.message_len = cpu_to_le16((u16)message_len);\\n",
             "A52_REC3_STATUS_RECORD_HEADER_OK", "fixed-header checkpoint"),
            ("\\trecord.tgid = cpu_to_le32((u32)current->tgid);\\n",
             "A52_REC3_STATUS_RECORD_CURRENT_OK", "current checkpoint"),
            ("\\trecord.cpu = cpu_to_le16((u16)task_cpu(current));\\n",
             "A52_REC3_STATUS_RECORD_CPU_OK", "cpu checkpoint"),
            ("\\tget_task_comm(record.comm, current);\\n",
             "A52_REC3_STATUS_RECORD_COMM_OK", "comm checkpoint"),
            ("\\tmemcpy(record.message, message, message_len);\\n",
             "A52_REC3_STATUS_RECORD_MESSAGE_OK", "message-copy checkpoint"),
        ]
        for statement, bit, label in checkpoints:
            text = replace_once(
                text,
                statement,
                statement + "\\ta52_ackfr_ramoops_mark_status(" + bit + ");\\n",
                label,
            )
        crc_old = (
            "\\trecord.crc32c = cpu_to_le32(a52_rec3_crc32c(&record,\\n"
            "\\t\\t\\t\\t\\t      offsetof(struct a52_rec3_data,\\n"
            "\\t\\t\\t\\t\\t               crc32c)));\\n"
        )
        text = replace_once(
            text,
            crc_old,
            crc_old + "\\ta52_ackfr_ramoops_mark_status(A52_REC3_STATUS_RECORD_CRC_OK);\\n",
            "crc checkpoint",
        )
        prepared_statement = "\\trecord.ns_inv = cpu_to_le64(~ns);\\n"
        text = replace_once(
            text,
            prepared_statement,
            prepared_statement + "\\ta52_ackfr_ramoops_mark_status(A52_REC3_STATUS_RECORD_PREPARED_OK);\\n",
            "prepared checkpoint",
        )
        writer_call = (
            "\\twritten = a52_ackfr_ramoops_write_record("
            "&record, sizeof(record), seq);\\n"
        )
        text = replace_once(
            text,
            writer_call,
            "\\ta52_ackfr_ramoops_mark_status(A52_REC3_STATUS_RECORD_BEFORE_WRITER);\\n" + writer_call,
            "before-writer checkpoint",
        )

'''
    return replace_once(
        patcher,
        core_marker,
        recorder_instrument + core_marker,
        "record-construction instrumentation insertion",
    )


def main() -> int:
    if not PATCHER.is_file() or not CI.is_file():
        raise SystemExit("run from the repository root")
    PATCHER.write_text(patch_patcher(PATCHER.read_text()), encoding="utf-8")
    CI.write_text(patch_ci(CI.read_text()), encoding="utf-8")
    print("record-construction checkpoint instrumentation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
