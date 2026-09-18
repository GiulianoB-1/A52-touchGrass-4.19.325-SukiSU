#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE363_REMOVE_60S_REBOOT_V1"
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")


def function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    start = text.find(anchor)
    if start < 0:
        raise SystemExit(f"Phase363 {label}: function anchor missing")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase363 {label}: opening brace missing")
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"Phase363 {label}: closing brace missing")


def validate(text: str) -> None:
    if MARK not in text:
        raise SystemExit("Phase363 marker missing")
    start, end = function_bounds(
        text, "static int a52_r340_thread_fn(void *unused)", "Phase340 kthread"
    )
    fn = text[start:end]
    if 'kernel_restart("recovery");' in fn:
        raise SystemExit("Phase363: Phase340 60s reboot still present")
    if 'P276 340C t=60 continue=1' not in fn:
        raise SystemExit("Phase363: continue marker missing")
    if 'P276 340K t=%u q=%llu r=%u' not in fn:
        raise SystemExit("Phase363: Phase340 checkpoints were lost")
    # The Phase339 330-second safety reboot must remain elsewhere.
    if text.count('kernel_restart("recovery");') < 1:
        raise SystemExit("Phase363: Phase339 safety reboot was removed")
    if 'P276 339R t=%u q=%llu r=%u warm=1' not in text:
        raise SystemExit("Phase363: Phase339 safety marker missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / REC
    if not path.is_file():
        raise SystemExit("Phase363 recorder source missing")
    text = path.read_text(encoding="utf-8")

    if MARK in text:
        validate(text)
        print("Phase363 remove-60s-reboot audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase363 marker missing in check-only mode")

    start, end = function_bounds(
        text, "static int a52_r340_thread_fn(void *unused)", "Phase340 kthread"
    )
    fn = text[start:end]

    old = (
        '\ta52_r340_sideband_write(A52_R340_EVT_REBOOT, 60U);\n'
        '\ta52_ackfr_record("P276 340R t=60 warm=1");\n'
        '\twmb();\n'
        '\tmsleep(250);\n'
        '\tkernel_restart("recovery");\n'
        '\treturn 0;\n'
    )
    new = (
        '\t/* ' + MARK + '\n'
        '\t * Phase362 proved UFS is no longer the boot wall. Do not terminate\n'
        '\t * the experiment at 60 s; leave the inherited Phase339 330 s safety\n'
        '\t * reboot intact so late Android/display progress can be observed.\n'
        '\t */\n'
        '\ta52_ackfr_record("P276 340C t=60 continue=1");\n'
        '\treturn 0;\n'
    )
    if fn.count(old) != 1:
        raise SystemExit(
            f"Phase363 expected exact Phase340 reboot tail once, found {fn.count(old)}"
        )
    fn = fn.replace(old, new, 1)
    text = text[:start] + fn + text[end:]

    validate(text)
    path.write_text(text, encoding="utf-8")
    print("Phase363 remove-60s-reboot applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
