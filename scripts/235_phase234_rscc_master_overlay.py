#!/usr/bin/env python3
"""Phase 235: admit the DRM component-master handoff without changing R48 transport.

The cumulative Phase 230 wrapper runs before the outer Phase 234 focus pass.  This
script therefore supports both ordering states:

* normal Phase 235 build: patch the Phase 210 recorder directly, install both the
  Phase 234 compatibility sentinel and the Phase 235 sentinel, then let the later
  Phase 234 pass narrow dd.c while skipping its recorder rewrite;
* already-focused fixture/replay: widen an existing Phase 234 recorder in place.
"""
from __future__ import annotations

import sys
from pathlib import Path

PHASE234_MARKER = "A52_PHASE234_RSCC_FOCUSED_RECORDER_V1"
PHASE235_MARKER = "A52_PHASE235_RSCC_MASTER_RECORDER_V1"
PHASE234_COMPAT_BOOT = "BOOT rs=ready phase=234 focus=rscc"
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DD_REL = Path("drivers/base/dd.c")
COMPONENT_REL = Path("drivers/base/component.c")
MSM_DRV_REL = Path("drivers/a52_display/msm/msm_drv.c")
SDE_RSC_REL = Path("drivers/a52_display/msm/sde_rsc.c")

BASE_SEQ_ANCHOR = "\tu64 seq;\n\n\tseq = (u64)atomic64_inc_return(&a52_r179_sequence);"
BASE_BOOT = "BOOT rs=ready phase=210 roots=%u copies=3 crc=crc32c"
PHASE234_BOOT = "BOOT rs=ready phase=234 focus=rscc roots=%u copies=3 crc=crc32c"
PHASE235_BOOT = "BOOT rs=ready phase=235 focus=rscc-master roots=%u copies=3 crc=crc32c"

PHASE234_FILTER = '''\tif (strncmp(fmt, "RSCC", 4) &&
\t    strncmp(fmt, "BOOT ctl=", 9) &&
\t    strncmp(fmt, "BOOT rs=ready", 13))
\t\treturn;'''

PHASE235_FILTER = '''\tif (strncmp(fmt, "RSCC", 4) &&
\t    strncmp(fmt, "DRMCOMP", 7) &&
\t    strncmp(fmt, "COMP ", 5) &&
\t    strncmp(fmt, "BOOT ctl=", 9) &&
\t    strncmp(fmt, "BOOT rs=ready", 13))
\t\treturn;'''

