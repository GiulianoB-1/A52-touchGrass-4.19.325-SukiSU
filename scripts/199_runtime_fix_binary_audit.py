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
PHASE238_BOOT = (
    "BOOT rs=ready phase=238 focus=gpu-supplier-broad "
    "roots=%u copies=3 crc=crc32c"
)
PHASE239_BOOT = (
    "BOOT rs=ready phase=239 focus=cx-vdd-parent-fix "
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


def repair_phase239_final_identity_audit() -> None:
    """Require the exact Phase 239 runtime identity in the inherited final Image audit."""
    if not PHASE217_PATH.is_file():
        raise SystemExit(f"missing final binary audit script: {PHASE217_PATH}")

    text = PHASE217_PATH.read_text(encoding="utf-8")
    stale = (PHASE210_BOOT, PHASE237_BOOT, PHASE238_BOOT)

    if PHASE239_BOOT in text and not any(marker in text for marker in stale):
        if text.count(PHASE239_BOOT) != 1:
            raise SystemExit(
                "Phase 239 final boot-marker audit is not exact: "
                f"found {text.count(PHASE239_BOOT)} copies"
            )
        print("Phase 217 final binary audit already expects Phase 239 recorder identity")
        return

    stale_count = sum(text.count(marker) for marker in stale)
    if stale_count != 1 or PHASE239_BOOT in text:
        raise SystemExit(
            "expected exactly one stale Phase 210/237/238 final boot audit and no "
            f"Phase 239 audit, found stale={stale_count} phase239={text.count(PHASE239_BOOT)}"
        )

    for marker in stale:
        if marker in text:
            text = text.replace(marker, PHASE239_BOOT, 1)
            break
    PHASE217_PATH.write_text(text, encoding="utf-8")

    verify = PHASE217_PATH.read_text(encoding="utf-8")
    if any(marker in verify for marker in stale) or verify.count(PHASE239_BOOT) != 1:
        raise SystemExit("Phase 239 final boot-marker audit repair failed")
    print("Phase 217 final binary audit updated for Phase 239 recorder identity")


def main() -> int:
    repair_phase199_binary_audit()
    repair_phase239_final_identity_audit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
