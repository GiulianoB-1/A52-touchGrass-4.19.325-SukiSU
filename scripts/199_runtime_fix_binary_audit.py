#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

PATH = Path("scripts/199_ci.sh")
PHASE217_PATH = Path("scripts/217_ci.sh")
PHASE239_IDENTITY_PATH = Path("scripts/239_phase238_identity_overlay.py")
PHASE240_IDENTITY_PATH = Path("scripts/240_phase239_identity_overlay.py")
PHASE241_IDENTITY_PATH = Path("scripts/241_phase240_identity_overlay.py")
PHASE242_IDENTITY_PATH = Path("scripts/242_phase241_identity_overlay.py")
PHASE243_IDENTITY_PATH = Path("scripts/243_phase242_identity_overlay.py")

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
PHASE240_BOOT = (
    "BOOT rs=ready phase=240 focus=cx-supplier-gate-latch "
    "roots=%u copies=3 crc=crc32c"
)
PHASE240_IDENTITY_MARKER = "A52_PHASE240_CX_SUPPLIER_GATE_LATCH_IDENTITY_V1"
PHASE241_BOOT = (
    "BOOT rs=ready phase=241 focus=cx-broad-corridor-latch "
    "roots=%u copies=3 crc=crc32c"
)
PHASE241_IDENTITY_MARKER = "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_IDENTITY_V1"
PHASE242_BOOT = (
    "BOOT rs=ready phase=242 focus=cx-sticky-state "
    "roots=%u copies=3 crc=crc32c"
)
PHASE242_IDENTITY_MARKER = "A52_PHASE242_CX_STICKY_STATE_IDENTITY_V1"
PHASE243_BOOT = (
    "BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers "
    "roots=%u copies=3 crc=crc32c"
)
PHASE243_IDENTITY_MARKER = "A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1"


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


def identity_present(path: Path, marker: str, boot: str) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return marker in text and boot in text


def phase239_identity_present() -> bool:
    return identity_present(PHASE239_IDENTITY_PATH, PHASE239_IDENTITY_MARKER, PHASE239_BOOT)


def phase240_identity_present() -> bool:
    return identity_present(PHASE240_IDENTITY_PATH, PHASE240_IDENTITY_MARKER, PHASE240_BOOT)


def phase241_identity_present() -> bool:
    return identity_present(PHASE241_IDENTITY_PATH, PHASE241_IDENTITY_MARKER, PHASE241_BOOT)


def phase242_identity_present() -> bool:
    return identity_present(PHASE242_IDENTITY_PATH, PHASE242_IDENTITY_MARKER, PHASE242_BOOT)


def phase243_identity_present() -> bool:
    return identity_present(PHASE243_IDENTITY_PATH, PHASE243_IDENTITY_MARKER, PHASE243_BOOT)


def repair_final_identity_text(text: str, target_boot: str, label: str) -> str:
    known = (
        PHASE210_BOOT, PHASE237_BOOT, PHASE238_BOOT, PHASE239_BOOT,
        PHASE240_BOOT, PHASE241_BOOT, PHASE242_BOOT, PHASE243_BOOT,
    )
    target_count = text.count(target_boot)
    stale = tuple(marker for marker in known if marker != target_boot)
    stale_count = sum(text.count(marker) for marker in stale)
    if target_count == 1 and stale_count == 0:
        return text
    if target_count != 0:
        raise RuntimeError(f"{label}: expected zero or one target boot audit, found {target_count}")
    if stale_count != 1:
        raise RuntimeError(f"{label}: expected exactly one stale final boot audit, found {stale_count}")
    source = next(marker for marker in stale if marker in text)
    repaired = text.replace(source, target_boot, 1)
    if repaired.count(target_boot) != 1:
        raise RuntimeError(f"{label}: target final boot audit count is not one")
    leftovers = [marker for marker in stale if marker in repaired]
    if leftovers:
        raise RuntimeError(f"{label}: stale final boot audit remains: {leftovers!r}")
    return repaired


