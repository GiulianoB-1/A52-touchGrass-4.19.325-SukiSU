#!/usr/bin/env python3
"""Phase 239: relabel retained Phase 238 recorder runtime identity."""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
GDSC = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
OLD = "BOOT rs=ready phase=238 focus=gpu-supplier-broad roots=%u copies=3 crc=crc32c"
NEW = "BOOT rs=ready phase=239 focus=cx-vdd-parent-fix roots=%u copies=3 crc=crc32c"
P238 = "A52_PHASE238_BROAD_GPU_SUPPLIER_RECORDER_V1"
P239 = "A52_PHASE239_GPU_CX_VDD_PARENT_V1"
MARKER = "A52_PHASE239_RUNTIME_IDENTITY_V1"


def candidates(args: list[str], cwd: Path) -> list[Path]:
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
    found: list[Path] = []
    for root in candidates(args, base):
        recorder = root / RECORDER
        gdsc = root / GDSC
        if not recorder.is_file() or not gdsc.is_file():
            continue
        rt = recorder.read_text(encoding="utf-8")
        gt = gdsc.read_text(encoding="utf-8")
        if P238 in rt and P239 in gt and (OLD in rt or NEW in rt):
            found.append(root)
    uniq: list[Path] = []
    seen: set[Path] = set()
    for root in found:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            uniq.append(root)
    if len(uniq) != 1:
        raise RuntimeError(
            f"expected one Phase 239 generated root, found {len(uniq)}: {uniq}"
        )
    return uniq[0]


def patch(text: str, label: str) -> str:
    if MARKER in text:
        if NEW not in text or OLD in text:
            raise RuntimeError(f"{label}: invalid existing Phase 239 identity")
        return text
    if text.count(OLD) != 1:
        raise RuntimeError(
            f"{label}: expected one Phase 238 boot identity, found {text.count(OLD)}"
        )
    anchor = " * A52_PHASE238_BROAD_GPU_SUPPLIER_RECORDER_V1\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"{label}: Phase 238 marker anchor mismatch")
    text = text.replace(anchor, anchor + f" * {MARKER}\n", 1)
    text = text.replace(OLD, NEW, 1)
    return text


def self_test() -> None:
    recorder = (
        "/*\n * A52_PHASE238_BROAD_GPU_SUPPLIER_RECORDER_V1\n */\n"
        + OLD
        + "\n"
    )
    patched = patch(recorder, "fixture")
    assert MARKER in patched and NEW in patched and OLD not in patched
    assert patch(patched, "idempotent") == patched
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "gki/common"
        (root / RECORDER).parent.mkdir(parents=True)
        (root / GDSC).parent.mkdir(parents=True)
        (root / RECORDER).write_text(recorder, encoding="utf-8")
        (root / GDSC).write_text(f"/* {P239} */", encoding="utf-8")
        assert locate([], Path(temp)).resolve() == root.resolve()
    print("Phase 239 runtime identity self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    path = root / RECORDER
    path.write_text(patch(path.read_text(encoding="utf-8"), str(path)), encoding="utf-8")
    print(f"Phase 239 runtime identity applied to {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
