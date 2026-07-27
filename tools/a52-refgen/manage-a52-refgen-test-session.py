#!/usr/bin/env python3
"""Bind an A52 REFGEN candidate, capture, diagnosis, and evidence ZIP together.

Local-file only and non-destructive. It never communicates with the phone.
"""
from __future__ import annotations

import argparse, hashlib, json, shutil, subprocess, sys, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "a52-refgen-test-session-v1"
BOOT_HASH = "d7959b3c917a22966d39b12e799ca1cee10bcb090632ec9ab8930d716166809a"
BOOT_BYTES = 100_663_296


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validator_path(tools: Path, override: Path | None) -> Path:
    path = override.resolve() if override else (tools / "validate-a52-refgen-hardware-inputs.py").resolve()
    if not path.is_file():
        raise ValueError(f"validator missing: {path}")
    return path


def run_validator(validator: Path, args: list[str], report: Path) -> tuple[int, dict[str, Any], str]:
    cmd = [sys.executable, str(validator), *args, "--report", str(report)]
    done = subprocess.run(cmd, text=True, capture_output=True, check=False)
    result = load(report) if report.is_file() else {"passed": False, "status": "validator-error"}
    text = "\n".join(x for x in (done.stdout.strip(), done.stderr.strip()) if x)
    return done.returncode, result, text


def unique_session(root: Path, boot_hash: str) -> tuple[str, Path]:
    base = f"A52-REFGEN-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{boot_hash[:8]}"
    for n in range(1000):
        sid = base if n == 0 else f"{base}-{n:02d}"
        path = root / sid
        try:
            path.mkdir(parents=True, exist_ok=False)
            return sid, path
        except FileExistsError:
            pass
    raise RuntimeError("could not allocate session directory")


def start(kit: Path, out: Path, tools: Path, override: Path | None) -> dict[str, Any]:
    kit, out, tools = kit.resolve(), out.resolve(), tools.resolve()
    out.mkdir(parents=True, exist_ok=True)
    validator = validator_path(tools, override)
    with tempfile.TemporaryDirectory(prefix="a52-session-start-") as td:
        temp_report = Path(td) / "candidate.json"
        rc, report, text = run_validator(validator, ["candidate", str(kit)], temp_report)
        if rc or not report.get("passed"):
            raise ValueError("candidate validation failed: " + (text or str(report.get("status"))))
        bh, bs = str(report.get("boot_sha256", "")), int(report.get("boot_bytes", -1))
        if bh != BOOT_HASH or bs != BOOT_BYTES:
            raise ValueError("candidate does not match the audited REFGEN boot identity")
        sid, session = unique_session(out, bh)
        shutil.copy2(temp_report, session / "candidate-validation-start.json")
    manifest = {
        "schema": SCHEMA, "session_id": sid, "status": "candidate-validated",
        "created_utc": now(), "non_destructive": True,
        "source": {"workflow_run": "30250912889", "artifact_id": "8646929814"},
        "kit_root": str(kit), "tools_root": str(tools),
        "candidate": {"boot_img": report.get("boot_img"), "boot_bytes": bs, "boot_sha256": bh},
        "capture": None, "diagnosis": None, "bundle": None,
    }
    save(session / "a52-refgen-test-session.json", manifest)
    (session / "NEXT-STEP.txt").write_text(
        f"A52 REFGEN TEST SESSION\n\nSession ID: {sid}\nCandidate SHA-256: {bh}\n"
        f"Candidate bytes: {bs}\nCreated UTC: {manifest['created_utc']}\n\n"
        "Keep this directory unchanged. After recovery collection, run the finish-session "
        "wrapper with this directory, the untouched capture, and unknown, stable, or black.\n",
        encoding="utf-8")
    (out / "LATEST-A52-REFGEN-SESSION.txt").write_text(str(session) + "\n", encoding="utf-8")
    return {"status": "session-started", "passed": True, "session_id": sid,
            "session_dir": str(session), "candidate_sha256": bh, "candidate_bytes": bs}


