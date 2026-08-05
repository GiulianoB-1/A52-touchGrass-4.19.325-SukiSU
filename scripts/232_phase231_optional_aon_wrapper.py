#!/usr/bin/env python3
"""Run Phase 231, then accept the exact Lagoon GPU GX DT without AON reset."""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

PHASE231_MARKER = "A52_PHASE231_GPU_GX_GDSC_PROVIDER"
PHASE232_MARKER = "A52_PHASE232_GPU_GX_OPTIONAL_AON"
GDSC_REL = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
DD_REL = Path("drivers/base/dd.c")
EXPECTED_BEFORE_SHA256 = "5567c52f4cab8c6667d423b2303ebf8ddd9b408b5061604fce9d4413b885c49c"
EXPECTED_AFTER_SHA256 = "d5dd48b76da3a84fa411bf2c0b271e14ef3522ddec6aedd745a57f70cb3bd2b0"

_FATAL_AON = '''        gdsc->reset_aon = of_property_read_bool(pdev->dev.of_node,
                                                "qcom,reset-aon-logic");
        if (!gdsc->reset_aon)
            return -EINVAL;

        before = readl_relaxed(gdsc->gdscr);'''

_OPTIONAL_AON = '''        gdsc->reset_aon = of_property_read_bool(pdev->dev.of_node,
                                                "qcom,reset-aon-logic");
        /*
         * The Lagoon DT does not set qcom,reset-aon-logic for GPU GX.
         * Match the Qualcomm GDSC helper semantics: the AON/GMEM reset
         * pulse is optional and is executed only when the property exists.
         */

        before = readl_relaxed(gdsc->gdscr);'''

_V1_LOG = '''        a52_ackfr_record(
            "A52GDSC GPU_GX_PROFILE_V1 init name=%s before=0x%x after=0x%x",
            name, before, val);'''

_V2_LOG = '''        a52_ackfr_record(
            "A52GDSC GPU_GX_PROFILE_V2 init name=%s before=0x%x after=0x%x aon=%u",
            name, before, val, gdsc->reset_aon);
        a52_persistent_diag_mark(
            "A52GDSC GPU_PROFILE dev=%s name=%s aon=%u reg=0x%08x\\n",
            dev_name(&pdev->dev), name, gdsc->reset_aon, val);'''


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patch_gdsc(text: str, label: str) -> str:
    if PHASE232_MARKER in text:
        return text
    actual = sha256_text(text)
    if actual != EXPECTED_BEFORE_SHA256:
        raise RuntimeError(
            f"{label}: unexpected Phase 231 GDSC source sha256 {actual}"
        )
    if text.count(PHASE231_MARKER) != 1:
        raise RuntimeError(f"{label}: Phase 231 marker count is not one")
    if text.count(_FATAL_AON) != 1:
        raise RuntimeError(f"{label}: fatal AON block count is not one")
    if text.count(_V1_LOG) != 1:
        raise RuntimeError(f"{label}: Phase 231 GPU profile log count is not one")

    patched = text.replace(
        PHASE231_MARKER,
        PHASE231_MARKER + "\n * " + PHASE232_MARKER,
        1,
    )
    patched = patched.replace(_FATAL_AON, _OPTIONAL_AON, 1)
    patched = patched.replace(_V1_LOG, _V2_LOG, 1)

    actual_after = sha256_text(patched)
    if actual_after != EXPECTED_AFTER_SHA256:
        raise RuntimeError(
            f"{label}: unexpected Phase 232 GDSC sha256 {actual_after}"
        )
    for token in (
        PHASE231_MARKER,
        PHASE232_MARKER,
        "A52GDSC GPU_GX_PROFILE_V2",
        "if (gdsc->reset_aon)",
        "a52_legacy_gdsc_enable_gpu_gx",
        "a52_legacy_gdsc_disable_gpu_gx",
    ):
        if token not in patched:
            raise RuntimeError(f"{label}: missing Phase 232 token {token}")
    if _FATAL_AON in patched:
        raise RuntimeError(f"{label}: optional AON property remains fatal")
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
        if PHASE231_MARKER not in gdsc.read_text(encoding="utf-8"):
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
            f"expected one generated Phase 231 source root, found "
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
    before_path = (
        Path(__file__).resolve().parent
        / "231_fixtures"
        / "a52-legacy-gdsc-after.c"
    )
    if not before_path.is_file():
        raise RuntimeError(f"missing Phase 231 source fixture: {before_path}")
    original = before_path.read_text(encoding="utf-8")
    if sha256_text(original) != EXPECTED_BEFORE_SHA256:
        raise RuntimeError("Phase 232 input fixture checksum mismatch")
    patched = patch_gdsc(original, "fixture")
    if sha256_text(patched) != EXPECTED_AFTER_SHA256:
        raise AssertionError("Phase 232 output checksum mismatch")
    if patch_gdsc(patched, "fixture/idempotent") != patched:
        raise AssertionError("Phase 232 patch is not idempotent")
    if patched.count('"gpu_gx_gdsc"') != 2:
        raise AssertionError("Phase 232 GPU GX whitelist is not exact")
    if '"gpu_cx_gdsc"' in patched:
        raise AssertionError("Phase 232 unexpectedly claims GPU CX")
    print("Phase 232 exact GPU GX optional-AON self-test: PASS")


def main() -> int:
    base = Path(__file__).with_name("231_phase230_gpu_gdsc_wrapper.py")
    if not base.is_file():
        raise SystemExit(f"missing Phase 231 base wrapper: {base}")
    completed = subprocess.run(
        [sys.executable, str(base), *sys.argv[1:]], check=False
    )
    if completed.returncode:
        return completed.returncode
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = patch_generated(sys.argv[1:])
    print(f"Phase 232 exact GPU GX optional-AON fix applied to {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
