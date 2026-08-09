#!/usr/bin/env python3
"""Phase 245: make inferred firmware device links permissive by default.

This is a single-variable functional A/B experiment over the Phase 243 kernel
state.  It changes only the initial value of fw_devlink_flags from
FW_DEVLINK_FLAGS_ON to FW_DEVLINK_FLAGS_PERMISSIVE.  DT, GDSC provider code,
KGSL, GPUCC, initcall ordering, boot cmdline, and recorder hooks are untouched.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

CORE = Path("drivers/base/core.c")
RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
PHASE243_IDENTITY = "A52_PHASE243_CXGX_LIVE_SUPPLIER_V1"
OLD = "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_ON;"
NEW = "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;"


def patch_core(text: str, label: str) -> str:
    old_count = text.count(OLD)
    new_count = text.count(NEW)
    if old_count == 0 and new_count == 1:
        return text
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            f"{label}: expected exactly one ON declaration and no permissive declaration; "
            f"found on={old_count} permissive={new_count}"
        )
    patched = text.replace(OLD, NEW, 1)
    validate_core(patched, label)
    return patched


def validate_core(text: str, label: str) -> None:
    if text.count(NEW) != 1:
        raise RuntimeError(f"{label}: permissive declaration count is {text.count(NEW)}, expected 1")
    if OLD in text:
        raise RuntimeError(f"{label}: stale FW_DEVLINK_FLAGS_ON declaration remains")
    # Preserve the parameter machinery.  We are changing only the compiled default.
    for token in ("fw_devlink", "FW_DEVLINK_FLAGS_PERMISSIVE"):
        if token not in text:
            raise RuntimeError(f"{label}: missing expected fw_devlink token {token}")


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
        core = root / CORE
        recorder = root / RECORDER
        if not core.is_file() or not recorder.is_file():
            continue
        if PHASE243_IDENTITY not in recorder.read_text(encoding="utf-8"):
            continue
        text = core.read_text(encoding="utf-8")
        if OLD not in text and NEW not in text:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(
            "expected exactly one generated Phase 243 root containing fw_devlink default; found "
            + (", ".join(map(str, hits)) or "none")
        )
    return hits[0]


def self_test() -> None:
    fixture = (
        "#include <linux/device.h>\n"
        "#define FW_DEVLINK_FLAGS_PERMISSIVE 0x1\n"
        "#define FW_DEVLINK_FLAGS_ON 0x2\n"
        + OLD + "\n"
        "static int __init fw_devlink_setup(char *arg) { return 0; }\n"
        "early_param(\"fw_devlink\", fw_devlink_setup);\n"
    )
    patched = patch_core(fixture, "fixture/core.c")
    assert patched.count(NEW) == 1
    assert OLD not in patched
    assert patch_core(patched, "fixture/core.c/idempotent") == patched

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "gki/common"
        (root / CORE).parent.mkdir(parents=True)
        (root / RECORDER).parent.mkdir(parents=True)
        (root / CORE).write_text(fixture, encoding="utf-8")
        (root / RECORDER).write_text(PHASE243_IDENTITY + "\n", encoding="utf-8")
        found = locate([], Path(td))
        assert found.resolve() == root.resolve()
    print("Phase 245 fw_devlink permissive overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    path = root / CORE
    before = path.read_text(encoding="utf-8")
    after = patch_core(before, str(path))
    path.write_text(after, encoding="utf-8")
    print(
        "Phase 245 fw_devlink A/B applied: "
        "FW_DEVLINK_FLAGS_ON -> FW_DEVLINK_FLAGS_PERMISSIVE",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
