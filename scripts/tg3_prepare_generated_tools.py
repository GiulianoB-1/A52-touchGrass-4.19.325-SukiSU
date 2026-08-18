#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

APPLY = Path("scripts/tg1_apply_critical_flight_recorder.py")
DECODE = Path("scripts/tg1_decode_critical_bank.py")
V2_MARKER = "A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V2_EARLY_SEAL_90S"
V3_MARKER = "A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V3_WDT_OFF_TYPED_SEAL_210S"
WDT_MARKER = "A52_TOUCHGRASS_V3_DIAGNOSTIC_QCOM_WDT_OFF"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected one anchor, found {n}")
    return text.replace(old, new, 1)


def main() -> int:
    apply = APPLY.read_text(encoding="utf-8")
    decode = DECODE.read_text(encoding="utf-8")

    if V2_MARKER not in apply:
        raise SystemExit("v3 requires the v2 generated-tool patch first")

    # Give fallback seals their own event type. v2 used DSI_DMA_TIMEOUT for every
    # seal record, which made a timer seal look like a real DSI timeout.
    apply = replace_once(
        apply,
        '#define A52_TGCR_EVT_TEXT                 0x0001U',
        '#define A52_TGCR_EVT_TEXT                 0x0001U\n'
        '#define A52_TGCR_EVT_SEAL                 0x0002U',
        'typed seal event',
    )
    apply = replace_once(
        apply,
        '#define A52_TGCR_REASON_DEADLINE_90S      0x00000002U',
        '#define A52_TGCR_REASON_DEADLINE_90S      0x00000002U\n'
        '#define A52_TGCR_REASON_DEADLINE_210S     0x00000003U',
        '210-second fallback reason',
    )
    apply = replace_once(
        apply,
        'a52_tgcr_write_slot_locked(A52_TGCR_EVT_DSI_DMA_TIMEOUT,\n                point,',
        'a52_tgcr_write_slot_locked(A52_TGCR_EVT_SEAL,\n                point,',
        'seal event type',
    )
    apply = replace_once(
        apply,
        'a52_tgcr_seal(A52_TGCR_REASON_DEADLINE_90S, 0xffffffffU,',
        'a52_tgcr_seal(A52_TGCR_REASON_DEADLINE_210S, 0xffffffffU,',
        'fallback seal reason',
    )
    apply = replace_once(
        apply,
        'msecs_to_jiffies(90000)',
        'msecs_to_jiffies(210000)',
        'fallback deadline',
    )
    apply = replace_once(
        apply,
        V2_MARKER,
        V3_MARKER,
        'compiled v3 marker',
    )
    apply = replace_once(
        apply,
        'a recorder-only 90-second fallback seal preserves the final circular window\n'
        ' * before the configured 100-second softdog.',
        'a recorder-only 210-second fallback seal preserves the late circular window\n'
        ' * after SurfaceFlinger/composer have had time to start.',
        'fallback comment',
    )

    # Tag the watchdog handoff already present in the reconstructed Phase279
    # tree as the v3 diagnostic contract. Phase279 already stops qcom-wdt and
    # returns before watchdog registration, so v3 must preserve that proven
    # disarm instead of anchoring on the pre-Phase279 probe layout.
    apply = replace_once(
        apply,
        'SMMU_REL = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")',
        'SMMU_REL = Path("drivers/iommu/arm/arm-smmu/arm-smmu.c")\n'
        'WDT_REL = Path("drivers/watchdog/qcom-wdt.c")',
        'watchdog path',
    )

    watchdog_fn = f'''\n\ndef patch_watchdog(text: str) -> str:
    marker = "{WDT_MARKER}"
    if marker in text:
        return text
    old = (
        "\\t\\t/* A52_FAILURE_WINDOW_WATCHDOG_DISARM */\\n"
        "\\t\\t{{\\n"
        "\\t\\t\\tu32 a52_wdt_before;\\n"
        "\\t\\t\\tu32 a52_wdt_after;\\n\\n"
        "\\t\\t\\ta52_wdt_before = readl(wdt_addr(wdt, WDT_STS));\\n"
        "\\t\\t\\tqcom_wdt_stop(&wdt->wdd);\\n"
        "\\t\\t\\ta52_wdt_after = readl(wdt_addr(wdt, WDT_STS));\\n"
        "\\t\\t\\ta52_ackfr_record(\\\"WDT disarm before=%u after=%u\\\",\\n"
        "\\t\\t\\t\\t\\t  !!(a52_wdt_before & 1),\\n"
        "\\t\\t\\t\\t\\t  !!(a52_wdt_after & 1));\\n"
        "\\t\\t\\tdev_warn(&pdev->dev,\\n"
        "\\t\\t\\t\\t \\\"A52 diagnostic: watchdog disabled for manual recovery\\\\n\\\");\\n"
        "\\t\\t\\treturn 0;\\n"
        "\\t\\t}}\\n"
    )
    new = (
        "\\t\\t/* A52_FAILURE_WINDOW_WATCHDOG_DISARM */\\n"
        f"\\t\\t/* {{marker}}: diagnostic boot only */\\n"
        "\\t\\t{{\\n"
        "\\t\\t\\tu32 a52_wdt_before;\\n"
        "\\t\\t\\tu32 a52_wdt_after;\\n\\n"
        "\\t\\t\\ta52_wdt_before = readl(wdt_addr(wdt, WDT_STS));\\n"
        "\\t\\t\\tqcom_wdt_stop(&wdt->wdd);\\n"
        "\\t\\t\\ta52_wdt_after = readl(wdt_addr(wdt, WDT_STS));\\n"
        "\\t\\t\\ta52_ackfr_record(\\\"WDT disarm before=%u after=%u\\\",\\n"
        "\\t\\t\\t\\t\\t  !!(a52_wdt_before & 1),\\n"
        "\\t\\t\\t\\t\\t  !!(a52_wdt_after & 1));\\n"
        "\\t\\t\\tdev_warn(&pdev->dev,\\n"
        "\\t\\t\\t\\t \\\"A52 TouchGrass v3: QCOM watchdog disabled for late recorder\\\\n\\\");\\n"
        "\\t\\t\\treturn 0; /* diagnostic only: do not register/re-arm */\\n"
        "\\t\\t}}\\n"
    )
    return replace_once(text, old, new, "Phase279 watchdog handoff")
'''
    apply = replace_once(apply, '\n\ndef apply(root: Path) -> None:\n', watchdog_fn + '\n\ndef apply(root: Path) -> None:\n', 'watchdog patch function')

    apply = replace_once(
        apply,
        'paths = [REC_REL, HDR_REL, DSI_REL, SMMU_REL]',
        'paths = [REC_REL, HDR_REL, DSI_REL, SMMU_REL, WDT_REL]',
        'apply source list',
    )
    apply = replace_once(
        apply,
        '        SMMU_REL: patch_smmu,\n    }',
        '        SMMU_REL: patch_smmu,\n        WDT_REL: patch_watchdog,\n    }',
        'apply function map',
    )
    apply = replace_once(
        apply,
        'if changed not in (0, 4):',
        'if changed not in (0, 5):',
        'apply changed count',
    )
    apply = replace_once(
        apply,
        'partial application: changed {changed}/4 files',
        'partial application: changed {changed}/5 files',
        'apply count message',
    )
    expected_loop = 'for rel in [REC_REL, HDR_REL, DSI_REL, SMMU_REL]:'
    if apply.count(expected_loop) != 3:
        raise SystemExit(f"self-test source lists: expected 3 anchors, found {apply.count(expected_loop)}")
    apply = apply.replace(
        expected_loop,
        'for rel in [REC_REL, HDR_REL, DSI_REL, SMMU_REL, WDT_REL]:',
    )
    apply = replace_once(
        apply,
        '            if text.count(MARKER) != 1:\n'
        '                raise RuntimeError(f"self-test marker count for {rel}")',
        f'            expected_marker = "{WDT_MARKER}" if rel == WDT_REL else MARKER\n'
        '            if text.count(expected_marker) != 1:\n'
        '                raise RuntimeError(f"self-test marker count for {rel}: {expected_marker}")',
        'self-test per-file marker',
    )

    decode = replace_once(
        decode,
        "EVENT_NAMES = {\n    0x0001: 'TEXT',",
        "EVENT_NAMES = {\n    0x0001: 'TEXT',\n    0x0002: 'SEAL',",
        'decoder typed seal',
    )
    decode = replace_once(
        decode,
        "REASONS = {1: 'DSI_DMA_TIMEOUT', 2: 'DEADLINE_90S'}",
        "REASONS = {1: 'DSI_DMA_TIMEOUT', 2: 'DEADLINE_90S', 3: 'DEADLINE_210S'}",
        'decoder 210-second reason',
    )

    APPLY.write_text(apply, encoding="utf-8")
    DECODE.write_text(decode, encoding="utf-8")
    print("TouchGrass v3 generated-tool patch: PASS")
    print("TouchGrass v3 typed seal: PASS")
    print("TouchGrass v3 Phase261 watchdog-stop restoration: staged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
