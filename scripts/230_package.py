#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
REF = "agent/a52-phase230-kgsl-driver-core-path-v1"
WORKFLOW_ID = 325785868
API = f"https://api.github.com/repos/{REPO}"


def request(url: str, *, method: str = "GET", data: dict | None = None) -> bytes:
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()


def get_json(url: str) -> dict:
    return json.loads(request(url))


def dispatch_and_wait() -> tuple[int, str, int]:
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    request(f"{API}/actions/workflows/{WORKFLOW_ID}/dispatches", method="POST", data={"ref": REF})
    run = None
    for _ in range(40):
        data = get_json(f"{API}/actions/workflows/{WORKFLOW_ID}/runs?branch={REF}&event=workflow_dispatch&per_page=20")
        candidates = [r for r in data.get("workflow_runs", []) if r.get("created_at", "") >= started]
        if candidates:
            run = sorted(candidates, key=lambda r: r["created_at"])[-1]
            break
        time.sleep(15)
    if not run:
        raise RuntimeError("dispatched inherited build was not found")
    run_id = int(run["id"])
    print(f"Monitoring {run['html_url']}", flush=True)
    for _ in range(720):
        run = get_json(f"{API}/actions/runs/{run_id}")
        print(f"run={run_id} status={run['status']} conclusion={run.get('conclusion') or 'pending'}", flush=True)
        if run["status"] == "completed":
            break
        time.sleep(30)
    if run.get("conclusion") != "success":
        raise RuntimeError(f"inherited builder conclusion: {run.get('conclusion')}")
    artifacts = get_json(f"{API}/actions/runs/{run_id}/artifacts?per_page=100").get("artifacts", [])
    artifacts = [a for a in artifacts if not a.get("expired")]
    if not artifacts:
        raise RuntimeError("successful inherited build produced no artifact")
    artifact = artifacts[-1]
    return run_id, run["html_url"], int(artifact["id"])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_artifact(artifact_id: int, dest: Path) -> None:
    api_url = f"{API}/actions/artifacts/{artifact_id}/zip"
    req = urllib.request.Request(api_url, method="GET")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        opener.open(req, timeout=120)
        raise RuntimeError("artifact download unexpectedly returned without redirect")
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        location = exc.headers.get("Location")
        if not location:
            raise RuntimeError("artifact redirect did not include Location") from exc
    with urllib.request.urlopen(location, timeout=120) as response:
        dest.write_bytes(response.read())


def select_source() -> tuple[int, str, int]:
    artifact = os.environ.get("PHASE230_SOURCE_ARTIFACT_ID")
    run = os.environ.get("PHASE230_SOURCE_RUN_ID")
    if artifact and run:
        run_id = int(run)
        return run_id, f"https://github.com/{REPO}/actions/runs/{run_id}", int(artifact)
    if artifact or run:
        raise RuntimeError("both PHASE230_SOURCE_ARTIFACT_ID and PHASE230_SOURCE_RUN_ID are required")
    return dispatch_and_wait()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def locate_root(base: Path) -> Path:
    matches = list(base.rglob("package/boot.img"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one package/boot.img, found {len(matches)}")
    return matches[0].parent.parent


def verify_markers(image: Path) -> None:
    data = image.read_bytes()
    for marker in (
        b"KGPPOST 230", b"KGPPOST 229", b"TRIPOST 228", b"ODSPOST 226",
        b"GFXPOST 225 ks1", b"GFXPOST 225 ks2",
    ):
        if marker not in data:
            raise RuntimeError(f"missing binary marker: {marker.decode()}")
        print(f"binary marker present: {marker.decode()}")


def package(root: Path, source_run_id: int, source_run_url: str) -> Path:
    verify_markers(root / "compile/Image")

    audit_dir = root / "audit/phase230"
    audit_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2("scripts/227_phase226_retention_wrapper.py", audit_dir / "phase230_driver_core_loader.py")
    shutil.copy2("scripts/229_phase228_kgsl_wrapper.py", audit_dir / "phase229_kgsl_loader.py")
    shutil.copy2("scripts/230_package.py", audit_dir / "phase230_package.py")
    shutil.copytree("scripts/230_patcher_parts", audit_dir / "patcher_parts", dirs_exist_ok=True)
    shutil.copytree("scripts/230_fixtures", audit_dir / "fixtures", dirs_exist_ok=True)
    shutil.copy2("scripts/230_design.md", root / "PHASE230-DESIGN.md")
    shutil.copy2("scripts/230_trigger.txt", root / "PHASE230-HARDWARE-TEST.txt")

    audit_path = root / "final-audit.json"
    audit = json.loads(audit_path.read_text())
    audit.update({
        "phase": 230,
        "base_phase": 229,
        "functional_base_phase": 229,
        "hardware_validated": False,
        "status": "phase230-kgsl-driver-core-path-ci-audited-not-hardware-validated",
        "trace_marker_prefix": "KGPPOST 230",
        "phase229_kgsl_snapshot_retained": True,
        "phase228_tri_track_retained": True,
        "phase226_odspost_retained": True,
        "phase230_platform_record_limit": 32,
        "phase230_dd_record_limit": 160,
        "phase230_core_record_limit": 96,
        "phase230_behavior_changed": False,
        "phase230_supplier_decisions_changed": False,
        "phase230_return_values_changed": False,
        "phase230_rs_roots": 48,
    })
    retained = list(audit.get("post_capacity_retention", []))
    for marker in ("ODSPOST", "TRIPOST", "KGPPOST"):
        if marker not in retained:
            retained.append(marker)
    audit["post_capacity_retention"] = retained
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")

    identity = {
        "phase": 230,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "source_builder_run_id": str(source_run_id),
        "source_builder_run_url": source_run_url,
        "touchgrass_commit": "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
        "hardware_validated": False,
        "change": "KGSL platform-match, bidirectional attach, supplier and pre-probe trace",
        "rs_roots": 48,
    }
    (root / "BUILD-IDENTITY.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
    (root / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 230 KGSL driver-core path candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND THE PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "Phase 230 preserves Phase 229 and adds bounded metadata-only tracing\n"
        "for the exact qcom,kgsl-3d0 / kgsl-3d platform match, both attach\n"
        "directions, deferred-probe state, supplier identities and every\n"
        "pre-callback driver-core boundary.\n\n"
        "No match, supplier, DT, return-value, power, IOMMU, firmware, service\n"
        "or security behavior is changed. Reed-Solomon remains RS48.\n\n"
        "CI-validated, not hardware-validated. Follow PHASE230-HARDWARE-TEST.txt.\n"
    )

    sums = root / "SHA256SUMS"
    if sums.exists():
        sums.unlink()
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS")
    sums.write_text("".join(f"{sha256(path)}  ./{path.relative_to(root)}\n" for path in files))

    out = Path("phase230-out")
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(root, out)
    return out


def main() -> int:
    run_id, run_url, artifact_id = select_source()
    work = Path("phase230-work")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    zip_path = work / "inherited.zip"
    download_artifact(artifact_id, zip_path)
    extract = work / "inherited"
    extract.mkdir()
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract)
    root = locate_root(extract)
    package(root, run_id, run_url)
    print(f"Phase 230 package prepared from source run {run_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print(f"Phase 230 packaging failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
