#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

DEFAULT_PATH = Path("scripts/233_package.py")
OLD = '        "# CONFIG_FB_MSM is not set",\n'
NEW = '        "# CONFIG_FB is not set",\n'


def repair(path: Path) -> None:
    if not path.is_file():
        raise SystemExit(f"missing materialized Phase 233 packager: {path}")

    text = path.read_text(encoding="utf-8")
    if OLD not in text and NEW in text:
        print("Phase 233 FB_MSM audit already parent-aware")
        return

    count = text.count(OLD)
    if count != 1:
        raise SystemExit(
            "expected exactly one stale Phase 233 FB_MSM parity token, "
            f"found {count}"
        )

    text = text.replace(OLD, NEW, 1)
    path.write_text(text, encoding="utf-8")

    verify = path.read_text(encoding="utf-8")
    if OLD in verify or verify.count(NEW) != 1:
        raise SystemExit("Phase 233 FB_MSM audit repair verification failed")

    print(
        "Phase 233 final-config audit repaired: CONFIG_FB=n is the parent-level "
        "proof that legacy FB_MSM is unavailable/disabled"
    )


def self_test() -> None:
    fixture = (
        "def verify_markers(config_text):\n"
        "    for token in (\n"
        "        \"CONFIG_DRM_PANEL=y\",\n"
        "        \"# CONFIG_DRM_MSM is not set\",\n"
        "        \"# CONFIG_FB_MSM is not set\",\n"
        "    ):\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "233_package.py"
        path.write_text(fixture, encoding="utf-8")
        repair(path)
        repaired = path.read_text(encoding="utf-8")
        assert OLD not in repaired
        assert repaired.count(NEW) == 1
        repair(path)
        assert path.read_text(encoding="utf-8") == repaired
    print("Phase 239 Phase 233 FB_MSM audit repair self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()

    if args.self_test:
        self_test()
    else:
        repair(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