PHASE235_DIRECT_BLOCK = (
    "\tu64 seq;\n\n"
    "\t/* " + PHASE234_MARKER + "\n"
    "\t * " + PHASE235_MARKER + "\n"
    "\t * " + PHASE234_COMPAT_BOOT + " compatibility marker only.\n"
    "\t * Phase 235 preserves the Phase 210 R48/RS48/CRC32C wire format.\n"
    "\t * Persist only RSCC plus the inherited bounded DRM component-master\n"
    "\t * records so the useful retention window is not consumed by unrelated\n"
    "\t * boot/GPU/driver-core traffic.\n"
    "\t */\n"
    "\tif (!fmt)\n"
    "\t\treturn;\n"
    + PHASE235_FILTER
    + "\n\n\tseq = (u64)atomic64_inc_return(&a52_r179_sequence);"
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def validate_phase235_state(text: str, label: str) -> None:
    for token in (
        PHASE234_MARKER,
        PHASE235_MARKER,
        PHASE234_COMPAT_BOOT,
        PHASE235_FILTER,
        PHASE235_BOOT,
        'strncmp(fmt, "RSCC", 4)',
        'strncmp(fmt, "DRMCOMP", 7)',
        'strncmp(fmt, "COMP ", 5)',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: incomplete Phase 235 state, missing {token}")
    if BASE_BOOT in text:
        raise RuntimeError(f"{label}: stale Phase 210 boot identity remains")
    # PHASE234_COMPAT_BOOT is deliberately retained only in a C comment so the
    # later Phase 234 source-level compatibility assertion succeeds.  The full
    # Phase 234 runtime format string must not remain.
    if PHASE234_BOOT in text:
        raise RuntimeError(f"{label}: stale Phase 234 runtime boot identity remains")


def patch_recorder(text: str, label: str) -> str:
    if PHASE235_MARKER in text:
        validate_phase235_state(text, label)
        return text

    if PHASE234_MARKER in text:
        marker_block = (
            PHASE234_MARKER
            + "\n\t * "
            + PHASE235_MARKER
            + "\n\t * "
            + PHASE234_COMPAT_BOOT
            + " compatibility marker only."
        )
        text = replace_once(
            text,
            PHASE234_MARKER,
            marker_block,
            f"{label}: Phase 235 marker after Phase 234",
        )
        text = replace_once(
            text,
            PHASE234_FILTER,
            PHASE235_FILTER,
            f"{label}: widen focused event filter",
        )
        text = replace_once(
            text,
            PHASE234_BOOT,
            PHASE235_BOOT,
            f"{label}: Phase 235 boot identity after Phase 234",
        )
        validate_phase235_state(text, label)
        return text

    # Normal cumulative ordering: Phase 230 is still inside the generated
    # Phase 233 chain, while the outer Phase 234 focus pass has not run yet.
    # Install the final Phase 235 recorder now.  Carry the Phase 234 sentinel
    # and its boot-prefix as source-only compatibility markers so that the
    # later Phase 234 pass skips only the recorder rewrite but still applies
    # the focused RSCC dd.c filter and passes its source assertion.
    if text.count(BASE_SEQ_ANCHOR) != 1 or text.count(BASE_BOOT) != 1:
        raise RuntimeError(
            f"{label}: recorder is neither Phase 210 base nor Phase 234 focused state"
        )
    text = replace_once(
        text,
        BASE_SEQ_ANCHOR,
        PHASE235_DIRECT_BLOCK,
        f"{label}: direct Phase 210 -> Phase 235 filter",
    )
    text = replace_once(
        text,
        BASE_BOOT,
        PHASE235_BOOT,
        f"{label}: direct Phase 210 -> Phase 235 boot identity",
    )
    validate_phase235_state(text, label)
    return text


def candidate_roots(arguments: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        path = Path(value)
        roots.extend((path, path.parent))
    roots.extend((Path("workspace/gki-phase199-src"), Path("gki/common")))

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def locate_root(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for root in candidate_roots(arguments):
        required = (
            root / RECORDER_REL,
            root / DD_REL,
            root / COMPONENT_REL,
            root / MSM_DRV_REL,
            root / SDE_RSC_REL,
        )
        if not all(path.is_file() for path in required):
            continue
        recorder = (root / RECORDER_REL).read_text(encoding="utf-8")
        recognized = any(
            token in recorder
            for token in (PHASE235_MARKER, PHASE234_MARKER, BASE_BOOT)
        )
        if not recognized:
            continue
        matches.append(root)

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(path) for path in unique) or "none"
        raise RuntimeError(
            f"expected one generated Phase 210/234 kernel root, found {len(unique)}: {rendered}"
        )
    return unique[0]


def require_tokens(path: Path, tokens: tuple[str, ...]) -> str:
    text = path.read_text(encoding="utf-8")
    for token in tokens:
        if token not in text:
            raise RuntimeError(f"{path}: required inherited trace marker missing: {token}")
    return text


def validate_inherited_component_trace(root: Path) -> None:
    require_tokens(
        root / MSM_DRV_REL,
        (
            "DRMCOMP collect enter",
            "DRMCOMP connectors prop=",
            "DRMCOMP connector i=",
            "DRMCOMP match-add i=",
            "DRMCOMP collect exit",
            "DRMCOMP probe collect",
            "DRMCOMP master-add enter",
            "DRMCOMP master-add exit",
        ),
    )
    require_tokens(
        root / COMPONENT_REL,
        (
            "COMP master-add enter",
            "COMP master-add result",
            "COMP master stage=",
            "COMP slot i=",
            "COMP component-add enter",
            "COMP component-add result",
        ),
    )
    require_tokens(
        root / SDE_RSC_REL,
        (
            "RSCC probe enter",
            "RSCC component-add enter",
            "RSCC bind enter",
            "RSCC bind exit",
        ),
    )

    # At this hook Phase 234 normally has not narrowed dd.c yet.  Accept either
    # inherited broad Phase 193 markers or already-focused Phase 234 markers.
    # The outer Phase 234 pass remains authoritative for converting broad
    # RSCCCORE candidate logging to RSCCFOCUS before compilation.
    dd = (root / DD_REL).read_text(encoding="utf-8")
    device_ok = (
        "RSCCFOCUS match path=device-attach" in dd
        or "RSCCCORE match path=device-attach" in dd
    )
    driver_ok = (
        "RSCCFOCUS match path=driver-attach" in dd
        or "RSCCCORE match path=driver-attach" in dd
    )
    if not device_ok or not driver_ok:
        raise RuntimeError(f"{root / DD_REL}: inherited RSCC match trace anchors missing")


def apply(arguments: list[str]) -> Path:
    root = locate_root(arguments)
    validate_inherited_component_trace(root)

    recorder_path = root / RECORDER_REL
    original = recorder_path.read_text(encoding="utf-8")
    patched = patch_recorder(original, str(recorder_path))
    recorder_path.write_text(patched, encoding="utf-8")
    validate_phase235_state(patched, str(recorder_path))

    print(
        "Phase 235 RSCC component-master recorder applied: R48/RS48/CRC32C "
        "transport unchanged; persisted classes are RSCC, DRMCOMP, bounded COMP, "
        "and recorder control",
        flush=True,
    )
    return root


def self_test() -> None:
    base_fixture = f'''void a52_ackfr_record(const char *fmt, ...)\n{{\n{BASE_SEQ_ANCHOR}\n}}\na52_ackfr_record("{BASE_BOOT}", roots);\n'''
    base_patched = patch_recorder(base_fixture, "phase235-base-fixture")
    if patch_recorder(base_patched, "phase235-base-idempotence") != base_patched:
        raise AssertionError("Phase 235 direct-base recorder patch is not idempotent")
    validate_phase235_state(base_patched, "phase235-base-validated")

    focused_fixture = f'''void a52_ackfr_record(const char *fmt, ...)\n{{\n\tu64 seq;\n\n\t/* {PHASE234_MARKER}\n\t * inherited focus\n\t */\n{PHASE234_FILTER}\n\n\tseq = 1;\n}}\na52_ackfr_record("{PHASE234_BOOT}", roots);\n'''
    focused_patched = patch_recorder(focused_fixture, "phase235-focused-fixture")
    if patch_recorder(
        focused_patched, "phase235-focused-idempotence"
    ) != focused_patched:
        raise AssertionError("Phase 235 focused recorder patch is not idempotent")
    validate_phase235_state(focused_patched, "phase235-focused-validated")

    for patched in (base_patched, focused_patched):
        if PHASE234_BOOT in patched:
            raise AssertionError("Phase 235 self-test retained Phase 234 runtime boot marker")
        if BASE_BOOT in patched:
            raise AssertionError("Phase 235 self-test retained Phase 210 runtime boot marker")
    print("Phase 235 pre/post-Phase234 recorder overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