def self_test() -> None:
    prefix = "for marker in 'x' '"
    suffix = "'; do\n"
    phase238 = repair_final_identity_text(prefix + PHASE237_BOOT + suffix, PHASE238_BOOT, "fixture/phase238")
    if PHASE238_BOOT not in phase238 or PHASE237_BOOT in phase238:
        raise AssertionError("Phase 238 final identity audit repair failed")
    phase239 = repair_final_identity_text(prefix + PHASE238_BOOT + suffix, PHASE239_BOOT, "fixture/phase239")
    if PHASE239_BOOT not in phase239 or PHASE238_BOOT in phase239:
        raise AssertionError("Phase 239 final identity audit repair failed")
    if repair_final_identity_text(phase239, PHASE239_BOOT, "fixture/phase239-idempotent") != phase239:
        raise AssertionError("Phase 239 final identity audit repair is not idempotent")
    phase240 = repair_final_identity_text(prefix + PHASE239_BOOT + suffix, PHASE240_BOOT, "fixture/phase240")
    if PHASE240_BOOT not in phase240 or PHASE239_BOOT in phase240:
        raise AssertionError("Phase 240 final identity audit repair failed")
    if repair_final_identity_text(phase240, PHASE240_BOOT, "fixture/phase240-idempotent") != phase240:
        raise AssertionError("Phase 240 final identity audit repair is not idempotent")
    phase241 = repair_final_identity_text(prefix + PHASE240_BOOT + suffix, PHASE241_BOOT, "fixture/phase241")
    if PHASE241_BOOT not in phase241 or PHASE240_BOOT in phase241:
        raise AssertionError("Phase 241 final identity audit repair failed")
    if repair_final_identity_text(phase241, PHASE241_BOOT, "fixture/phase241-idempotent") != phase241:
        raise AssertionError("Phase 241 final identity audit repair is not idempotent")
    phase242 = repair_final_identity_text(prefix + PHASE241_BOOT + suffix, PHASE242_BOOT, "fixture/phase242")
    if PHASE242_BOOT not in phase242 or PHASE241_BOOT in phase242:
        raise AssertionError("Phase 242 final identity audit repair failed")
    if repair_final_identity_text(phase242, PHASE242_BOOT, "fixture/phase242-idempotent") != phase242:
        raise AssertionError("Phase 242 final identity audit repair is not idempotent")
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as temp:
        probe = Path(temp) / "identity.py"
        probe.write_text(PHASE243_IDENTITY_MARKER + "\n" + PHASE243_BOOT + "\n", encoding="utf-8")
        if not identity_present(probe, PHASE243_IDENTITY_MARKER, PHASE243_BOOT):
            raise AssertionError("Phase 243 identity presence helper failed")
    phase243 = repair_final_identity_text(prefix + PHASE242_BOOT + suffix, PHASE243_BOOT, "fixture/phase243")
    if PHASE243_BOOT not in phase243 or PHASE242_BOOT in phase243:
        raise AssertionError("Phase 243 final identity audit repair failed")
    if repair_final_identity_text(phase243, PHASE243_BOOT, "fixture/phase243-idempotent") != phase243:
        raise AssertionError("Phase 243 final identity audit repair is not idempotent")
    print("final binary identity audit phase-awareness self-test: PASS through Phase 243")


def repair_final_identity_audit() -> None:
    if not PHASE217_PATH.is_file():
        raise SystemExit(f"missing final binary audit script: {PHASE217_PATH}")
    if phase243_identity_present():
        target_boot, phase = PHASE243_BOOT, "Phase 243"
    elif phase242_identity_present():
        target_boot, phase = PHASE242_BOOT, "Phase 242"
    elif phase241_identity_present():
        target_boot, phase = PHASE241_BOOT, "Phase 241"
    elif phase240_identity_present():
        target_boot, phase = PHASE240_BOOT, "Phase 240"
    elif phase239_identity_present():
        target_boot, phase = PHASE239_BOOT, "Phase 239"
    else:
        target_boot, phase = PHASE238_BOOT, "Phase 238"
    text = PHASE217_PATH.read_text(encoding="utf-8")
    try:
        repaired = repair_final_identity_text(text, target_boot, f"{PHASE217_PATH} {phase}")
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if repaired == text:
        print(f"Phase 217 final binary audit already expects {phase} recorder identity")
        return
    PHASE217_PATH.write_text(repaired, encoding="utf-8")
    verify = PHASE217_PATH.read_text(encoding="utf-8")
    if verify.count(target_boot) != 1:
        raise SystemExit(f"{phase} final boot-marker audit repair failed")
    stale = (PHASE210_BOOT, PHASE237_BOOT, PHASE238_BOOT, PHASE239_BOOT, PHASE240_BOOT, PHASE241_BOOT, PHASE242_BOOT, PHASE243_BOOT)
    leftovers = [marker for marker in stale if marker != target_boot and marker in verify]
    if leftovers:
        raise SystemExit(f"{phase} stale boot-marker audit remains: {leftovers!r}")
    print(f"Phase 217 final binary audit updated for {phase} recorder identity")


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test(); return 0
    self_test()
    repair_phase199_binary_audit()
    repair_final_identity_audit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())