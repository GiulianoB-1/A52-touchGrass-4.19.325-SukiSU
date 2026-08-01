#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

PATH = Path("scripts/199_ci.sh")

OLD = "for marker in 'A52R0199' 'phase199 triple-copy RS+CRC32C recorder enabled'"
NEW = "for marker in 'phase199 triple-copy RS+CRC32C recorder enabled'"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if NEW in text and OLD not in text:
        print("phase199 binary audit already ignores optimized record magic")
        return 0
    count = text.count(OLD)
    if count != 1:
        raise SystemExit(f"expected one binary magic audit anchor, found {count}")
    text = text.replace(OLD, NEW, 1)
    PATH.write_text(text, encoding="utf-8")
    verify = PATH.read_text(encoding="utf-8")
    if OLD in verify or NEW not in verify:
        raise SystemExit("binary audit repair verification failed")
    print("phase199 binary audit repaired: source validates A52R0199, Image validates runtime strings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
