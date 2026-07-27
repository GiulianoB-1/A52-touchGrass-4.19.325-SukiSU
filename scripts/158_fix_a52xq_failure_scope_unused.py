#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

HEADER_REL = Path("include/linux/a52_ack_secure_flight_recorder.h")
REPORT = "phase26-a52-failure-scope-unused-fix-report.json"
OLD = "struct a52_ackfr_scope __a52_ackfr_scope \\\n"
NEW = "struct a52_ackfr_scope __a52_ackfr_scope __maybe_unused \\\n"


def patch_header(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if NEW in text:
        if text.count(NEW) != 1:
            raise SystemExit(f"fixed scope declaration count mismatch: {text.count(NEW)}")
        return "already-present"
    count = text.count(OLD)
    if count != 1:
        raise SystemExit(f"scope declaration anchor mismatch: expected 1, found {count}")
    text = text.replace(OLD, NEW, 1)
    path.write_text(text, encoding="utf-8")
    return "inserted"


def self_test() -> None:
    sample = (
        "#define A52_ACKFR_SCOPE(domain, name) \\\n"
        "\tstruct a52_ackfr_scope __a52_ackfr_scope \\\n"
        "\t__attribute__((cleanup(a52_ackfr_scope_cleanup))) = value\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "header.h"
        path.write_text(sample, encoding="utf-8")
        first = patch_header(path)
        second = patch_header(path)
        text = path.read_text(encoding="utf-8")
        if first != "inserted" or second != "already-present":
            raise SystemExit("scope unused fix is not idempotent")
        if NEW not in text or OLD in text:
            raise SystemExit("scope unused fix self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    header = root / HEADER_REL
    if not header.is_file():
        raise SystemExit(f"missing generated recorder header: {header}")

    state = patch_header(header)
    text = header.read_text(encoding="utf-8", errors="replace")
    checks = {
        "maybe_unused_present": NEW in text,
        "unfixed_declaration_absent": OLD not in text,
        "cleanup_attribute_retained": "cleanup(a52_ackfr_scope_cleanup)" in text,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("scope unused fix audit failed: " + ", ".join(failed))

    report = {
        "status": "a52-failure-scope-unused-warning-fixed",
        "hardware_validated": False,
        "source": str(HEADER_REL),
        "state": state,
        "checks": checks,
        "reason": (
            "Clang treats the cleanup-scoped local as unused even though its cleanup "
            "function runs at scope exit; mark the generated local __maybe_unused"
        ),
    }
    (output / REPORT).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
