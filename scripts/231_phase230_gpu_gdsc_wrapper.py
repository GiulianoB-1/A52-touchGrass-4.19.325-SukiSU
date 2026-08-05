#!/usr/bin/env python3
"""Run Phase 230, then provide the exact Lagoon GPU GX legacy GDSC."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

PHASE230_MARKER = "A52_PHASE230_KGSL_DRIVER_CORE_PATH"
PHASE231_MARKER = "A52_PHASE231_GPU_GX_GDSC_PROVIDER"
GDSC_REL = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
DD_REL = Path("drivers/base/dd.c")
EXPECTED_BEFORE_SHA256 = "e86d86b42609c4e7b84ace339dfc21e4387846f32b8c88b9ce6b241f5673dc56"
EXPECTED_AFTER_SHA256 = "5567c52f4cab8c6667d423b2303ebf8ddd9b408b5061604fce9d4413b885c49c"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "231_fixtures"


def replacement_source() -> str:
    path = fixture_dir() / "a52-legacy-gdsc-after.c"
    if not path.is_file():
        raise RuntimeError(f"missing Phase 231 replacement fixture: {path}")
    text = path.read_text(encoding="utf-8")
    if sha256_text(text) != EXPECTED_AFTER_SHA256:
        raise RuntimeError("Phase 231 replacement fixture checksum mismatch")
    return text


def patch_gdsc(text: str, label: str) -> str:
    if PHASE231_MARKER in text:
        return text
    actual = sha256_text(text)
    if actual != EXPECTED_BEFORE_SHA256:
        raise RuntimeError(
            f"{label}: unexpected legacy GDSC source sha256 {actual}"
        )
    patched = replacement_source()
    for token in (
        PHASE231_MARKER,
        "A52GDSC GPU_GX_PROFILE_V1",
        "A52_GDSC_GPU_GX_ADDR",
        "syscon_regmap_lookup_by_phandle",
        "a52_legacy_gdsc_enable_gpu_gx",
        "a52_legacy_gdsc_disable_gpu_gx",
    ):
        if token not in patched:
            raise RuntimeError(f"{label}: missing Phase 231 token {token}")
    return patched


def candidate_roots(arguments: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        path = Path(value)
        roots.extend((path, path.parent))
    roots.extend((Path("workspace/gki-phase199-src"), Path("gki/common")))
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def locate_root(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for root in candidate_roots(arguments):
        gdsc = root / GDSC_REL
        dd = root / DD_REL
        if not gdsc.is_file() or not dd.is_file():
            continue
        if PHASE230_MARKER not in dd.read_text(encoding="utf-8"):
            continue
        matches.append(root)
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(path) for path in unique) or "none"
        raise RuntimeError(
            f"expected one generated Phase 230 source root, found "
            f"{len(unique)}: {rendered}"
        )
    return unique[0]


def patch_generated(arguments: list[str]) -> Path:
    root = locate_root(arguments)
    path = root / GDSC_REL
    original = path.read_text(encoding="utf-8")
    path.write_text(patch_gdsc(original, str(path)), encoding="utf-8")
    return root


def self_test() -> None:
    before_path = fixture_dir() / "a52-legacy-gdsc-before.c"
    if not before_path.is_file():
        raise RuntimeError(f"missing Phase 231 source fixture: {before_path}")
    original = before_path.read_text(encoding="utf-8")
    if sha256_text(original) != EXPECTED_BEFORE_SHA256:
        raise RuntimeError("Phase 231 input fixture checksum mismatch")
    patched = patch_gdsc(original, "fixture")
    if sha256_text(patched) != EXPECTED_AFTER_SHA256:
        raise AssertionError("Phase 231 output checksum mismatch")
    if patch_gdsc(patched, "fixture/idempotent") != patched:
        raise AssertionError("Phase 231 patch is not idempotent")
    if patched.count('"gpu_gx_gdsc"') != 2:
        raise AssertionError("Phase 231 GPU GX whitelist is not exact")
    if '"gpu_cx_gdsc"' in patched:
        raise AssertionError("Phase 231 unexpectedly claims GPU CX")
    print("Phase 231 exact GPU GX GDSC provider self-test: PASS")


def main() -> int:
    base = Path(__file__).with_name("230_phase229_driver_core_wrapper.py")
    if not base.is_file():
        raise SystemExit(f"missing Phase 230 base wrapper: {base}")
    completed = subprocess.run(
        [sys.executable, str(base), *sys.argv[1:]], check=False
    )
    if completed.returncode:
        return completed.returncode
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = patch_generated(sys.argv[1:])
    print(f"Phase 231 exact GPU GX GDSC provider applied to {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
