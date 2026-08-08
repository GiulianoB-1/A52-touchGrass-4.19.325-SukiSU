#!/usr/bin/env python3
"""Extend the inherited Phase 230 late journal to the exact GPU CX GDSC.

Phase 238 hardware proved KGSL's first unavailable managed supplier is
3d9106c.qcom,gdsc.  The broad G238 records around CX can be lost in the same
mid-boot retention hole that motivated Phase 230's late journal.  Reuse that
already-proven journal for the exact CX device/driver pair and for CX's
`device_links_check_suppliers()` call.  This changes only trace selection; it
does not change matching, supplier checks, return codes, or device state.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PLATFORM = Path("drivers/base/platform.c")
DD = Path("drivers/base/dd.c")
CORE = Path("drivers/base/core.c")

PLATFORM_OLD = r'''static bool a52_r230_gpu_platform_pair(const struct device *dev,
		const struct device_driver *drv)
{
	return dev && dev->of_node && drv && drv->name &&
		of_device_is_compatible(dev->of_node, "qcom,kgsl-3d0") &&
		!strcmp(drv->name, "kgsl-3d");
}
'''

PLATFORM_NEW = r'''/* A52_PHASE238_CX_JOURNAL_PLATFORM_V1 */
static bool a52_r238_cx_platform_pair(const struct device *dev,
		const struct device_driver *drv)
{
	return dev && dev->of_node && dev_name(dev) && drv && drv->name &&
		strstr(dev_name(dev), "3d9106c") &&
		!strcmp(drv->name, "a52-legacy-gdsc-regulator");
}

static bool a52_r230_gpu_platform_pair(const struct device *dev,
		const struct device_driver *drv)
{
	return (dev && dev->of_node && drv && drv->name &&
		of_device_is_compatible(dev->of_node, "qcom,kgsl-3d0") &&
		!strcmp(drv->name, "kgsl-3d")) ||
		a52_r238_cx_platform_pair(dev, drv);
}
'''

DD_OLD = r'''static bool a52_r230_gpu_pair(const struct device *dev,
		const struct device_driver *drv)
{
	return a52_r230_gpu_dev(dev) && a52_r230_gpu_drv(drv);
}
'''

DD_NEW = r'''/* A52_PHASE238_CX_JOURNAL_DD_V1 */
static bool a52_r238_cx_dd_pair(const struct device *dev,
		const struct device_driver *drv)
{
	return dev && dev->of_node && dev_name(dev) && drv && drv->name &&
		strstr(dev_name(dev), "3d9106c") &&
		!strcmp(drv->name, "a52-legacy-gdsc-regulator");
}

static bool a52_r230_gpu_pair(const struct device *dev,
		const struct device_driver *drv)
{
	return (a52_r230_gpu_dev(dev) && a52_r230_gpu_drv(drv)) ||
		a52_r238_cx_dd_pair(dev, drv);
}
'''

CORE_OLD = r'''static bool a52_r230_gpu_consumer(const struct device *dev)
{
	return dev && dev->of_node &&
		of_device_is_compatible(dev->of_node, "qcom,kgsl-3d0");
}
'''

CORE_NEW = r'''/* A52_PHASE238_CX_JOURNAL_CORE_V1 */
static bool a52_r230_gpu_consumer(const struct device *dev)
{
	return dev && dev->of_node &&
		(of_device_is_compatible(dev->of_node, "qcom,kgsl-3d0") ||
		 (dev_name(dev) && strstr(dev_name(dev), "3d9106c")));
}
'''

TARGETS = (
    (PLATFORM, PLATFORM_OLD, PLATFORM_NEW, "A52_PHASE238_CX_JOURNAL_PLATFORM_V1"),
    (DD, DD_OLD, DD_NEW, "A52_PHASE238_CX_JOURNAL_DD_V1"),
    (CORE, CORE_OLD, CORE_NEW, "A52_PHASE238_CX_JOURNAL_CORE_V1"),
)


def replace_exact(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one Phase 230 helper, found {count}")
    return text.replace(old, new, 1)


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        roots.extend((path, path.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def root_has_expected_sources(root: Path) -> bool:
    for rel, old, new, marker in TARGETS:
        path = root / rel
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8")
        if marker in text:
            if text.count(marker) != 1:
                return False
            continue
        if new in text or text.count(old) != 1:
            return False
    return True


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    matches = [root for root in candidate_roots(args, base)
               if root_has_expected_sources(root)]

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(root) for root in unique) or "none"
        raise RuntimeError(
            "expected exactly one generated Phase 230/238 CX journal source root, "
            f"found {len(unique)}: {rendered}"
        )
    return unique[0]


def patch_one(path: Path, old: str, new: str, marker: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"missing generated source: {path}")
    text = path.read_text(encoding="utf-8")
    patched = replace_exact(text, old, new, str(path))
    if patched.count(marker) != 1:
        raise RuntimeError(f"{path}: missing or duplicate {marker}")
    path.write_text(patched, encoding="utf-8")
    print(f"Phase 238 CX journal extension applied: {path}", flush=True)


def self_test() -> None:
    for old, new, marker, label in (
        (PLATFORM_OLD, PLATFORM_NEW, "A52_PHASE238_CX_JOURNAL_PLATFORM_V1", "platform"),
        (DD_OLD, DD_NEW, "A52_PHASE238_CX_JOURNAL_DD_V1", "dd"),
        (CORE_OLD, CORE_NEW, "A52_PHASE238_CX_JOURNAL_CORE_V1", "core"),
    ):
        patched = replace_exact(old, old, new, f"fixture/{label}")
        if marker not in patched or "3d9106c" not in patched:
            raise AssertionError(f"{label}: CX journal self-test failed")
        if replace_exact(patched, old, new, f"fixture/{label}/idempotent") != patched:
            raise AssertionError(f"{label}: CX journal patch is not idempotent")

    # Regression test for the inherited-builder layout.  The wrapper executes
    # from the repository root while the generated kernel tree is gki/common.
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        generated = repo / "gki/common"
        for rel, old, _new, _marker in TARGETS:
            path = generated / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(old, encoding="utf-8")
        found = locate_generated([], cwd=repo)
        if found.resolve() != generated.resolve():
            raise AssertionError(
                f"CX journal generated-root locator selected {found}, expected {generated}"
            )

    print("Phase 238 CX journal extension self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    root = locate_generated(sys.argv[1:])
    for rel, old, new, marker in TARGETS:
        patch_one(root / rel, old, new, marker)
    print(
        "Phase 238 CX journal extension: exact CX match/attach/supplier path will "
        f"reuse the inherited KGPPOST 230 late journal in {root}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
