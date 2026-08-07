#!/usr/bin/env python3
"""Phase 235: widen the proven Phase 234 recorder to the DRM component-master handoff."""
from __future__ import annotations

import sys
from pathlib import Path

PHASE234_MARKER = "A52_PHASE234_RSCC_FOCUSED_RECORDER_V1"
PHASE235_MARKER = "A52_PHASE235_RSCC_MASTER_RECORDER_V1"
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DD_REL = Path("drivers/base/dd.c")
COMPONENT_REL = Path("drivers/base/component.c")
MSM_DRV_REL = Path("drivers/a52_display/msm/msm_drv.c")
SDE_RSC_REL = Path("drivers/a52_display/msm/sde_rsc.c")

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

PHASE234_BOOT = "BOOT rs=ready phase=234 focus=rscc roots=%u copies=3 crc=crc32c"
PHASE235_BOOT = "BOOT rs=ready phase=235 focus=rscc-master roots=%u copies=3 crc=crc32c"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_recorder(text: str, label: str) -> str:
    if PHASE235_MARKER in text:
        for token in (PHASE235_FILTER, PHASE235_BOOT):
            if token not in text:
                raise RuntimeError(f"{label}: incomplete idempotent Phase 235 state")
        return text

    if text.count(PHASE234_MARKER) != 1:
        raise RuntimeError(
            f"{label}: expected one Phase 234 recorder marker, "
            f"found {text.count(PHASE234_MARKER)}"
        )

    text = replace_once(
        text,
        PHASE234_MARKER,
        PHASE234_MARKER + "\n\t * " + PHASE235_MARKER,
        f"{label}: Phase 235 marker",
    )
    text = replace_once(
        text, PHASE234_FILTER, PHASE235_FILTER, f"{label}: focused event filter"
    )
    text = replace_once(
        text, PHASE234_BOOT, PHASE235_BOOT, f"{label}: boot identity"
    )
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
        if PHASE234_MARKER not in recorder and PHASE235_MARKER not in recorder:
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
            f"expected one generated Phase 234 kernel root, found {len(unique)}: {rendered}"
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
    dd = require_tokens(
        root / DD_REL,
        (
            "RSCCFOCUS match path=device-attach",
            "RSCCFOCUS match path=driver-attach",
        ),
    )
    for broad in (
        'a52_ackfr_record("RSCCCORE match path=device-attach',
        'a52_ackfr_record("RSCCCORE match path=driver-attach',
    ):
        if broad in dd:
            raise RuntimeError(f"{root / DD_REL}: broad Phase 193 RSCC spam remains")


def apply(arguments: list[str]) -> Path:
    root = locate_root(arguments)
    validate_inherited_component_trace(root)

    recorder_path = root / RECORDER_REL
    original = recorder_path.read_text(encoding="utf-8")
    patched = patch_recorder(original, str(recorder_path))
    recorder_path.write_text(patched, encoding="utf-8")

    for token in (
        PHASE234_MARKER,
        PHASE235_MARKER,
        PHASE235_BOOT,
        'strncmp(fmt, "DRMCOMP", 7)',
        'strncmp(fmt, "COMP ", 5)',
        'strncmp(fmt, "RSCC", 4)',
    ):
        if token not in patched:
            raise RuntimeError(f"{recorder_path}: missing Phase 235 token: {token}")
    if PHASE234_BOOT in patched:
        raise RuntimeError(f"{recorder_path}: stale Phase 234 boot identity remains")

    print(
        "Phase 235 RSCC component-master recorder applied: R48/RS48/CRC32C "
        "transport unchanged; persisted classes are RSCC, DRMCOMP, bounded COMP, "
        "and recorder control",
        flush=True,
    )
    return root


def self_test() -> None:
    fixture = f'''void a52_ackfr_record(const char *fmt, ...)\n{{\n\tu64 seq;\n\n\t/* {PHASE234_MARKER}\n\t * inherited focus\n\t */\n{PHASE234_FILTER}\n\n\tseq = 1;\n}}\na52_ackfr_record("{PHASE234_BOOT}", roots);\n'''
    patched = patch_recorder(fixture, "phase235-recorder-fixture")
    if patch_recorder(patched, "phase235-recorder-idempotence") != patched:
        raise AssertionError("Phase 235 recorder patch is not idempotent")
    for token in (
        PHASE235_MARKER,
        PHASE235_FILTER,
        PHASE235_BOOT,
        'strncmp(fmt, "DRMCOMP", 7)',
        'strncmp(fmt, "COMP ", 5)',
    ):
        if token not in patched:
            raise AssertionError(f"Phase 235 self-test missing token: {token}")
    if PHASE234_BOOT in patched:
        raise AssertionError("Phase 235 self-test retained stale Phase 234 boot marker")
    print("Phase 235 RSCC component-master overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
