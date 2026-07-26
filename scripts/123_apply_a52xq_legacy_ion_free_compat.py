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
        patched = path.read_text(encoding="utf-8")
        second = normalize_verbose_macro_calls(root)
        if first["replacements"] != 1 or "##__VA_ARGS__);" not in patched:
            raise SystemExit("A52_VERBOSE macro normalization self-test failed")
        if second["replacements"] != 0:
            raise SystemExit("A52_VERBOSE macro normalization is not idempotent")

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
                "{\n"
                "\treturn 0;\n"
                "}\n"
                'const char *driver = dev->driver ? dev->driver->name : "<unbound>";\n'
                "const char *type = kind;\n"
                'const char *driver = dev->driver ? dev->driver->name : "<unbound>";\n'
                'const char *driver = "<unbound>";\n'
            ),
            "drivers/a52_secure/a52_ack_secure_flight_recorder.c": (
                '#define pr_fmt(fmt) "A52ACKFR: " fmt\n'
            ),
        }
        for relative, content in samples.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        first = apply_post_cleanup_compile_fixes(root)
        second = apply_post_cleanup_compile_fixes(root)
        if first["changed_total"] != 7:
            raise SystemExit("post-cleanup compile-fix self-test changed wrong count")
        if second["changed_total"] != 0:
            raise SystemExit("post-cleanup compile fixes are not idempotent")
        gdsc = (root / "drivers/regulator/a52-legacy-gdsc-regulator.c").read_text()
        if gdsc.count("u32 val = readl_relaxed(gdsc->gdscr);") != 1:
            raise SystemExit("used GDSC is_enabled status local was not preserved")
        if gdsc.count("u32 val __maybe_unused = readl_relaxed(gdsc->gdscr);") != 1:
            raise SystemExit("GDSC disable status local was not repaired exactly once")
        ufs = (root / "drivers/scsi/ufs/a52-ufs-live-trace.c").read_text()
        if ufs.count("static int __maybe_unused a52_prop_len(") != 1:
            raise SystemExit("cleaned UFS property helper was not repaired exactly once")
        if "static int a52_prop_len(" in ufs:
            raise SystemExit("unfixed cleaned UFS property helper remains")


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
    report_path = out / "phase15-legacy-ion-free-report.json"
    report_path.write_text(
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

    compile_fixes = apply_post_cleanup_compile_fixes(gki)
    report["post_cleanup_compile_fixes"] = compile_fixes
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
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
    if "#undef pr_fmt\n#define pr_fmt" not in final_recorder:
        raise SystemExit("generated ACK recorder pr_fmt reset is missing")
    if "EXPORT_SYMBOL_GPL(a52_ackfr_record);" not in final_recorder:
        raise SystemExit("generated ACK recorder export declaration is missing")

    trace_report = out / "phase16-ack-secure-flight-recorder-report.json"
    if not trace_report.is_file():
        raise SystemExit("ACK secure flight-recorder report was not generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
