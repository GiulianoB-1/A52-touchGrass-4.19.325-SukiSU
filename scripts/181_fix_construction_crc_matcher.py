#!/usr/bin/env python3
from pathlib import Path

PATCHER = Path("scripts/178_apply_a52_display_init_recorder_fec.py")


def main() -> int:
    if not PATCHER.is_file():
        raise SystemExit("run after script 180 from the repository root")

    source = PATCHER.read_text(encoding="utf-8")
    start = source.find("        crc_old = (\n")
    end = source.find("        prepared_statement = ", start)
    if start < 0 or end < 0 or end <= start:
        raise SystemExit("construction CRC matcher block not found")

    robust = '''        crc_start = text.find(
            "\\trecord.crc32c = cpu_to_le32(a52_rec3_crc32c(&record,"
        )
        if crc_start < 0:
            raise SystemExit("CRC assignment start missing")
        crc_close = text.find("crc32c)));", crc_start)
        if crc_close < 0:
            raise SystemExit("CRC assignment terminator missing")
        crc_line_end = text.find("\\n", crc_close)
        if crc_line_end < 0:
            raise SystemExit("CRC assignment line ending missing")
        text = (
            text[:crc_line_end + 1]
            + "\\ta52_ackfr_ramoops_mark_status(A52_REC3_STATUS_RECORD_CRC_OK);\\n"
            + text[crc_line_end + 1:]
        )
'''

    PATCHER.write_text(source[:start] + robust + source[end:], encoding="utf-8")
    print("construction CRC matcher made whitespace independent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
