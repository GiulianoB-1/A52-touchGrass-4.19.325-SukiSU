#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

AUDIO_SYSFS_REL = Path("sound/soc/samsung/sec_audio_sysfs.c")
MARKER = "A52_TOUCHGRASS_AUDIO_SYSFS_BOOT_GUARD"
PANIC = 'panic("sound card is not registered");'
REPLACEMENT = (
    f"/* {MARKER} */\n"
    '\t\tdev_warn_ratelimited(dev, "%s: callback not ready; reporting 0\\n", '
    "__func__);"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.kernel.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    path = root / AUDIO_SYSFS_REL
    if not path.is_file():
        raise SystemExit(f"missing TouchGrass audio sysfs source: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    before_count = text.count(PANIC)
    marker_count = text.count(MARKER)

    if marker_count:
        if before_count:
            raise SystemExit("audio boot guard marker exists but panic remains")
        state = "already-staged"
    else:
        if before_count != 1:
            raise SystemExit(
                f"expected exactly one audio sysfs panic, found {before_count}"
            )
        text = text.replace(PANIC, REPLACEMENT, 1)
        path.write_text(text, encoding="utf-8")
        state = "inserted"

    final = path.read_text(encoding="utf-8", errors="replace")
    if PANIC in final:
        raise SystemExit("audio sysfs panic still present after patch")
    if MARKER not in final:
        raise SystemExit("audio sysfs boot guard marker missing after patch")
    if "callback not ready; reporting 0" not in final:
        raise SystemExit("audio sysfs safe response text missing after patch")

    report = {
        "status": "touchgrass-audio-sysfs-boot-guard-staged",
        "hardware_validated": False,
        "state": state,
        "source": str(AUDIO_SYSFS_REL),
        "panic_removed": True,
        "replacement_semantics": (
            "When the Samsung audio callback has not registered yet, log a "
            "rate-limited warning and return the existing default value 0 instead "
            "of panicking the kernel. Registered callback behavior is unchanged."
        ),
        "evidence": {
            "captured_panic": "sound card is not registered",
            "captured_reader_process": "samsungpowersou",
            "captured_stack_symbol": "audio_key_state_show/audio_jack_state_show",
        },
        "marker": MARKER,
    }
    (output / "touchgrass-audio-sysfs-boot-guard-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
