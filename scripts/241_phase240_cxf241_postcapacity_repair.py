#!/usr/bin/env python3
"""Phase 241: repair CXF241 source classification and late retention.

Phase 240 hardware reached the late heartbeat but its CXF240 replay was absent.
Post-capacity persistence is gated by a52_r179_is_critical_message(), so this
diagnostic-only overlay adds CXF241 to that critical-prefix classifier.

The Phase 241 broad overlay also originally rejected every CXF241 message at the
top of a52_r241_classify(), which made its later CXF241 create-* and dreg-*
branches unreachable.  Replay recursion already has the independent
``a52_r241_replaying`` guard, so this repair removes only that blanket CXF241
classification exclusion.  It does not alter match/probe results, supplier
links, deferred-probe decisions, provider state, driver ordering, or transport.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
PHASE241_MARKER = "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1"
MARKER = "A52_PHASE241_CXF241_POSTCAPACITY_CRITICAL_V1"
CLASSIFY_MARKER = "A52_PHASE241_CXF241_SOURCE_CLASSIFICATION_V1"

FUNCTION_ANCHOR = "static bool a52_r179_is_critical_message(const char *message)\n{\n"
RETURN_OLD = '''\treturn !strncmp(message, "BOOT ", 5) ||\n'''
RETURN_NEW = '''\t/* A52_PHASE241_CXF241_POSTCAPACITY_CRITICAL_V1\n\t * Keep Phase 241 late replay visible after A52_R179_CAPACITY is exhausted.\n\t */\n\treturn !strncmp(message, "CXF241 ", 7) ||\n\t       !strncmp(message, "BOOT ", 5) ||\n'''

CLASSIFY_OLD = '''\tif (!message || !strncmp(message, "CXF241 ", 7))\n\t\treturn 0;\n'''
CLASSIFY_NEW = '''\t/* A52_PHASE241_CXF241_SOURCE_CLASSIFICATION_V1\n\t * Replay feedback is blocked by a52_r241_replaying in the latch hook.\n\t * Do not suppress source-side CXF241 create-* / dreg-* evidence here.\n\t */\n\tif (!message)\n\t\treturn 0;\n'''


def patch_recorder(text: str, label: str) -> str:
    if PHASE241_MARKER not in text:
        raise RuntimeError(f"{label}: Phase 241 broad-corridor marker missing")

    if MARKER not in text:
        if text.count(FUNCTION_ANCHOR) != 1:
            raise RuntimeError(
                f"{label}: expected one critical-message function, found "
                f"{text.count(FUNCTION_ANCHOR)}"
            )
        if text.count(RETURN_OLD) != 1:
            raise RuntimeError(
                f"{label}: expected one BOOT critical-prefix anchor, found "
                f"{text.count(RETURN_OLD)}"
            )
        text = text.replace(RETURN_OLD, RETURN_NEW, 1)

    if CLASSIFY_MARKER not in text:
        if text.count(CLASSIFY_OLD) != 1:
            raise RuntimeError(
                f"{label}: expected one blanket CXF241 classifier exclusion, found "
                f"{text.count(CLASSIFY_OLD)}"
            )
        text = text.replace(CLASSIFY_OLD, CLASSIFY_NEW, 1)

    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    required = (
        PHASE241_MARKER,
        MARKER,
        CLASSIFY_MARKER,
        FUNCTION_ANCHOR,
        'return !strncmp(message, "CXF241 ", 7) ||',
        '!strncmp(message, "BOOT ", 5) ||',
        'critical = a52_r179_is_critical_message(event.message);',
        'if (atomic_read(&a52_r241_replaying))',
        '!strncmp(message, "CXF241 create-", 15)',
        '!strncmp(message, "CXF241 dreg-", 13)',
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")

    if CLASSIFY_OLD in text:
        raise RuntimeError(f"{label}: blanket CXF241 classifier exclusion remains")

    fn_start = text.index(FUNCTION_ANCHOR)
    fn_end = text.find("\n}\n", fn_start)
    if fn_end < 0:
        raise RuntimeError(f"{label}: critical-message function is unterminated")
    body = text[fn_start:fn_end]
    critical_count = body.count('!strncmp(message, "CXF241 ", 7)')
    if critical_count != 1:
        raise RuntimeError(
            f"{label}: CXF241 critical prefix count is {critical_count}, expected 1"
        )


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        roots.extend((path, path.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    matches: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        recorder = root / RECORDER
        if not recorder.is_file():
            continue
        text = recorder.read_text(encoding="utf-8")
        if PHASE241_MARKER not in text:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            matches.append(root)
    if len(matches) != 1:
        rendered = ", ".join(str(root) for root in matches) or "none"
        raise RuntimeError(
            "expected exactly one generated Phase 241 source root, "
            f"found {len(matches)}: {rendered}"
        )
    return matches[0]


def self_test() -> None:
    fixture = (
        "/* A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1 */\n"
        + FUNCTION_ANCHOR
        + "\tif (!message)\n\t\treturn false;\n\n"
        + RETURN_OLD
        + "\t       !strncmp(message, \"HB \", 3);\n}\n\n"
        + "static unsigned int a52_r241_classify(const char *message)\n{\n"
        + CLASSIFY_OLD
        + "\tif (!strncmp(message, \"CXF241 create-\", 15))\n\t\treturn 1;\n"
        + "\tif (!strncmp(message, \"CXF241 dreg-\", 13))\n\t\treturn 2;\n"
        + "\treturn 0;\n}\n\n"
        + "static void a52_r241_corridor_latch(const char *message)\n{\n"
        + "\tif (atomic_read(&a52_r241_replaying))\n\t\treturn;\n}\n\n"
        + "void record(void)\n{\n"
        + "\tcritical = a52_r179_is_critical_message(event.message);\n}\n"
    )
    patched = patch_recorder(fixture, "fixture/recorder.c")
    if patch_recorder(patched, "fixture/recorder.c/idempotent") != patched:
        raise AssertionError("Phase 241 classification/retention repair is not idempotent")
    if 'return !strncmp(message, "CXF241 ", 7) ||' not in patched:
        raise AssertionError("CXF241 was not admitted as a critical prefix")
    if CLASSIFY_OLD in patched:
        raise AssertionError("blanket CXF241 classifier exclusion survived")

    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        root = repo / "gki/common"
        path = root / RECORDER
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fixture, encoding="utf-8")
        found = locate_generated([], cwd=repo)
        if found.resolve() != root.resolve():
            raise AssertionError(f"locator chose {found}, expected {root}")

    print(
        "Phase 241 CXF241 source classification + post-capacity retention self-test: PASS",
        flush=True,
    )


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    recorder = root / RECORDER
    recorder.write_text(
        patch_recorder(recorder.read_text(encoding="utf-8"), str(recorder)),
        encoding="utf-8",
    )
    print(
        "Phase 241 CXF241 repair applied: source create/dreg evidence classifiable; late replay critical",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 241 CXF241 classification/retention repair failed: {exc}", file=sys.stderr)
        raise
