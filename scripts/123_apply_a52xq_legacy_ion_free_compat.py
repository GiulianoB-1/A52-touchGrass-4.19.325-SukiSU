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


def replace_or_verify(
    path: Path,
    old: str,
    new: str,
    expected: int,
    label: str,
) -> int:
    if not path.is_file():
        raise SystemExit(f"{label}: missing generated source: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    new_count = text.count(new)
    if new_count:
        if new_count != expected:
            raise SystemExit(
                f"{label}: expected {expected} existing fixes, found {new_count}"
            )
        return 0
    old_count = text.count(old)
    if old_count != expected:
        raise SystemExit(
            f"{label}: expected {expected} unfixed anchors, found {old_count}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")
    return old_count


def apply_post_cleanup_compile_fixes(root: Path) -> dict[str, object]:
    fixes: list[dict[str, object]] = []
    specifications = (
        (
            Path("drivers/regulator/a52-legacy-gdsc-regulator.c"),
            (
                "static int a52_legacy_gdsc_disable(struct regulator_dev *rdev)\n"
                "{\n"
                "\tstruct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);\n"
                "\tu32 val = readl_relaxed(gdsc->gdscr);"
            ),
            (
                "static int a52_legacy_gdsc_disable(struct regulator_dev *rdev)\n"
                "{\n"
                "\tstruct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);\n"
                "\tu32 val __maybe_unused = readl_relaxed(gdsc->gdscr);"
            ),
            1,
            "legacy GDSC disable status local",
        ),
        (
            Path("drivers/base/dd.c"),
            "const char *reason =",
            "const char *reason __maybe_unused =",
            1,
            "fw_devlink diagnostic reason local",
        ),
        (
            Path("drivers/scsi/ufs/a52-ufs-live-trace.c"),
            "static int a52_prop_len(struct device_node *np, const char *name)",
            "static int __maybe_unused a52_prop_len(struct device_node *np, const char *name)",
            1,
            "UFS property helper after breadcrumb cleanup",
        ),
        (
            Path("drivers/scsi/ufs/a52-ufs-live-trace.c"),
            'const char *driver = dev->driver ? dev->driver->name : "<unbound>";',
            'const char *driver __maybe_unused = dev->driver ? dev->driver->name : "<unbound>";',
            2,
            "UFS live-trace callback driver locals",
        ),
        (
            Path("drivers/scsi/ufs/a52-ufs-live-trace.c"),
            "const char *type =",
            "const char *type __maybe_unused =",
            1,
            "UFS live-trace type local",
        ),
        (
            Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c"),
            '#define pr_fmt(fmt) "A52ACKFR: " fmt\n',
            '#undef pr_fmt\n#define pr_fmt(fmt) "A52ACKFR: " fmt\n',
            1,
            "ACK recorder pr_fmt reset",
        ),
        (
            Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c"),
            (
                "static int __init a52_ackfr_init(void)\n"
                "{\n"
                "\tschedule_delayed_work(&a52_ackfr_dump_work, msecs_to_jiffies(12000));"
            ),
            (
                "static int __init a52_ackfr_init(void)\n"
                "{\n"
                '\tpr_info("A52 ACK 5.10 secure-startup flight recorder enabled\\n");\n'
                "\tschedule_delayed_work(&a52_ackfr_dump_work, msecs_to_jiffies(12000));"
            ),
            1,
            "ACK recorder retained build identifier",
        ),
    )
    for relative, old, new, expected, label in specifications:
        changed = replace_or_verify(root / relative, old, new, expected, label)
        fixes.append(
            {
                "path": str(relative),
                "label": label,
                "expected": expected,
                "changed": changed,
            }
        )
    return {
        "status": "post-cleanup-compile-fixes-applied",
        "changed_total": sum(int(item["changed"]) for item in fixes),
        "fixes": fixes,
    }


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
        second = normalize_verbose_macro_calls(root)
        if first["replacements"] != 1 or second["replacements"] != 0:
            raise SystemExit("A52_VERBOSE macro normalization self-test failed")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        samples = {
            "drivers/regulator/a52-legacy-gdsc-regulator.c": (
                "static int a52_legacy_gdsc_is_enabled(struct regulator_dev *rdev)\n"
                "{\n"
                "\tstruct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);\n"
                "\tu32 val = readl_relaxed(gdsc->gdscr);\n"
                "\treturn !!val;\n"
                "}\n\n"
                "static int a52_legacy_gdsc_disable(struct regulator_dev *rdev)\n"
                "{\n"
                "\tstruct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);\n"
                "\tu32 val = readl_relaxed(gdsc->gdscr);\n"
                "\treturn 0;\n"
                "}\n"
            ),
            "drivers/base/dd.c": "const char *reason = reason_source;\n",
            "drivers/scsi/ufs/a52-ufs-live-trace.c": (
                "static int a52_prop_len(struct device_node *np, const char *name)\n"
                "{\n\treturn 0;\n}\n"
                'const char *driver = dev->driver ? dev->driver->name : "<unbound>";\n'
                "const char *type = kind;\n"
                'const char *driver = dev->driver ? dev->driver->name : "<unbound>";\n'
            ),
            "drivers/a52_secure/a52_ack_secure_flight_recorder.c": (
                '#define pr_fmt(fmt) "A52ACKFR: " fmt\n'
                "static int __init a52_ackfr_init(void)\n"
                "{\n"
                "\tschedule_delayed_work(&a52_ackfr_dump_work, msecs_to_jiffies(12000));\n"
                "\treturn 0;\n"
                "}\n"
            ),
        }
        for relative, content in samples.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        first = apply_post_cleanup_compile_fixes(root)
        second = apply_post_cleanup_compile_fixes(root)
        if first["changed_total"] != 8 or second["changed_total"] != 0:
            raise SystemExit("post-cleanup compile-fix self-test failed")


def run_stage_script(name: str, gki: Path, out: Path) -> None:
    script = Path(__file__).with_name(name)
    if not script.is_file():
        raise SystemExit(f"missing staging script: {script}")
    subprocess.run(
        [sys.executable, str(script), "--gki", str(gki), "--output", str(out)],
        check=True,
    )


def audit_reference_ion(gki: Path) -> dict[str, bool]:
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
    return checks


def add_recorder_export_header(gki: Path) -> str:
    source = gki / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
    if not source.is_file():
        raise SystemExit("generated ACK recorder source is missing")
    recorder = source.read_text(encoding="utf-8", errors="replace")
    export_include = "#include <linux/export.h>\n"
    if export_include not in recorder:
        anchor = "#include <linux/atomic.h>\n"
        if anchor not in recorder:
            raise SystemExit("generated ACK recorder include anchor is missing")
        recorder = recorder.replace(anchor, anchor + export_include, 1)
        source.write_text(recorder, encoding="utf-8")
    return source.read_text(encoding="utf-8", errors="replace")


def verify_final_stage(gki: Path, out: Path, recorder: str) -> dict[str, object]:
    required_reports = (
        "phase16-ack-secure-flight-recorder-report.json",
        "phase18-ack-secure-parameter-probe-report.json",
    )
    missing_reports = [name for name in required_reports if not (out / name).is_file()]
    if missing_reports:
        raise SystemExit("missing ACK recorder reports: " + ", ".join(missing_reports))

    recorder_checks = {
        "export_header": "#include <linux/export.h>\n" in recorder,
        "pr_fmt_reset": "#undef pr_fmt\n#define pr_fmt" in recorder,
        "build_identifier": (
            'pr_info("A52 ACK 5.10 secure-startup flight recorder enabled\\n");'
            in recorder
        ),
        "export_declaration": "EXPORT_SYMBOL_GPL(a52_ackfr_record);" in recorder,
    }
    ion = (gki / "drivers/staging/android/ion/ion.c").read_text(
        encoding="utf-8", errors="replace"
    )
    qsee = (gki / "drivers/a52_secure/qseecom.c").read_text(
        encoding="utf-8", errors="replace"
    )
    parameter_checks = {
        "ion_allocation_result": (
            "ION result fd=%d len=%llu heap=%x flags=%x" in ion
        ),
        "qsee_send_api": "QSEE SEND api req=%u rsp=%u" in qsee,
        "qsee_send_core": (
            "QSEE SEND core id=%u app=%s req=%u rsp=%u" in qsee
        ),
    }
    failed = [
        name
        for name, passed in {**recorder_checks, **parameter_checks}.items()
        if not passed
    ]
    if failed:
        raise SystemExit("ACK final staging audit failed: " + ", ".join(failed))
    phase17 = json.loads(
        (out / "phase18-ack-secure-parameter-probe-report.json").read_text()
    )
    if phase17.get("payload_capture") is not False:
        raise SystemExit("ACK parameter probe must remain metadata-only")
    return {
        "recorder": recorder_checks,
        "parameter_probe": parameter_checks,
        "payload_capture": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    gki = args.gki.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    checks = audit_reference_ion(gki)
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
    report_path = out / "phase15-legacy-ion-free-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    run_stage_script("124_apply_a52xq_ion_qsee_runtime_trace.py", gki, out)
    run_stage_script("141_apply_a52xq_ack_secure_parameter_probe.py", gki, out)

    report["post_cleanup_compile_fixes"] = apply_post_cleanup_compile_fixes(gki)
    recorder = add_recorder_export_header(gki)
    report["final_stage_audit"] = verify_final_stage(gki, out, recorder)
    report["next_probe"] = json.loads(
        (out / "phase18-ack-secure-parameter-probe-report.json").read_text()
    )
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
