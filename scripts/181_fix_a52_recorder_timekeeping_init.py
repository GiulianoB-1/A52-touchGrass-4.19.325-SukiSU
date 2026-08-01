#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

CI = Path("scripts/178_ci_display_init_fec.sh")
MAIN_REL = Path("init/main.c")
REPORT = "phase37-a52-recorder-timekeeping-fix.json"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_main(text: str) -> str:
    old_early = '''\tif (a52_persistent_diag_init())
\t\tpr_err("A52 early persistent diagnostic initialization failed\\n");
\telse
\t\ta52_ackfr_record("BOOT phase=mm_init");
'''
    new_early = '''\tif (a52_persistent_diag_init())
\t\tpr_err("A52 early persistent diagnostic initialization failed\\n");
'''
    text = replace_once(
        text,
        old_early,
        new_early,
        "remove pre-timekeeping full recorder event",
    )

    timekeeping = "\ttimekeeping_init();\n"
    timekeeping_new = (
        timekeeping
        + '\ta52_ackfr_record("BOOT phase=timekeeping_init");\n'
    )
    text = replace_once(
        text,
        timekeeping,
        timekeeping_new,
        "post-timekeeping recorder event",
    )

    if 'a52_ackfr_record("BOOT phase=mm_init")' in text:
        raise SystemExit("pre-timekeeping full recorder event remains")
    if text.count('a52_ackfr_record("BOOT phase=timekeeping_init")') != 1:
        raise SystemExit("post-timekeeping recorder event count is not one")
    if text.index("timekeeping_init();") > text.index(
        'a52_ackfr_record("BOOT phase=timekeeping_init")'
    ):
        raise SystemExit("post-timekeeping recorder event ordering is invalid")
    return text


def patch_ci(text: str) -> str:
    text = replace_once(
        text,
        'make -k -C gki/common O="$BUILD"',
        'make -C gki/common O="$BUILD"',
        "fail-fast kernel make",
    )

    config_marker = (
        "grep -Fxq 'CONFIG_REED_SOLOMON_DEC8=y' \"$BUILD/.config\"\n\n"
        "set +e\n"
    )
    preflight = """grep -Fxq 'CONFIG_REED_SOLOMON_DEC8=y' "$BUILD/.config"

make -C gki/common O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \\
  KCFLAGS=-Wno-error=frame-larger-than \\
  fs/pstore/ram.o \\
  drivers/a52_secure/a52_ack_secure_flight_recorder.o \\
  > "$OUT/logs/recorder-object-preflight.log" 2>&1
grep -Fq 'CC      fs/pstore/ram.o' \\
  "$OUT/logs/recorder-object-preflight.log"
grep -Fq 'CC      drivers/a52_secure/a52_ack_secure_flight_recorder.o' \\
  "$OUT/logs/recorder-object-preflight.log"

set +e
"""
    text = replace_once(
        text, config_marker, preflight, "object preflight insertion"
    )

    old_audit = '  grep -Fq "$object" "$OUT/logs/compile.log"\ndone\n'
    new_audit = '''  if ! grep -Fq "$object" "$OUT/logs/compile.log"; then
  grep -Fq "$object" "$OUT/logs/recorder-object-preflight.log"
fi
done
'''
    text = replace_once(
        text, old_audit, new_audit, "object audit fallback"
    )

    apply_marker = '''python3 scripts/178_apply_a52_display_init_recorder_fec.py \\
  --gki gki/common --output "$OUT/stage" \\
  2>&1 | tee "$OUT/logs/single-map-stage.log"
'''
    apply_new = apply_marker + '''python3 scripts/181_fix_a52_recorder_timekeeping_init.py \\
  --gki gki/common --output "$OUT/stage" \\
  2>&1 | tee "$OUT/logs/timekeeping-fix-stage.log"
'''
    text = replace_once(
        text, apply_marker, apply_new, "timekeeping fix execution"
    )

    old_grep = (
        "grep -Fq 'a52_ackfr_record(\"BOOT phase=mm_init\")' \"$MAIN\"\n"
    )
    new_grep = (
        "! grep -Fq 'a52_ackfr_record(\"BOOT phase=mm_init\")' \"$MAIN\"\n"
        "grep -Fq 'a52_ackfr_record(\"BOOT phase=timekeeping_init\")' \"$MAIN\"\n"
    )
    text = replace_once(
        text, old_grep, new_grep, "timekeeping source audit"
    )

    image_marker = "  'BOOT recorder=v3 profile=%s copies=3 rs=32 crc32c=1 slots=1023' \\\n"
    image_new = image_marker + "  'BOOT phase=timekeeping_init' \\\n"
    text = replace_once(
        text, image_marker, image_new, "compiled Image timekeeping marker"
    )

    return text


def run(gki: Path, output: Path) -> dict[str, object]:
    main = gki / MAIN_REL
    if not main.is_file():
        raise SystemExit(f"missing generated kernel source: {main}")
    main.write_text(patch_main(main.read_text()), encoding="utf-8")
    report = {
        "status": "a52-recorder-timekeeping-order-fixed",
        "hardware_validated": False,
        "capture": "A52_RAW_RAMOOPS_20260801_115124",
        "capture_status": "0x000000FF",
        "finding": (
            "sequence allocation completed but the checkpoint after "
            "ktime_get_ns did not execute; the full recorder was called "
            "after mm_init before timekeeping_init"
        ),
        "mapping_stage": "after-mm_init",
        "first_full_record_stage": "immediately-after-timekeeping_init",
        "removed_event": "BOOT phase=mm_init",
        "added_event": "BOOT phase=timekeeping_init",
        "files": [str(MAIN_REL)],
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT).write_text(json.dumps(report, indent=2) + "\n")
    return report


def self_test() -> None:
    main_source = '''void start_kernel(void)
{
\tmm_init();
\tif (a52_persistent_diag_init())
\t\tpr_err("A52 early persistent diagnostic initialization failed\\n");
\telse
\t\ta52_ackfr_record("BOOT phase=mm_init");
\tsoftirq_init();
\ttimekeeping_init();
\tkfence_init();
}
'''
    patched = patch_main(main_source)
    assert 'BOOT phase=mm_init' not in patched
    assert patched.index('timekeeping_init();') < patched.index(
        'BOOT phase=timekeeping_init'
    )

    with tempfile.TemporaryDirectory(prefix="a52-timekeeping-") as td:
        root = Path(td)
        main = root / MAIN_REL
        main.parent.mkdir(parents=True)
        main.write_text(main_source)
        report = run(root, root / "out")
        assert report["capture_status"] == "0x000000FF"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare-ci", action="store_true")
    ap.add_argument("--gki", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        print(json.dumps({"status": "self-test-passed"}))
        return 0
    if args.prepare_ci:
        CI.write_text(patch_ci(CI.read_text()), encoding="utf-8")
        print("timekeeping-fix CI preparation applied")
        return 0
    if args.gki is None or args.output is None:
        ap.error("use --prepare-ci, --self-test, or provide --gki and --output")
    print(json.dumps(run(args.gki.resolve(), args.output.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