def zip_dir(source: Path, target: Path, deterministic: bool = False) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for item in sorted(source.rglob("*"), key=lambda p: p.as_posix().lower()):
            if not item.is_file():
                continue
            rel = item.relative_to(source).as_posix()
            if deterministic:
                info = zipfile.ZipInfo(rel, (1980, 1, 1, 0, 0, 0))
                info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o100644 << 16
                z.writestr(info, item.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            else:
                z.write(item, rel)


def keep_capture(source: Path, dest: Path) -> tuple[Path, str]:
    source, dest = source.resolve(), dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        target = dest / "original-collector-directory.zip"
        if target.exists():
            target.unlink()
        zip_dir(source, target)
        return target, "directory-archived"
    if not source.is_file():
        raise ValueError(f"capture missing: {source}")
    target = dest / source.name
    if target.exists():
        if digest(target) != digest(source):
            raise ValueError(f"different preserved capture already exists: {target}")
    else:
        shutil.copy2(source, target)
    return target, "collector-zip" if source.suffix.lower() == ".zip" else "raw-image"


def checksum_lines(root: Path, skip: Path) -> list[str]:
    return [f"{digest(p)}  {p.relative_to(root).as_posix()}" for p in
            sorted(root.rglob("*"), key=lambda x: x.as_posix().lower())
            if p.is_file() and p.resolve() != skip.resolve()]


def finish(session: Path, capture: Path, screen: str, override: Path | None,
           bundle_out: Path | None) -> dict[str, Any]:
    session = session.resolve()
    mp = session / "a52-refgen-test-session.json"
    m = load(mp)
    if m.get("schema") != SCHEMA or m.get("status") not in ("candidate-validated", "complete"):
        raise ValueError("invalid or unfinished session manifest")
    kit, tools = Path(str(m["kit_root"])), Path(str(m["tools_root"]))
    validator = validator_path(tools, override)
    cr = session / "candidate-validation-final.json"
    rc, candidate, text = run_validator(validator, ["candidate", str(kit)], cr)
    if rc or not candidate.get("passed"):
        raise ValueError("candidate revalidation failed: " + (text or str(candidate.get("status"))))
    if candidate.get("boot_sha256") != m["candidate"]["boot_sha256"] or int(candidate.get("boot_bytes", -1)) != int(m["candidate"]["boot_bytes"]):
        raise ValueError("candidate changed after session start")

    evidence = session / "evidence"
    preserved, kind = keep_capture(capture, evidence / "original-capture")
    intake_path, diagnosis_dir = evidence / "capture-intake.json", evidence / "diagnosis"
    args = ["capture", str(capture.resolve()), "--analyse", screen,
            "--tools", str(tools.resolve()), "--analysis-output", str(diagnosis_dir)]
    rc, intake, text = run_validator(validator, args, intake_path)
    if rc or not intake.get("passed"):
        raise ValueError("capture validation or diagnosis failed: " + (text or str(intake.get("status"))))
    dp = diagnosis_dir / "diagnosis.json"
    if not dp.is_file():
        raise ValueError("diagnosis.json was not generated")
    diagnosis = load(dp)
    verdict = diagnosis.get("verdict")
    verdict = verdict.get("code") if isinstance(verdict, dict) else verdict

    bundle = bundle_out.resolve() if bundle_out else session.parent / f"A52_REFGEN_EVIDENCE_{m['session_id']}.zip"
    m.update({"status": "complete", "finalised_utc": now(), "screen_result": screen,
              "candidate_revalidated": True,
              "capture": {"source_path": str(capture.resolve()),
                          "preserved_path": str(preserved.relative_to(session)),
                          "preserved_kind": kind, "preserved_bytes": preserved.stat().st_size,
                          "preserved_sha256": digest(preserved),
                          "ramoops_bytes": intake.get("ramoops_bytes"),
                          "ramoops_sha256": intake.get("ramoops_sha256"),
                          "complete_evidence": intake.get("complete_evidence")},
              "diagnosis": {"directory": str(diagnosis_dir.relative_to(session)), "verdict": verdict},
              "bundle": {"name": bundle.name, "checksum_file": bundle.name + ".sha256",
                         "checksum_receipt": bundle.name + ".receipt.json"}})
    save(mp, m)
    sums = session / "FINAL-SHA256SUMS.txt"
    sums.write_text("\n".join(checksum_lines(session, sums)) + "\n", encoding="utf-8")
    bundle.parent.mkdir(parents=True, exist_ok=True)
    if bundle.exists():
        bundle.unlink()
    zip_dir(session, bundle, True)
    bh = digest(bundle)
    shafile = Path(str(bundle) + ".sha256")
    shafile.write_text(f"{bh}  {bundle.name}\n", encoding="utf-8")
    receipt = Path(str(bundle) + ".receipt.json")
    save(receipt, {"schema": "a52-refgen-evidence-bundle-receipt-v1",
                   "session_id": m["session_id"], "bundle": str(bundle),
                   "bundle_bytes": bundle.stat().st_size, "bundle_sha256": bh,
                   "created_utc": now()})
    return {"status": "session-complete", "passed": True, "session_id": m["session_id"],
            "session_dir": str(session), "screen_result": screen,
            "capture_sha256": digest(preserved), "ramoops_sha256": intake.get("ramoops_sha256"),
            "complete_evidence": intake.get("complete_evidence"), "verdict": verdict,
            "bundle": str(bundle), "bundle_sha256": bh,
            "bundle_checksum_file": str(shafile), "bundle_receipt": str(receipt)}


def safe_extract(source: Path, dest: Path) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(source) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise ValueError(f"unsafe ZIP member: {info.filename}")
        z.extractall(dest)


def verify_manifest(root: Path, manifest: Path) -> tuple[int, list[str]]:
    count, errors = 0, []
    for n, line in enumerate(manifest.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            errors.append(f"line {n}: invalid checksum entry"); continue
        expected, rel = parts[0].lower(), parts[1].lstrip("*").replace("\\", "/")
        item = (root / rel).resolve()
        try:
            item.relative_to(root.resolve())
        except ValueError:
            errors.append(f"line {n}: path escapes root: {rel}"); continue
        if not item.is_file(): errors.append(f"line {n}: missing file: {rel}"); continue
        if digest(item) != expected: errors.append(f"line {n}: checksum mismatch: {rel}"); continue
        count += 1
    return count, errors


def verify(bundle: Path, receipt: Path | None, checksum: Path | None) -> dict[str, Any]:
    bundle = bundle.resolve()
    if not bundle.is_file() or bundle.suffix.lower() != ".zip":
        raise ValueError(f"bundle missing or not ZIP: {bundle}")
    receipt = receipt.resolve() if receipt else Path(str(bundle) + ".receipt.json")
    checksum = checksum.resolve() if checksum else Path(str(bundle) + ".sha256")
    actual = digest(bundle)
    expected = checksum.read_text(encoding="utf-8", errors="replace").split()[0].lower() if checksum.is_file() else None
    r = load(receipt) if receipt.is_file() else {}
    checks = [{"name": "outer_checksum", "passed": actual == expected},
              {"name": "receipt_hash", "passed": r.get("bundle_sha256") == actual},
              {"name": "receipt_size", "passed": r.get("bundle_bytes") == bundle.stat().st_size}]
    with tempfile.TemporaryDirectory(prefix="a52-bundle-verify-") as td:
        root = Path(td); safe_extract(bundle, root)
        mp, sp = root / "a52-refgen-test-session.json", root / "FINAL-SHA256SUMS.txt"
        m = load(mp) if mp.is_file() else {}
        count, errors = verify_manifest(root, sp) if sp.is_file() else (0, ["manifest missing"])
        originals = list((root / "evidence" / "original-capture").glob("*")) if (root / "evidence" / "original-capture").is_dir() else []
        checks += [{"name": "session_schema", "passed": m.get("schema") == SCHEMA and m.get("status") == "complete"},
                   {"name": "receipt_session", "passed": r.get("session_id") == m.get("session_id")},
                   {"name": "bundle_name", "passed": m.get("bundle", {}).get("name") == bundle.name},
                   {"name": "internal_checksums", "passed": count > 0 and not errors},
                   {"name": "original_capture", "passed": len([p for p in originals if p.is_file()]) == 1},
                   {"name": "diagnosis", "passed": (root / "evidence/diagnosis/diagnosis.json").is_file()}]
    passed = all(c["passed"] for c in checks)
    return {"status": "bundle-valid" if passed else "bundle-invalid", "passed": passed,
            "non_destructive": True, "bundle": str(bundle), "bundle_bytes": bundle.stat().st_size,
            "bundle_sha256": actual, "session_id": r.get("session_id"), "checks": checks}


def stub(path: Path) -> None:
    path.write_text('''#!/usr/bin/env python3
import argparse,hashlib,json,tempfile,zipfile
from pathlib import Path
h=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
p=argparse.ArgumentParser();s=p.add_subparsers(dest="c",required=True)
a=s.add_parser("candidate");a.add_argument("path");a.add_argument("--report")
b=s.add_parser("capture");b.add_argument("path");b.add_argument("--report");b.add_argument("--analyse");b.add_argument("--tools");b.add_argument("--analysis-output")
x=p.parse_args()
if x.c=="candidate":
 q=Path(x.path)/"package/boot.img";r={"passed":True,"boot_img":str(q),"boot_bytes":q.stat().st_size,"boot_sha256":h(q)}
else:
 q=Path(x.path);t=None
 if q.suffix.lower()==".zip":t=tempfile.TemporaryDirectory();z=zipfile.ZipFile(q);z.extractall(t.name);q=next(Path(t.name).rglob("ramoops-raw-1MiB.bin"))
 r={"passed":True,"ramoops_bytes":q.stat().st_size,"ramoops_sha256":h(q),"complete_evidence":True}
 if x.analyse:
  o=Path(x.analysis_output);o.mkdir(parents=True,exist_ok=True);(o/"diagnosis.json").write_text(json.dumps({"verdict":{"code":"stub"}}));(o/"diagnosis.md").write_text("stub");(o/"critical-timeline.csv").write_text("x")
Path(x.report).write_text(json.dumps(r));print(json.dumps(r))
''', encoding="utf-8")


def selftest() -> dict[str, Any]:
    global BOOT_HASH, BOOT_BYTES
    oh, ob, checks = BOOT_HASH, BOOT_BYTES, []
    try:
        with tempfile.TemporaryDirectory(prefix="a52-session-test-") as td:
            root, kit = Path(td), Path(td) / "kit"; tools = kit / "tools"
            (kit / "package").mkdir(parents=True); tools.mkdir(parents=True)
            boot = kit / "package/boot.img"; boot.write_bytes(b"candidate")
            BOOT_HASH, BOOT_BYTES = digest(boot), boot.stat().st_size
            v = tools / "stub.py"; stub(v)
            a = start(kit, root / "sessions", tools, v); checks.append({"name": "start", "passed": a["passed"]})
            raw = root / "ramoops-raw-1MiB.bin"; raw.write_bytes(b"\0" * 1_048_576)
            b = finish(Path(a["session_dir"]), raw, "black", v, None); checks.append({"name": "finish", "passed": b["passed"]})
            c = verify(Path(b["bundle"]), None, None); checks.append({"name": "verify", "passed": c["passed"]})
    finally:
        BOOT_HASH, BOOT_BYTES = oh, ob
    passed = all(c["passed"] for c in checks)
    return {"status": "self-test-passed" if passed else "self-test-failed", "passed": passed, "checks": checks}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__); s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("self-test")
    a = s.add_parser("start"); a.add_argument("--kit-root", type=Path, required=True); a.add_argument("--output-root", type=Path, default=Path.cwd()); a.add_argument("--tools", type=Path); a.add_argument("--validator", type=Path)
    b = s.add_parser("finish"); b.add_argument("session_dir", type=Path); b.add_argument("capture", type=Path); b.add_argument("screen_result", choices=("unknown", "stable", "black")); b.add_argument("--validator", type=Path); b.add_argument("--bundle-output", type=Path)
    c = s.add_parser("verify"); c.add_argument("bundle", type=Path); c.add_argument("--receipt", type=Path); c.add_argument("--checksum-file", type=Path)
    x = p.parse_args()
    try:
        if x.cmd == "self-test": r = selftest()
        elif x.cmd == "start": r = start(x.kit_root, x.output_root, x.tools or x.kit_root / "tools", x.validator)
        elif x.cmd == "finish": r = finish(x.session_dir, x.capture, x.screen_result, x.validator, x.bundle_output)
        else: r = verify(x.bundle, x.receipt, x.checksum_file)
        print(json.dumps(r, indent=2, sort_keys=True)); return 0 if r.get("passed") else 1
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, zipfile.BadZipFile) as e:
        print(json.dumps({"status": "error", "passed": False, "error": str(e)}, indent=2), file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
