#!/usr/bin/env python3
"""Phase 255: restore post-BOOT_READY recorder visibility without behavior changes.

Phase 234 intentionally narrowed the persistent recorder to RSCC/control traffic.
Later GPU phases extended that gate with Kxxx prefixes, but the already-existing
Android/userspace cumulative probes remained filtered before sequence allocation.

Phase 255 changes only recorder admission/retention. It restores the exact
post-boot prefixes already compiled by earlier phases:
  BOOTPOST - exec/exit/service milestones including SurfaceFlinger
  USRPOST  - vdc/vold userspace boundary
  ODSPOST  - odsign/odrefresh boundary
  GFXPOST  - late KGSL userspace/open state
  TRIPOST  - Phase 228 cumulative vold/ODS/SurfaceFlinger/KGSL checkpoint

On Phase256+ branches this script chains the Phase256 KGSL devnode/framework
overlay, its pinned devfreq dependency closure, and, when present, the Phase257
KGSL publication-pipeline recorder. The Phase257 hook is committed here so the
actual child kernel builder sees it before compilation; it is not injected only
into a parent runner's temporary workspace.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MARKER = "A52_PHASE255_POSTBOOT_VISIBILITY_V1"
PHASE256 = Path(__file__).resolve().parent / "256_phase255_kgsl_devnode_framework_overlay.py"
PHASE256_CLOSURE = Path(__file__).resolve().parent / "256_devfreq_import_closure.py"
PHASE257 = Path(__file__).resolve().parent / "257_phase256_kgsl_publication_pipeline_overlay.py"
PHASE257_CHAIN_MARKER = "A52_PHASE257_COMMITTED_CHILD_BUILD_CHAIN_V1"

FORMAT_ANCHOR = 'if (strncmp(fmt, "K254", 4) &&\n'
FORMAT_REPLACEMENT = """if (strncmp(fmt, "K255VIS", 7) &&
    strncmp(fmt, "TRIPOST", 7) &&
    strncmp(fmt, "BOOTPOST", 8) &&
    strncmp(fmt, "USRPOST", 7) &&
    strncmp(fmt, "ODSPOST", 7) &&
    strncmp(fmt, "GFXPOST", 7) &&
    strncmp(fmt, "K254", 4) &&
"""

CRITICAL_ANCHOR = 'return !strncmp(message, "K254 ", 5) ||\n'
CRITICAL_REPLACEMENT = """return !strncmp(message, "K255VIS ", 8) ||
       !strncmp(message, "TRIPOST ", 8) ||
       !strncmp(message, "BOOTPOST ", 9) ||
       !strncmp(message, "USRPOST ", 8) ||
       !strncmp(message, "ODSPOST ", 8) ||
       !strncmp(message, "GFXPOST ", 8) ||
       !strncmp(message, "K254 ", 5) ||
"""

COMMENTED_REPLACEMENT = """/* A52_PHASE255_POSTBOOT_VISIBILITY_V1
 * Visibility only. Restore previously compiled post-boot recorder prefixes
 * that Phase234's focused admission gate suppressed. TRIPOST is the sparse
 * cumulative checkpoint; BOOTPOST/USRPOST/ODSPOST/GFXPOST feed its state.
 */
""" + FORMAT_REPLACEMENT


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_text(text: str, label: str) -> str:
    if MARKER in text:
        validate(text, label)
        return text

    if 'strncmp(fmt, "K254", 4)' not in text:
        raise RuntimeError(f"{label}: Phase254 recorder admission gate missing")
    if '!strncmp(message, "K254 ", 5)' not in text:
        raise RuntimeError(f"{label}: Phase254 critical-retention gate missing")

    text = replace_once(
        text, FORMAT_ANCHOR, FORMAT_REPLACEMENT,
        f"{label}: postboot format admission"
    )
    text = replace_once(
        text, FORMAT_REPLACEMENT, COMMENTED_REPLACEMENT,
        f"{label}: Phase255 source marker"
    )
    text = replace_once(
        text, CRITICAL_ANCHOR, CRITICAL_REPLACEMENT,
        f"{label}: post-capacity retention"
    )
    validate(text, label)
    return text


def validate(text: str, label: str) -> None:
    required = (
        MARKER,
        'strncmp(fmt, "K255VIS", 7)',
        'strncmp(fmt, "TRIPOST", 7)',
        'strncmp(fmt, "BOOTPOST", 8)',
        'strncmp(fmt, "USRPOST", 7)',
        'strncmp(fmt, "ODSPOST", 7)',
        'strncmp(fmt, "GFXPOST", 7)',
        '!strncmp(message, "K255VIS ", 8)',
        '!strncmp(message, "TRIPOST ", 8)',
        '!strncmp(message, "BOOTPOST ", 9)',
        '!strncmp(message, "USRPOST ", 8)',
        '!strncmp(message, "ODSPOST ", 8)',
        '!strncmp(message, "GFXPOST ", 8)',
        'strncmp(fmt, "K254", 4)',
        '!strncmp(message, "K254 ", 5)',
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: missing {token!r}")

    if text.count(MARKER) != 1:
        raise RuntimeError(f"{label}: Phase255 marker count is not exactly one")


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))

    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        path = root / RECORDER
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if 'strncmp(fmt, "K254", 4)' not in text:
            continue
        if '!strncmp(message, "K254 ", 5)' not in text:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(
            "expected one generated Phase254 recorder root, found "
            + (", ".join(map(str, hits)) or "none")
        )
    return hits[0]


def run_phase256(args: list[str]) -> int:
    stages = [
        (PHASE256, "KGSL devnode/framework overlay"),
        (PHASE256_CLOSURE, "devfreq import closure"),
    ]
    # A52_PHASE257_COMMITTED_CHILD_BUILD_CHAIN_V1
    # Only Phase257 branches contain this file. Because this decision is made
    # inside the committed cumulative wrapper, the remote child builder applies
    # Phase257 before compiling the Image.
    if PHASE257.is_file():
        stages.append((PHASE257, "Phase257 KGSL publication-pipeline recorder"))

    for stage, label in stages:
        if not stage.is_file():
            raise RuntimeError(f"missing Phase256/257 {label}: {stage}")
        result = subprocess.run([sys.executable, str(stage), *args], check=False)
        if result.returncode:
            raise RuntimeError(f"Phase256/257 {label} failed rc={result.returncode}")
    return 0


def self_test() -> None:
    fixture = """static bool a52_r179_is_critical_message(const char *message)
{
    return !strncmp(message, "K254 ", 5) ||
       !strncmp(message, "K253 ", 5);
}

void a52_ackfr_record(const char *fmt, ...)
{
    if (strncmp(fmt, "K254", 4) &&
    strncmp(fmt, "K253", 4) &&
        strncmp(fmt, "RSCC", 4))
        return;
}
"""
    patched = patch_text(fixture, "fixture")
    if patch_text(patched, "idempotence") != patched:
        raise AssertionError("Phase255 patch is not idempotent")
    validate(patched, "fixture-final")
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        "256_phase255_kgsl_devnode_framework_overlay.py",
        "256_devfreq_import_closure.py",
        "257_phase256_kgsl_publication_pipeline_overlay.py",
        PHASE257_CHAIN_MARKER,
    ):
        if token not in source:
            raise AssertionError(f"cumulative Phase256/257 chain missing {token}")
    print("Phase 255 post-BOOT_READY visibility overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return run_phase256(["--self-test"])

    root = locate(sys.argv[1:])
    path = root / RECORDER
    text = path.read_text(encoding="utf-8")
    path.write_text(patch_text(text, str(path)), encoding="utf-8")
    print(
        f"{MARKER}: restored BOOTPOST/USRPOST/ODSPOST/GFXPOST/TRIPOST "
        "admission and post-capacity retention",
        flush=True,
    )
    return run_phase256([str(root)])


if __name__ == "__main__":
    raise SystemExit(main())
