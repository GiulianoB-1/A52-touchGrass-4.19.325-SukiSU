#!/usr/bin/env python3
"""Phase 241: remove the obsolete Phase 240 replay string from the Image gate.

Phase 241 deliberately removes the Phase 240 heartbeat replay call and replaces
it with the broader CXF241 replay. The retained static Phase 240 replay helper
is compile-only legacy structure and may be optimized out, so requiring its
``CXF240 replay-begin`` format string in the final Image is not a valid Phase
241 binary invariant.

This repair changes exactly one packager requirement. It does not relax any
active CXF241, CXF240 driver/supplier, G238, GDSC, identity, or checksum gate.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PACKAGE = Path("scripts/238_package.py")
MARKER = "A52_PHASE241_STALE_R240_REPLAY_IMAGE_GATE_REMOVED_V1"
STALE_LINE = '        b"CXF240 replay-begin t=%u kept=%u seen=%u",\n'
ANCHOR = '''    Structural/source-only Phase241 markers are enforced separately by\n    241_phase240_generated_source_audit.py before compilation.\n'''
REPLACEMENT = '''    Structural/source-only Phase241 markers are enforced separately by\n    241_phase240_generated_source_audit.py before compilation.\n\n    # A52_PHASE241_STALE_R240_REPLAY_IMAGE_GATE_REMOVED_V1\n    # Phase 241 supersedes the Phase 240 heartbeat replay call. Keep the\n    # active Phase 240 drv/match/probe/supplier markers below, but do not\n    # require a format string belonging only to the dead replay helper.\n'''


def patch_text(text: str, label: str) -> str:
    if MARKER not in text:
        if text.count(ANCHOR) != 1:
            raise RuntimeError(
                f"{label}: expected one Phase 241 verify docstring anchor, found {text.count(ANCHOR)}"
            )
        if text.count(STALE_LINE) != 1:
            raise RuntimeError(
                f"{label}: expected one stale Phase 240 replay Image marker, found {text.count(STALE_LINE)}"
            )
        text = text.replace(ANCHOR, REPLACEMENT, 1)
        text = text.replace(STALE_LINE, "", 1)
    validate(text, label)
    return text


def validate(text: str, label: str) -> None:
    if MARKER not in text:
        raise RuntimeError(f"{label}: package-gate repair marker missing")
    if STALE_LINE in text:
        raise RuntimeError(f"{label}: stale Phase 240 replay Image requirement remains")
    required = (
        'b"BOOT rs=ready phase=241 focus=cx-broad-corridor-latch"',
        'b"CXF241 replay-begin t=%u pop=%u/%u drv=%u/%u prb=%u/%u sup=%u/%u"',
        'b"CXF241 create-in node=%.64s"',
        'b"CXF241 dreg-in r=%.32s bus=%.16s"',
        'b"CXF240 drvwalk-in r=%.24s bus=%.16s"',
        'b"CXF240 drv-match d=%.20s r=%.20s rc=%d dead=%d cur=%.16s"',
        'b"CXF240 sup-in d=%.20s r=%.20s ls=%d"',
        'b"CXF240 sup-out d=%.20s rc=%d ls=%d"',
        'b"A52GDSC CX_VDD_PARENT_GET_V1 dev=%s rc=%d"',
        'b"3d9106c.qcom,gdsc"',
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: active Phase 241 package gate missing {token}")


def self_test() -> None:
    fixture = '''def verify_phase241_image(image):\n    """Binary gate.\n\n''' + ANCHOR + '''    """\n    required = (\n        b"BOOT rs=ready phase=241 focus=cx-broad-corridor-latch",\n        b"CXF241 replay-begin t=%u pop=%u/%u drv=%u/%u prb=%u/%u sup=%u/%u",\n        b"CXF241 create-in node=%.64s",\n        b"CXF241 dreg-in r=%.32s bus=%.16s",\n''' + STALE_LINE + '''        b"CXF240 drvwalk-in r=%.24s bus=%.16s",\n        b"CXF240 drv-match d=%.20s r=%.20s rc=%d dead=%d cur=%.16s",\n        b"CXF240 sup-in d=%.20s r=%.20s ls=%d",\n        b"CXF240 sup-out d=%.20s rc=%d ls=%d",\n        b"A52GDSC CX_VDD_PARENT_GET_V1 dev=%s rc=%d",\n        b"3d9106c.qcom,gdsc",\n    )\n'''
    patched = patch_text(fixture, "fixture/238_package.py")
    if patch_text(patched, "fixture/238_package-idempotent.py") != patched:
        raise AssertionError("Phase 241 package-gate repair is not idempotent")
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "238_package.py"
        path.write_text(fixture, encoding="utf-8")
        path.write_text(patch_text(path.read_text(encoding="utf-8"), str(path)), encoding="utf-8")
        validate(path.read_text(encoding="utf-8"), str(path))
    print("Phase 241 package-gate repair self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    if not PACKAGE.is_file():
        raise RuntimeError(f"Phase 241 packager missing: {PACKAGE}")
    PACKAGE.write_text(
        patch_text(PACKAGE.read_text(encoding="utf-8"), str(PACKAGE)),
        encoding="utf-8",
    )
    print(
        "Phase 241 package gate repaired: dead CXF240 replay marker dropped; active diagnostics still required",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 241 package-gate repair failed: {exc}", file=sys.stderr)
        raise
