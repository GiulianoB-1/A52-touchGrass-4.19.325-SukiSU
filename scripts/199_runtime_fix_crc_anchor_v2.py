#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

PATH = Path("scripts/199_apply_recorder_crc32c.py")

BRITTLE = '''    text = replace_once(
        text,
        "extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,\\n"
        "\\t\\t\\t\\t\\t     unsigned int targets);\\n\\n",
        "extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,\\n"
        "\\t\\t\\t\\t\\t     unsigned int targets);\\n\\n"
        + crc_function,
        "crc32c function insertion",
    )
'''

BASE64_BOUNDARY = '''    text = replace_once(
        text,
        "static int a52_r179_base64_encode(",
        crc_function + "static int a52_r179_base64_encode(",
        "crc32c function insertion",
    )
'''

FINAL = '''    text = replace_once(
        text,
        "extern unsigned int a52_ackfr_ramoops_write(",
        crc_function + "extern unsigned int a52_ackfr_ramoops_write(",
        "crc32c function insertion",
    )
'''


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if FINAL in text:
        print("phase199 CRC anchor v2 already applied")
        return 0

    matches = text.count(BRITTLE) + text.count(BASE64_BOUNDARY)
    if matches != 1:
        raise SystemExit(f"expected one replaceable CRC anchor, found {matches}")
    if BRITTLE in text:
        text = text.replace(BRITTLE, FINAL, 1)
    else:
        text = text.replace(BASE64_BOUNDARY, FINAL, 1)
    PATH.write_text(text, encoding="utf-8")

    verify = PATH.read_text(encoding="utf-8")
    if FINAL not in verify or BRITTLE in verify or BASE64_BOUNDARY in verify:
        raise SystemExit("CRC anchor v2 verification failed")
    print("phase199 CRC anchor repaired to writer declaration prefix")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
