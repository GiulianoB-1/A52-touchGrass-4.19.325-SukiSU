#!/usr/bin/env python3
"""Validate the A52 REFGEN candidate and returned hardware-test captures.

This tool is deliberately non-destructive. It never flashes, modifies, deletes,
or writes to the phone. It verifies local hashes, sizes, collector completeness,
and the collector SHA256SUMS file before postmortem analysis.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

EXPECTED_BOOT_SHA256 = "d7959b3c917a22966d39b12e799ca1cee10bcb090632ec9ab8930d716166809a"
EXPECTED_BOOT_BYTES = 100_663_296
EXPECTED_RAMOOPS_BYTES = 1_048_576
REQUIRED_CAPTURE_FILES = (
    "ramoops-raw-1MiB.bin",
    "exporter-status.txt",
    "recovery-identity.txt",
    "recovery-dmesg.txt",
    "pstore-list.txt",
    "SHA256SUMS.txt",
)
REQUIRED_KIT_FILES = (
    "package/boot.img",
    "tools/diagnose-a52-refgen-display.py",
    "tools/decode-a52-unified-secure-recorder.py",
    "tools/decode-a52-mirrored-ramoops-v2.py",
    "tools/collect_a52_raw_ramoops_exporter_FIXED.bat",
)
SHA_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_unique(root: Path, name: str) -> Path | None:
    matches = [item for item in root.rglob(name) if item.is_file()]
    return matches[0] if len(matches) == 1 else None


def candidate_root(path: Path) -> tuple[Path, Path]:
    resolved = path.resolve()
    if resolved.is_file():
        if resolved.name.lower() != "boot.img":
            raise ValueError("candidate input must be boot.img or an extracted kit directory")
        return resolved.parent.parent, resolved
    if not resolved.is_dir():
        raise ValueError(f"candidate path does not exist: {resolved}")
    direct = resolved / "package" / "boot.img"
    if direct.is_file():
        return resolved, direct
    boot = find_unique(resolved, "boot.img")
    if boot is None:
        raise ValueError("could not locate one unique package/boot.img")
    return boot.parent.parent, boot


def validate_candidate(path: Path) -> dict[str, object]:
    root, boot = candidate_root(path)
    checks: list[Check] = []
    boot_size = boot.stat().st_size
    boot_hash = sha256(boot)
    checks.append(Check("boot_size", boot_size == EXPECTED_BOOT_BYTES,
                        f"{boot_size} bytes, expected {EXPECTED_BOOT_BYTES}"))
    checks.append(Check("boot_sha256", boot_hash == EXPECTED_BOOT_SHA256,
                        f"{boot_hash}, expected {EXPECTED_BOOT_SHA256}"))
    for relative in REQUIRED_KIT_FILES:
        item = root / relative
        checks.append(Check(f"required:{relative}", item.is_file(), str(item)))
    manifest = root / "SHA256SUMS"
    if manifest.is_file():
        manifest_result = verify_manifest(root, manifest)
        checks.append(Check("kit_manifest", manifest_result["passed"],
                            f"verified={manifest_result['verified']} errors={len(manifest_result['errors'])}"))
    else:
        checks.append(Check("kit_manifest", False, "SHA256SUMS is missing"))
    passed = all(check.passed for check in checks)
    return {
        "status": "candidate-valid" if passed else "candidate-invalid",
        "non_destructive": True,
        "kit_root": str(root),
        "boot_img": str(boot),
        "boot_bytes": boot_size,
        "boot_sha256": boot_hash,
        "checks": [asdict(check) for check in checks],
        "passed": passed,
    }


def safe_extract(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise ValueError(f"unsafe ZIP member: {info.filename}")
        archive.extractall(destination)


def verify_manifest(root: Path, manifest: Path) -> dict[str, object]:
    verified = 0
    errors: list[str] = []
    for line_number, raw_line in enumerate(manifest.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = SHA_LINE.match(line)
        if not match:
            errors.append(f"line {line_number}: unrecognised checksum format")
            continue
        expected, relative = match.groups()
        relative = relative.replace("\\", "/")
        item = (root / relative).resolve()
        try:
            item.relative_to(root.resolve())
        except ValueError:
            errors.append(f"line {line_number}: path escapes capture root: {relative}")
            continue
        if not item.is_file():
            errors.append(f"line {line_number}: missing file: {relative}")
            continue
        actual = sha256(item)
        if actual.lower() != expected.lower():
            errors.append(f"line {line_number}: checksum mismatch: {relative}")
            continue
        verified += 1
    return {"passed": not errors and verified > 0, "verified": verified, "errors": errors}


def choose_capture_root(path: Path, temporary: Path) -> tuple[Path, str]:
    resolved = path.resolve()
    if resolved.is_dir():
        return resolved, "directory"
    if resolved.is_file() and resolved.suffix.lower() == ".zip":
        safe_extract(resolved, temporary)
        raw = find_unique(temporary, "ramoops-raw-1MiB.bin")
        if raw is None:
            raise ValueError("ZIP must contain exactly one ramoops-raw-1MiB.bin")
        return raw.parent, "zip"
    if resolved.is_file() and resolved.name == "ramoops-raw-1MiB.bin":
        return resolved.parent, "raw"
    raise ValueError("capture input must be a collector ZIP, collector directory, or ramoops-raw-1MiB.bin")


def validate_capture(path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="a52-refgen-intake-") as temporary_name:
        temporary = Path(temporary_name)
        root, input_type = choose_capture_root(path, temporary)
        checks: list[Check] = []
        locations: dict[str, str | None] = {}
        complete_evidence = True
        for name in REQUIRED_CAPTURE_FILES:
            item = root / name
            present = item.is_file()
            locations[name] = str(item) if present else None
            required_now = input_type != "raw" or name == "ramoops-raw-1MiB.bin"
            if not present:
                complete_evidence = False
            checks.append(Check(f"required:{name}", present or not required_now,
                                str(item) if present else
                                ("optional for raw-only intake" if not required_now else str(item))))
        raw = root / "ramoops-raw-1MiB.bin"
        raw_bytes = raw.stat().st_size if raw.is_file() else None
        raw_hash = sha256(raw) if raw.is_file() else None
        checks.append(Check("ramoops_size", raw_bytes == EXPECTED_RAMOOPS_BYTES,
                            f"{raw_bytes} bytes, expected {EXPECTED_RAMOOPS_BYTES}"))
        manifest = root / "SHA256SUMS.txt"
        if manifest.is_file():
            manifest_result = verify_manifest(root, manifest)
            checks.append(Check("collector_manifest", manifest_result["passed"],
                                f"verified={manifest_result['verified']} errors={len(manifest_result['errors'])}"))
        elif input_type == "raw":
            manifest_result = {"passed": False, "verified": 0, "errors": ["SHA256SUMS.txt unavailable in raw-only intake"]}
            checks.append(Check("collector_manifest", True, "optional for raw-only intake"))
        else:
            manifest_result = {"passed": False, "verified": 0, "errors": ["SHA256SUMS.txt missing"]}
            checks.append(Check("collector_manifest", False, "SHA256SUMS.txt missing"))
        exporter = root / "exporter-status.txt"
        exporter_text = exporter.read_text(encoding="utf-8", errors="replace") if exporter.is_file() else ""
        exporter_mentions_device = "/dev/a52_ramoops_raw" in exporter_text
        checks.append(Check("exporter_identity", exporter_mentions_device or input_type == "raw",
                            "exporter-status.txt references /dev/a52_ramoops_raw" if exporter_mentions_device
                            else ("optional for raw-only intake" if input_type == "raw"
                                  else "expected exporter device was not found in exporter-status.txt")))
        passed = all(check.passed for check in checks)
        return {
            "status": "capture-valid" if passed else "capture-invalid",
            "non_destructive": True,
            "input_type": input_type,
            "capture_root": str(root),
            "ramoops_bytes": raw_bytes,
            "ramoops_sha256": raw_hash,
            "files": locations,
            "manifest": manifest_result,
            "complete_evidence": complete_evidence,
            "warnings": ([] if complete_evidence else
                         ["Only the raw RAMOOPS image was validated; recovery identity and collector context are incomplete."]),
            "checks": [asdict(check) for check in checks],
            "passed": passed,
        }


def write_report(report: dict[str, object], output: Path | None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")


def run_analysis(capture: Path, screen: str, tools: Path, output: Path) -> int:
    analyzer = tools / "diagnose-a52-refgen-display.py"
    if not analyzer.is_file():
        print(f"ERROR: analyzer missing: {analyzer}", file=sys.stderr)
        return 4
    command = [sys.executable, str(analyzer), str(capture), "--screen-result", screen, "--output", str(output)]
    return subprocess.run(command, check=False).returncode


def write_test_manifest(root: Path, names: tuple[str, ...]) -> None:
    lines = [f"{sha256(root / name)}  {name}" for name in names]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def self_test() -> dict[str, object]:
    global EXPECTED_BOOT_SHA256, EXPECTED_BOOT_BYTES
    original_hash, original_bytes = EXPECTED_BOOT_SHA256, EXPECTED_BOOT_BYTES
    results: list[Check] = []
    try:
        with tempfile.TemporaryDirectory(prefix="a52-refgen-validator-test-") as name:
            root = Path(name)
            kit = root / "kit"
            for relative in REQUIRED_KIT_FILES:
                item = kit / relative
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_bytes(b"candidate" if relative == "package/boot.img" else b"tool")
            EXPECTED_BOOT_BYTES = (kit / "package/boot.img").stat().st_size
            EXPECTED_BOOT_SHA256 = sha256(kit / "package/boot.img")
            manifest_names = tuple(str(item.relative_to(kit)).replace("\\", "/")
                                   for item in kit.rglob("*") if item.is_file())
            lines = [f"{sha256(kit / relative)}  ./{relative}" for relative in manifest_names]
            (kit / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
            candidate_good = validate_candidate(kit)
            results.append(Check("candidate_positive", bool(candidate_good["passed"]),
                                 str(candidate_good["status"])))
            (kit / "package/boot.img").write_bytes(b"corrupt")
            candidate_bad = validate_candidate(kit)
            results.append(Check("candidate_corruption", not bool(candidate_bad["passed"]),
                                 str(candidate_bad["status"])))

            capture = root / "capture"
            capture.mkdir()
            (capture / "ramoops-raw-1MiB.bin").write_bytes(b"\0" * EXPECTED_RAMOOPS_BYTES)
            (capture / "exporter-status.txt").write_text("/dev/a52_ramoops_raw readable\n", encoding="utf-8")
            for filename in REQUIRED_CAPTURE_FILES[2:-1]:
                (capture / filename).write_text(filename + "\n", encoding="utf-8")
            write_test_manifest(capture, REQUIRED_CAPTURE_FILES[:-1])
            capture_good = validate_capture(capture)
            results.append(Check("capture_directory", bool(capture_good["passed"]),
                                 str(capture_good["status"])))

            archive = root / "capture.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
                for item in capture.iterdir():
                    handle.write(item, item.name)
            capture_zip = validate_capture(archive)
            results.append(Check("capture_zip", bool(capture_zip["passed"]),
                                 str(capture_zip["status"])))

            raw_only = root / "raw-only"
            raw_only.mkdir()
            (raw_only / "ramoops-raw-1MiB.bin").write_bytes(b"\0" * EXPECTED_RAMOOPS_BYTES)
            raw_report = validate_capture(raw_only / "ramoops-raw-1MiB.bin")
            results.append(Check("capture_raw_only", bool(raw_report["passed"]) and
                                 not bool(raw_report["complete_evidence"]),
                                 str(raw_report["status"])))

            (capture / "ramoops-raw-1MiB.bin").write_bytes(b"bad")
            capture_bad = validate_capture(capture)
            results.append(Check("capture_corruption", not bool(capture_bad["passed"]),
                                 str(capture_bad["status"])))

            malicious = root / "malicious.zip"
            with zipfile.ZipFile(malicious, "w") as handle:
                handle.writestr("../escape.txt", "bad")
            try:
                validate_capture(malicious)
            except ValueError:
                traversal_rejected = True
            else:
                traversal_rejected = False
            results.append(Check("zip_traversal_rejected", traversal_rejected,
                                 "unsafe member rejected" if traversal_rejected else "unsafe member accepted"))
    finally:
        EXPECTED_BOOT_SHA256, EXPECTED_BOOT_BYTES = original_hash, original_bytes
    passed = all(item.passed for item in results)
    return {
        "status": "self-test-passed" if passed else "self-test-failed",
        "passed": passed,
        "checks": [asdict(item) for item in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("self-test", help="run isolated candidate, capture, corruption, and ZIP-safety tests")

    candidate_parser = subparsers.add_parser("candidate", help="verify the local REFGEN boot candidate and kit")
    candidate_parser.add_argument("path", type=Path, help="boot.img or extracted kit directory")
    candidate_parser.add_argument("--report", type=Path)

    capture_parser = subparsers.add_parser("capture", help="verify a returned collector ZIP, directory, or raw image")
    capture_parser.add_argument("path", type=Path)
    capture_parser.add_argument("--report", type=Path)
    capture_parser.add_argument("--analyse", choices=("unknown", "stable", "black"))
    capture_parser.add_argument("--tools", type=Path, default=Path(__file__).resolve().parent)
    capture_parser.add_argument("--analysis-output", type=Path, default=Path("a52-refgen-display-diagnosis"))

    args = parser.parse_args()
    try:
        if args.command == "self-test":
            report = self_test()
            write_report(report, None)
            return 0 if report["passed"] else 1
        if args.command == "candidate":
            report = validate_candidate(args.path)
            write_report(report, args.report)
            return 0 if report["passed"] else 1
        report = validate_capture(args.path)
        write_report(report, args.report)
        if not report["passed"]:
            return 1
        if args.analyse:
            return run_analysis(args.path.resolve(), args.analyse, args.tools.resolve(), args.analysis_output.resolve())
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "error", "passed": False, "error": str(error)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
