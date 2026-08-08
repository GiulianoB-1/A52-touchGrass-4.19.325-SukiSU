#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PATH = Path("scripts/199_ci.sh")
PHASE217_PATH = Path("scripts/217_ci.sh")
PHASE239_IDENTITY_PATH = Path("scripts/239_phase238_identity_overlay.py")

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
    "BOOT rs=ready phase=239 focus=gpu-cx-vdd-parent "
    "roots=%u copies=3 crc=crc32c"
)
PHASE239_IDENTITY_MARKER = "A52_PHASE239_GPU_CX_VDD_PARENT_IDENTITY_V1"


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


def phase239_identity_present() -> bool:
    if not PHASE239_IDENTITY_PATH.is_file():
        return False
    text = PHASE239_IDENTITY_PATH.read_text(encoding="utf-8")
    return PHASE239_IDENTITY_MARKER in text and PHASE239_BOOT in text


def repair_final_identity_text(text: str, target_boot: str, label: str) -> str:
    known = (PHASE210_BOOT, PHASE237_BOOT, PHASE238_BOOT, PHASE239_BOOT)

    target_count = text.count(target_boot)
    stale = tuple(marker for marker in known if marker != target_boot)
    stale_count = sum(text.count(marker) for marker in stale)

    if target_count == 1 and stale_count == 0:
        return text

    if target_count != 0:
        raise RuntimeError(
            f"{label}: expected zero or one target boot audit, found {target_count}"
        )
    if stale_count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one stale final boot audit, found {stale_count}"
        )

    source = next(marker for marker in stale if marker in text)
    repaired = text.replace(source, target_boot, 1)

    if repaired.count(target_boot) != 1:
        raise RuntimeError(f"{label}: target final boot audit count is not one")
    leftovers = [marker for marker in stale if marker in repaired]
    if leftovers:
        raise RuntimeError(
            f"{label}: stale final boot audit remains: {leftovers!r}"
        )
    return repaired


def self_test() -> None:
    prefix = "for marker in 'x' '"
    suffix = "'; do\n"

    phase238 = repair_final_identity_text(
        prefix + PHASE237_BOOT + suffix,
        PHASE238_BOOT,
        "fixture/phase238",
    )
    if PHASE238_BOOT not in phase238 or PHASE237_BOOT in phase238:
        raise AssertionError("Phase 238 final identity audit repair failed")

    phase239 = repair_final_identity_text(
        prefix + PHASE238_BOOT + suffix,
        PHASE239_BOOT,
        "fixture/phase239",
    )
    if PHASE239_BOOT not in phase239 or PHASE238_BOOT in phase239:
        raise AssertionError("Phase 239 final identity audit repair failed")
    if repair_final_identity_text(
        phase239, PHASE239_BOOT, "fixture/phase239-idempotent"
    ) != phase239:
        raise AssertionError("Phase 239 final identity audit repair is not idempotent")

    print("final binary identity audit phase-awareness self-test: PASS")


def repair_final_identity_audit() -> None:
    """Keep the inherited Phase 217 Image audit aligned with final runtime identity."""
    if not PHASE217_PATH.is_file():
        raise SystemExit(f"missing final binary audit script: {PHASE217_PATH}")

    if phase239_identity_present():
        target_boot = PHASE239_BOOT
        phase = "Phase 239"
    else:
        target_boot = PHASE238_BOOT
        phase = "Phase 238"

    text = PHASE217_PATH.read_text(encoding="utf-8")
    try:
        repaired = repair_final_identity_text(
            text,
            target_boot,
            f"{PHASE217_PATH} {phase}",
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    if repaired == text:
        print(f"Phase 217 final binary audit already expects {phase} recorder identity")
        return

    PHASE217_PATH.write_text(repaired, encoding="utf-8")
    verify = PHASE217_PATH.read_text(encoding="utf-8")
    if verify.count(target_boot) != 1:
        raise SystemExit(f"{phase} final boot-marker audit repair failed")

    print(f"Phase 217 final binary audit updated for {phase} recorder identity")


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    self_test()
    repair_phase199_binary_audit()
    repair_final_identity_audit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
