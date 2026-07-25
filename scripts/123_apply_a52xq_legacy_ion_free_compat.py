#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

RETIRED_MARKER = "A52_LEGACY_ION_FREE_IOCTL_COMPAT"
SEMICOLONLESS_VERBOSE_MACRO = re.compile(
    r'(?m)^(?P<call>[ \t]*a52_persistent_diag_mark'
    r'\("A52VERBOSE " fmt "\\n", ##__VA_ARGS__\))(?P<trailing>[ \t]*)$'
)


def normalize_verbose_macro_calls(root: Path) -> dict[str, object]:
    changed_files: list[str] = []
    replacements = 0
    for path in sorted(root.rglob("*.[ch]")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        updated, count = SEMICOLONLESS_VERBOSE_MACRO.subn(
            lambda match: f'{match.group("call")};{match.group("trailing")}',
            text,
        )
        if not count:
            continue
        path.write_text(updated, encoding="utf-8")
        changed_files.append(str(path.relative_to(root)))
        replacements += count
    return {"replacements": replacements, "changed_files": changed_files}


def self_test() -> None:
    import tempfile

    sample = (
        '#define A52_VERBOSE(fmt, ...) \\\n'
        '\ta52_persistent_diag_mark("A52VERBOSE " fmt "\\n", ##__VA_ARGS__)\n'
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "macro.c"
        path.write_text(sample, encoding="utf-8")
        first = normalize_verbose_macro_calls(root)
        patched = path.read_text(encoding="utf-8")
        second = normalize_verbose_macro_calls(root)
        if first["replacements"] != 1 or "##__VA_ARGS__);" not in patched:
            raise SystemExit("A52_VERBOSE macro normalization self-test failed")
        if second["replacements"] != 0:
            raise SystemExit("A52_VERBOSE macro normalization is not idempotent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    gki = args.gki.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    path = gki / "drivers/staging/android/ion/ion.c"
    if not path.is_file():
        raise SystemExit(f"missing ACK ION source: {path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    checks = {
        "ack_ion_ioctl_present": "static long ion_ioctl(" in text,
        "ack_dma_buf_allocator_present": "struct dma_buf *ion_alloc(size_t len" in text,
        "fd_returning_allocation_present": (
            "case ION_IOC_ALLOC:" in text
            and "dma_buf_fd(" in text
            and "data.allocation.fd = fd" in text
        ),
        "legacy_handle_subsystem_absent": "struct ion_handle" not in text,
        "disproven_noop_absent": RETIRED_MARKER not in text,
        "unknown_ioctl_returns_enotty": "return -ENOTTY;" in text,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("ACK ION reference audit failed: " + ", ".join(failed))

    macro_normalization = normalize_verbose_macro_calls(gki)
    report = {
        "status": "legacy-ion-free-compat-retired",
        "runtime_fix": "none-reference-parity-restored",
        "changed": False,
        "flashable": False,
        "hardware_validated": False,
        "observed_ioctl": "0xc0044901",
        "working_touchgrass_result": "-ENOTTY",
        "reason": (
            "The successful TouchGrass flight-recorder boot shows that Samsung "
            "userspace tolerates ION_IOC_FREE returning -ENOTTY, then allocates "
            "through the same 24-byte fd-returning ION_IOC_ALLOC ABI. The earlier "
            "ACK no-op patch was hardware-tested and did not fix the boot."
        ),
        "checks": checks,
        "macro_normalization": macro_normalization,
    }
    (out / "phase15-legacy-ion-free-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    trace_script = Path(__file__).with_name(
        "124_apply_a52xq_ion_qsee_runtime_trace.py"
    )
    subprocess.run(
        [sys.executable, str(trace_script), "--gki", str(gki), "--output", str(out)],
        check=True,
    )

    recorder_source = gki / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
    if not recorder_source.is_file():
        raise SystemExit("generated ACK recorder source is missing")
    recorder = recorder_source.read_text(encoding="utf-8", errors="replace")
    export_include = "#include <linux/export.h>\n"
    if export_include not in recorder:
        anchor = "#include <linux/atomic.h>\n"
        if anchor not in recorder:
            raise SystemExit("generated ACK recorder include anchor is missing")
        recorder = recorder.replace(anchor, anchor + export_include, 1)
        recorder_source.write_text(recorder, encoding="utf-8")
    final_recorder = recorder_source.read_text(encoding="utf-8", errors="replace")
    if export_include not in final_recorder:
        raise SystemExit("generated ACK recorder export header was not added")
    if "EXPORT_SYMBOL_GPL(a52_ackfr_record);" not in final_recorder:
        raise SystemExit("generated ACK recorder export declaration is missing")

    trace_report = out / "phase16-ack-secure-flight-recorder-report.json"
    if not trace_report.is_file():
        raise SystemExit("ACK secure flight-recorder report was not generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
