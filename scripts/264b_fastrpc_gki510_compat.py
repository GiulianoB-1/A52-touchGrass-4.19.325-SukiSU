#!/usr/bin/env python3
"""Phase264b: GKI 5.10 compatibility fixes for the staged Golden FastRPC layer."""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE264_FASTRPC_GKI510_COMPAT_V1"
OLD_MSM_ION_SPDX = "/* SPDX-License-Identifier: GPL-2.0-only */"
NEW_MSM_ION_SPDX = "/* SPDX-License-Identifier: GPL-2.0-only WITH Linux-syscall-note */"


def locate(argv: list[str]) -> Path:
    if argv and argv[0] != "--self-test":
        return Path(argv[0]).resolve()
    return Path("gki/common").resolve()


def patch_msm_ion_uapi(root: Path) -> None:
    path = root / "include/uapi/linux/msm_ion.h"
    if not path.is_file():
        raise RuntimeError(f"Phase264b staged msm_ion UAPI header missing: {path}")

    text = path.read_text(encoding="utf-8")
    if NEW_MSM_ION_SPDX in text:
        return
    if text.count(OLD_MSM_ION_SPDX) != 1:
        raise RuntimeError(
            "Phase264b msm_ion SPDX anchor drifted: "
            f"{text.count(OLD_MSM_ION_SPDX)}"
        )
    text = text.replace(OLD_MSM_ION_SPDX, NEW_MSM_ION_SPDX, 1)
    path.write_text(text, encoding="utf-8")


def verify(root: Path) -> None:
    text = (root / "include/uapi/linux/msm_ion.h").read_text(encoding="utf-8")
    first = text.splitlines()[0]
    if first != NEW_MSM_ION_SPDX:
        raise RuntimeError(f"Phase264b msm_ion syscall-note SPDX missing: {first!r}")


def self_test() -> None:
    sample = OLD_MSM_ION_SPDX + "\n#define TEST 1\n"
    patched = sample.replace(OLD_MSM_ION_SPDX, NEW_MSM_ION_SPDX, 1)
    assert patched.splitlines()[0] == NEW_MSM_ION_SPDX
    assert "Linux-syscall-note" in patched
    print("Phase 264b FastRPC GKI 5.10 compatibility self-test: PASS", flush=True)


def apply(root: Path) -> None:
    patch_msm_ion_uapi(root)
    verify(root)
    print(f"{MARKER}: msm_ion UAPI metadata adapted to Android 5.10", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
