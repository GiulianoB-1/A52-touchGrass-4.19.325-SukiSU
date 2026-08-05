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
REF = "agent/a52-phase232-gpu-gx-optional-aon-v1"
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
    started = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    request(
        f"{API}/actions/workflows/{WORKFLOW_ID}/dispatches",
        method="POST",
        data={"ref": REF},
    )
    run = None
    for _ in range(40):
        data = get_json(
            f"{API}/actions/workflows/{WORKFLOW_ID}/runs?"
            f"branch={REF}&event=workflow_dispatch&per_page=20"
        )
        candidates = [
            item
            for item in data.get("workflow_runs", [])
            if item.get("created_at", "") >= started
        ]
        if candidates:
            run = sorted(candidates, key=lambda item: item["created_at"])[-1]
            break
        time.sleep(15)
    if not run:
        raise RuntimeError("dispatched inherited build was not found")

    run_id = int(run["id"])
    print(f"Monitoring {run['html_url']}", flush=True)
    for _ in range(720):
        run = get_json(f"{API}/actions/runs/{run_id}")
        print(
            f"run={run_id} status={run['status']} "
            f"conclusion={run.get('conclusion') or 'pending'}",
            flush=True,
        )
        if run["status"] == "completed":
            break
        time.sleep(30)
    if run.get("conclusion") != "success":
        raise RuntimeError(
            f"inherited builder conclusion: {run.get('conclusion')}"
        )

    artifacts = get_json(
        f"{API}/actions/runs/{run_id}/artifacts?per_page=100"
    ).get("artifacts", [])
    artifacts = [item for item in artifacts if not item.get("expired")]
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
        raise RuntimeError("artifact download returned without redirect")
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise
        location = exc.headers.get("Location")
        if not location:
            raise RuntimeError("artifact redirect missing Location") from exc
    with urllib.request.urlopen(location, timeout=120) as response:
        dest.write_bytes(response.read())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_root(base: Path) -> Path:
    matches = list(base.rglob("package/boot.img"))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one package/boot.img, found {len(matches)}"
        )
    return matches[0].parent.parent


def verify_markers(image: Path) -> None:
    data = image.read_bytes()
    for marker in (
        b"A52GDSC GPU_GX_PROFILE_V2",
        b"A52GDSC gpu-enable",
        b"KGPPOST 230 replay-begin",
        b"KGPPOST 230",
        b"KGPPOST 229",
        b"TRIPOST 228",
        b"ODSPOST 226",
    ):
        if marker not in data:
            raise RuntimeError(f"missing binary marker: {marker.decode()}")
        print(f"binary marker present: {marker.decode()}")


def package(root: Path, source_run_id: int, source_run_url: str) -> Path:
    verify_markers(root / "compile/Image")

    audit_dir = root / "audit/phase232"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "227_phase226_retention_wrapper.py",
        "230_phase229_driver_core_wrapper.py",
        "231_phase230_gpu_gdsc_wrapper.py",
        "232_phase231_optional_aon_wrapper.py",
        "232_package.py",
    ):
        shutil.copy2(Path("scripts") / name, audit_dir / name)
    shutil.copy2("scripts/232_design.md", root / "PHASE232-DESIGN.md")
    shutil.copy2(
        "scripts/232_trigger.txt",
        root / "PHASE232-HARDWARE-TEST.txt",
    )

    audit_path = root / "final-audit.json"
    audit = json.loads(audit_path.read_text())
    audit.update({
        "phase": 232,
        "base_phase": 231,
        "functional_base_phase": 230,
        "hardware_validated": False,
        "status": "phase232-gpu-gx-optional-aon-ci-audited-not-hardware-validated",
        "phase232_behavior_changed": True,
        "phase232_exact_supplier": "3d9100c.qcom,gdsc",
        "phase232_exact_regulator": "gpu_gx_gdsc",
        "phase232_exact_mmio": "0x03d9100c+0x4",
        "phase232_aon_reset_optional": True,
        "phase232_observed_dtb_aon_property": False,
        "phase232_gpu_cx_claimed": False,
        "phase232_unrelated_gdsc_claimed": False,
        "phase232_driver_core_bypass": False,
        "phase232_phase230_replay_retained": True,
    })
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")

    identity = {
        "phase": 232,
        "git_sha": os.environ.get("GITHUB_SHA"),
        "git_ref": os.environ.get("GITHUB_REF"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "source_builder_run_id": str(source_run_id),
        "source_builder_run_url": source_run_url,
        "touchgrass_commit": "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
        "hardware_validated": False,
        "change": "accept exact Lagoon gpu_gx_gdsc with optional AON reset",
        "phase230_replay_retained": True,
        "rs_roots": 48,
    }
    (root / "BUILD-IDENTITY.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n"
    )
    (root / "README-FIRST.txt").write_text(
        "A52 GKI 5.10 Phase 232 GPU GX optional-AON candidate\n\n"
        "FLASH ONLY AFTER SHA256SUMS AND THE PACKAGE AUDIT PASS:\n"
        "  package/boot.img -> BOOT partition\n\n"
        "The Phase 231 hardware capture proved that the exact GPU GX GDSC\n"
        "remained unbound. The flashed DTB omits qcom,reset-aon-logic, but\n"
        "Phase 231 incorrectly required that optional property. Phase 232\n"
        "keeps every exact profile and resource guard while executing the\n"
        "AON reset pulse only when the property is present.\n\n"
        "Phase 230 driver-core tracing and late replay remain enabled.\n\n"
        "CI-validated, not hardware-validated. Follow\n"
        "PHASE232-HARDWARE-TEST.txt.\n"
    )

    sums = root / "SHA256SUMS"
    if sums.exists():
        sums.unlink()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    sums.write_text(
        "".join(
            f"{sha256(path)}  ./{path.relative_to(root)}\n" for path in files
        )
    )

    out = Path("phase232-out")
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(root, out)
    return out


def main() -> int:
    run_id, run_url, artifact_id = dispatch_and_wait()
    work = Path("phase232-work")
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
    print(f"Phase 232 package prepared from source run {run_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print(f"Phase 232 packaging failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
