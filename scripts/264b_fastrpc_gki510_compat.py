#!/usr/bin/env python3
"""Phase264b: GKI 5.10 compatibility fixes for the staged Golden FastRPC layer."""
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "A52_PHASE264_FASTRPC_GKI510_COMPAT_V2"
OLD_MSM_ION_SPDX = "/* SPDX-License-Identifier: GPL-2.0-only */"
NEW_MSM_ION_SPDX = "/* SPDX-License-Identifier: GPL-2.0-only WITH Linux-syscall-note */"
LOCAL_CONFIG_FLAGS = (
    "ccflags-y += -DCONFIG_MSM_SERVICE_LOCATOR=1",
    "ccflags-y += -DCONFIG_MSM_SERVICE_NOTIFIER=1",
)
PR_FMT_FILES = (
    "drivers/a52_fastrpc/services/service-locator.c",
    "drivers/a52_fastrpc/services/service-notifier.c",
)


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


def patch_local_service_registry_contract(root: Path) -> None:
    """Expose real legacy service-registry declarations only to this subtree.

    Android 5.10 already carries the Qualcomm service-registry API headers, but
    their fallback branch provides static inline stubs unless the historical
    CONFIG_MSM_SERVICE_LOCATOR/NOTIFIER symbols are defined.  Our isolated
    A52 implementation is selected by CONFIG_A52_SERVICE_REGISTRY instead, so
    define the historical symbols as compile-time header selectors locally.
    This does not add or alter global Kconfig state.
    """
    path = root / "drivers/a52_fastrpc/Makefile"
    if not path.is_file():
        raise RuntimeError(f"Phase264b FastRPC Makefile missing: {path}")

    text = path.read_text(encoding="utf-8")
    anchor = "ccflags-y += -I$(srctree)/a52-compat/include/uapi\n"
    missing = [flag for flag in LOCAL_CONFIG_FLAGS if flag not in text]
    if not missing:
        return
    if text.count(anchor) != 1:
        raise RuntimeError(
            "Phase264b FastRPC ccflags anchor drifted: "
            f"{text.count(anchor)}"
        )
    block = "".join(flag + "\n" for flag in missing)
    text = text.replace(anchor, anchor + block, 1)
    path.write_text(text, encoding="utf-8")


def patch_pr_fmt_after_forced_include(root: Path, rel: str) -> None:
    """Avoid printk's default pr_fmt colliding with the legacy source macro."""
    path = root / rel
    if not path.is_file():
        raise RuntimeError(f"Phase264b service-registry source missing: {path}")

    text = path.read_text(encoding="utf-8")
    if "#undef pr_fmt\n" in text:
        return

    lines = text.splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines) if line.startswith("#define pr_fmt(fmt)")]
    if len(indices) != 1:
        raise RuntimeError(
            f"Phase264b {rel} pr_fmt anchor drifted: {len(indices)}"
        )

    i = indices[0]
    lines[i:i] = ["#ifdef pr_fmt\n", "#undef pr_fmt\n", "#endif\n"]
    path.write_text("".join(lines), encoding="utf-8")


def verify(root: Path) -> None:
    ion = root / "include/uapi/linux/msm_ion.h"
    first = ion.read_text(encoding="utf-8").splitlines()[0]
    if first != NEW_MSM_ION_SPDX:
        raise RuntimeError(f"Phase264b msm_ion syscall-note SPDX missing: {first!r}")

    makefile = (root / "drivers/a52_fastrpc/Makefile").read_text(encoding="utf-8")
    for flag in LOCAL_CONFIG_FLAGS:
        if makefile.count(flag) != 1:
            raise RuntimeError(f"Phase264b local service-registry flag missing/duplicated: {flag}")

    for rel in PR_FMT_FILES:
        text = (root / rel).read_text(encoding="utf-8")
        if text.count("#undef pr_fmt\n") != 1:
            raise RuntimeError(f"Phase264b pr_fmt guard missing/duplicated: {rel}")
        undef = text.index("#undef pr_fmt\n")
        define = text.index("#define pr_fmt(fmt)")
        if undef > define:
            raise RuntimeError(f"Phase264b pr_fmt guard ordered after definition: {rel}")


def self_test() -> None:
    sample = OLD_MSM_ION_SPDX + "\n#define TEST 1\n"
    patched = sample.replace(OLD_MSM_ION_SPDX, NEW_MSM_ION_SPDX, 1)
    assert patched.splitlines()[0] == NEW_MSM_ION_SPDX
    assert "Linux-syscall-note" in patched

    mk = "ccflags-y += -I$(srctree)/a52-compat/include/uapi\n"
    anchor = mk
    block = "".join(flag + "\n" for flag in LOCAL_CONFIG_FLAGS)
    mk = mk.replace(anchor, anchor + block, 1)
    assert all(mk.count(flag) == 1 for flag in LOCAL_CONFIG_FLAGS)

    src = '#define pr_fmt(fmt) "servloc: " fmt\n#include <linux/kernel.h>\n'
    lines = src.splitlines(keepends=True)
    i = next(i for i, line in enumerate(lines) if line.startswith("#define pr_fmt(fmt)"))
    lines[i:i] = ["#ifdef pr_fmt\n", "#undef pr_fmt\n", "#endif\n"]
    src = "".join(lines)
    assert src.index("#undef pr_fmt") < src.index("#define pr_fmt(fmt)")

    print("Phase 264b FastRPC GKI 5.10 compatibility self-test: PASS", flush=True)


def apply(root: Path) -> None:
    patch_msm_ion_uapi(root)
    patch_local_service_registry_contract(root)
    for rel in PR_FMT_FILES:
        patch_pr_fmt_after_forced_include(root, rel)
    verify(root)
    print(
        f"{MARKER}: msm_ion metadata + isolated service-registry header/pr_fmt compatibility applied",
        flush=True,
    )


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
