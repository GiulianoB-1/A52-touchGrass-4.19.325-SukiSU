#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

PATH = Path("scripts/199_apply_recorder_crc32c.py")

OLD = '''    text = replace_once(
        text,
        "extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,\\n"
        "\\t\\t\\t\\t\\t     unsigned int targets);\\n\\n",
        "extern unsigned int a52_ackfr_ramoops_write(const char *buf, size_t len,\\n"
        "\\t\\t\\t\\t\\t     unsigned int targets);\\n\\n"
        + crc_function,
        "crc32c function insertion",
    )
'''

NEW = '''    text = replace_once(
        text,
        "static int a52_r179_base64_encode(",
        crc_function + "static int a52_r179_base64_encode(",
        "crc32c function insertion",
    )
'''


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if NEW in text:
        print("phase199 CRC anchor already fixed")
        return 0
    count = text.count(OLD)
    if count != 1:
        raise SystemExit(f"expected one brittle CRC anchor, found {count}")
    text = text.replace(OLD, NEW, 1)
    PATH.write_text(text, encoding="utf-8")
    verify = PATH.read_text(encoding="utf-8")
    if NEW not in verify or OLD in verify:
        raise SystemExit("CRC anchor runtime repair verification failed")
    print("phase199 CRC anchor repaired to base64 function boundary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
