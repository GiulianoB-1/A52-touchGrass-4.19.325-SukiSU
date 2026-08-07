#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

PATH = Path("scripts/199_ci.sh")
PHASE217_PATH = Path("scripts/217_ci.sh")

OLD = "for marker in 'A52R0199' 'phase199 triple-copy RS+CRC32C recorder enabled'"
NEW = "for marker in 'phase199 triple-copy RS+CRC32C recorder enabled'"

PHASE210_BOOT = "BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c"
PHASE237_BOOT = (
    "BOOT rs=ready phase=237 focus=ofpop-probe "
    "roots=%u copies=3 crc=crc32c"
)


def repair_phase199_binary_audit() -> None:
    text = PATH.read_text(encoding="utf-8")
    if NEW in text and OLD not in text:
        print("phase199 binary audit already ignores optimized record magic")
        return
    count = text.count(OLD)
    if count != 1:
        raise SystemExit(f"expected one binary magic audit anchor, found {count}")
    text = text.replace(OLD, NEW, 1)
    PATH.write_text(text, encoding="utf-8")
    verify = PATH.read_text(encoding="utf-8")
    if OLD in verify or NEW not in verify:
        raise SystemExit("binary audit repair verification failed")
    print(
        "phase199 binary audit repaired: source validates A52R0199, "
        "Image validates runtime strings"
    )


def repair_phase237_final_identity_audit() -> None:
    """Update only the final Phase 217 Image audit for the Phase 237 identity."""
    if not PHASE217_PATH.is_file():
        raise SystemExit(f"missing final binary audit script: {PHASE217_PATH}")

    text = PHASE217_PATH.read_text(encoding="utf-8")
    if PHASE237_BOOT in text and PHASE210_BOOT not in text:
        print("Phase 217 final binary audit already expects Phase 237 recorder identity")
        return

    count = text.count(PHASE210_BOOT)
    if count != 1:
        raise SystemExit(
            "expected exactly one stale Phase 210 final boot audit, "
            f"found {count}"
        )

    text = text.replace(PHASE210_BOOT, PHASE237_BOOT, 1)
    PHASE217_PATH.write_text(text, encoding="utf-8")

    verify = PHASE217_PATH.read_text(encoding="utf-8")
    if PHASE210_BOOT in verify or verify.count(PHASE237_BOOT) != 1:
        raise SystemExit("Phase 237 final boot-marker audit repair failed")
    print("Phase 217 final binary audit updated for Phase 237 recorder identity")


def main() -> int:
    repair_phase199_binary_audit()
    repair_phase237_final_identity_audit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
