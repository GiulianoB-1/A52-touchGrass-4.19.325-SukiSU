#!/usr/bin/env python3
"""Phase262: golden-parity firmware-loader userspace fallback A/B."""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE262_FW_LOADER_FALLBACK_AB_V1"
TARGET_OFF = "# CONFIG_FW_LOADER_USER_HELPER_FALLBACK is not set"
TARGET_ON = "CONFIG_FW_LOADER_USER_HELPER_FALLBACK=y"
REQUIRED = (
    "CONFIG_FW_LOADER=y",
    "CONFIG_FW_LOADER_USER_HELPER=y",
    "CONFIG_SCSI=y",
    "CONFIG_CHR_DEV_SG=y",
    "CONFIG_QCOM_KGSL=y",
    "CONFIG_QCOM_KGSL_IOMMU=y",
)


def locate_config(root: Path) -> Path:
    root = root.resolve()
    candidates = [
        root.parent.parent / "workspace/gki-phase199-out/.config",
        Path.cwd() / "workspace/gki-phase199-out/.config",
        root / ".config",
    ]
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            return path
    raise RuntimeError("Phase262 authoritative .config not found: " + ", ".join(str(p) for p in candidates))


def apply(root: Path) -> None:
    cfg = locate_config(root)
    text = cfg.read_text(encoding="utf-8")
    for item in REQUIRED:
        if item not in text:
            raise RuntimeError(f"Phase262 invariant missing before A/B: {item}")
    if TARGET_ON in text:
        print(f"{MARKER}: already enabled in {cfg}", flush=True)
        return
    count = text.count(TARGET_OFF)
    if count != 1:
        raise RuntimeError(f"Phase262 expected one fallback-off line, found {count}")
    text = text.replace(TARGET_OFF, TARGET_ON, 1)
    cfg.write_text(text, encoding="utf-8")
    verify = cfg.read_text(encoding="utf-8")
    if TARGET_ON not in verify or TARGET_OFF in verify:
        raise RuntimeError("Phase262 fallback enable did not persist in staged config")
    for item in REQUIRED:
        if item not in verify:
            raise RuntimeError(f"Phase262 invariant changed unexpectedly: {item}")
    print(f"{MARKER}: enabled {TARGET_ON} in {cfg}", flush=True)


def self_test() -> None:
    sample = "\n".join(REQUIRED + (TARGET_OFF,)) + "\n"
    changed = sample.replace(TARGET_OFF, TARGET_ON, 1)
    assert TARGET_ON in changed and TARGET_OFF not in changed
    for item in REQUIRED:
        assert item in changed
    assert MARKER in Path(__file__).read_text(encoding="utf-8")
    print("Phase 262 firmware-loader fallback A/B self-test: PASS", flush=True)


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        self_test()
        return 0
    if not argv:
        raise SystemExit("usage: 262_fw_loader_fallback_ab.py <gki/common> | --self-test")
    apply(Path(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
