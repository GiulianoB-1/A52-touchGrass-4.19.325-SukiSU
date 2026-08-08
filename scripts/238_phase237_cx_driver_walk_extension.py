#!/usr/bin/env python3
"""Retain the exact pre-platform-match driver walk for GPU CX.

The second Phase 238 hardware capture retained the Phase 230 journal but showed
no 3d9106c/a52-legacy-gdsc-regulator pair records. Phase 230 already records
platform_match() and both device-side/driver-side attach paths once that exact
pair is visited, so the remaining blind spot is earlier: whether CX's device
attach walk runs, which OF-matching driver is encountered first, and whether an
earlier matching driver's probe stops the walk before the A52 compatibility
provider is reached.

This overlay is diagnostic only. It does not alter match/probe return values,
driver ordering, deferred-probe decisions, device links, or provider state.
It reuses the proven KGPPOST 230 replay journal and raises that bounded journal
from 96 to 128 records so the additional early CX records cannot crowd out the
already-useful KGSL supplier replay.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

DD = Path("drivers/base/dd.c")
RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MARKER = "A52_PHASE238_CX_DRIVER_WALK_V1"

HELPER_ANCHOR = r'''/* A52_PHASE238_CX_JOURNAL_DD_V1 */
static bool a52_r238_cx_dd_pair(const struct device *dev,
		const struct device_driver *drv)
{
	return dev && dev->of_node && dev_name(dev) && drv && drv->name &&
		strstr(dev_name(dev), "3d9106c") &&
		!strcmp(drv->name, "a52-legacy-gdsc-regulator");
}
'''

HELPER_NEW = HELPER_ANCHOR + r'''
/* A52_PHASE238_CX_DRIVER_WALK_V1 */
static atomic_t a52_r238_cx_walk_records = ATOMIC_INIT(0);

static bool a52_r238_cx_walk_dev(const struct device *dev)
{
	return dev && dev->of_node && dev_name(dev) &&
		strstr(dev_name(dev), "3d9106c");
}

static bool a52_r238_cx_walk_drv(const struct device_driver *drv)
{
	return drv && drv->name &&
		!strcmp(drv->name, "a52-legacy-gdsc-regulator");
}

#define A52_R238_CXW(_fmt, ...) \
	do { \
		if (atomic_inc_return(&a52_r238_cx_walk_records) <= 24) \
			A52_R230_DD("cxw " _fmt, ##__VA_ARGS__); \
	} while (0)
'''

INCLUDE_OLD = "#include <linux/module.h>\n"
INCLUDE_NEW = "#include <linux/module.h>\n#include <linux/of_device.h>\n"

ATTACH_DRIVER_OLD = r'''static int __device_attach_driver(struct device_driver *drv, void *_data)
{
	struct device_attach_data *data = _data;
	struct device *dev = data->dev;
	bool async_allowed;
	int ret;

	ret = driver_match_device(drv, dev);
	if (a52_r230_gpu_pair(dev, drv))
'''

ATTACH_DRIVER_NEW = r'''static int __device_attach_driver(struct device_driver *drv, void *_data)
{
	struct device_attach_data *data = _data;
	struct device *dev = data->dev;
	bool async_allowed;
	bool a52_r238_of_match = false;
	int ret;

	if (a52_r238_cx_walk_dev(dev)) {
		a52_r238_of_match = of_driver_match_device(dev, drv);
		if (a52_r238_of_match || a52_r238_cx_walk_drv(drv))
			A52_R238_CXW("cand r=%.24s of=%d",
				drv && drv->name ? drv->name : "-",
				a52_r238_of_match);
	}
	ret = driver_match_device(drv, dev);
	if (a52_r238_cx_walk_dev(dev) &&
	    (a52_r238_of_match || a52_r238_cx_walk_drv(drv)))
		A52_R238_CXW("match r=%.24s rc=%d",
			drv && drv->name ? drv->name : "-", ret);
	if (a52_r230_gpu_pair(dev, drv))
'''

PROBE_OLD = r'''	ret = driver_probe_device(drv, dev);
	if (a52_r230_gpu_pair(dev, drv))
		A52_R230_DD("ad path=dev probe=%d async=%d", ret, async_allowed);
	return ret;
}
'''

PROBE_NEW = r'''	ret = driver_probe_device(drv, dev);
	if (a52_r238_cx_walk_dev(dev) &&
	    (a52_r238_of_match || a52_r238_cx_walk_drv(drv)))
		A52_R238_CXW("probe r=%.24s rc=%d",
			drv && drv->name ? drv->name : "-", ret);
	if (a52_r230_gpu_pair(dev, drv))
		A52_R230_DD("ad path=dev probe=%d async=%d", ret, async_allowed);
	return ret;
}
'''

DEVICE_ATTACH_OLD = r'''static int __device_attach(struct device *dev, bool allow_async)
{
	int ret = 0;
	bool async = false;

'''

DEVICE_ATTACH_NEW = r'''static int __device_attach(struct device *dev, bool allow_async)
{
	int ret = 0;
	bool async = false;

	if (a52_r238_cx_walk_dev(dev))
		A52_R238_CXW("attach-in a=%d dead=%d cur=%.20s",
			allow_async, dev->p ? dev->p->dead : -1,
			dev->driver && dev->driver->name ? dev->driver->name : "-");

'''

DEVICE_ATTACH_RETURN_OLD = r'''	if (a52_smmu_unsec_trace_dev(dev))
		a52_ackfr_record("DCORE attach exit ret=%d driver=%d async=%d",
			ret, !!dev->driver, async);
	return ret;
}
'''

DEVICE_ATTACH_RETURN_NEW = r'''	if (a52_smmu_unsec_trace_dev(dev))
		a52_ackfr_record("DCORE attach exit ret=%d driver=%d async=%d",
			ret, !!dev->driver, async);
	if (a52_r238_cx_walk_dev(dev))
		A52_R238_CXW("attach-out rc=%d async=%d cur=%.20s",
			ret, async,
			dev->driver && dev->driver->name ? dev->driver->name : "-");
	return ret;
}
'''

DRIVER_WALK_OLD = r'''int driver_attach(struct device_driver *drv)
{
	int ret;

	if (a52_r230_gpu_drv(drv))
		A52_R230_DD("walk-in r=%.16s bus=%.16s", drv->name,
			drv->bus && drv->bus->name ? drv->bus->name : "-");
	ret = bus_for_each_dev(drv->bus, NULL, drv, __driver_attach);
	if (a52_r230_gpu_drv(drv))
		A52_R230_DD("walk-out r=%.16s rc=%d", drv->name, ret);
	return ret;
}
'''

DRIVER_WALK_NEW = r'''int driver_attach(struct device_driver *drv)
{
	int ret;

	if (a52_r230_gpu_drv(drv) || a52_r238_cx_walk_drv(drv))
		A52_R230_DD("walk-in r=%.16s bus=%.16s", drv->name,
			drv->bus && drv->bus->name ? drv->bus->name : "-");
	ret = bus_for_each_dev(drv->bus, NULL, drv, __driver_attach);
	if (a52_r230_gpu_drv(drv) || a52_r238_cx_walk_drv(drv))
		A52_R230_DD("walk-out r=%.16s rc=%d", drv->name, ret);
	return ret;
}
'''

JOURNAL_OLD = "#define A52_R230_JOURNAL_CAPACITY 96U\n"
JOURNAL_NEW = "#define A52_R230_JOURNAL_CAPACITY 128U /* A52_PHASE238_CX_DRIVER_WALK_JOURNAL_V1 */\n"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_dd(text: str, label: str) -> str:
    if MARKER in text:
        return text
    if "#include <linux/of_device.h>" not in text:
        text = replace_once(text, INCLUDE_OLD, INCLUDE_NEW, f"{label}: of_device include")
    text = replace_once(text, HELPER_ANCHOR, HELPER_NEW, f"{label}: helper")
    text = replace_once(text, ATTACH_DRIVER_OLD, ATTACH_DRIVER_NEW,
                        f"{label}: device-side candidate")
    text = replace_once(text, PROBE_OLD, PROBE_NEW, f"{label}: device-side probe")
    text = replace_once(text, DEVICE_ATTACH_OLD, DEVICE_ATTACH_NEW,
                        f"{label}: attach entry")
    text = replace_once(text, DEVICE_ATTACH_RETURN_OLD, DEVICE_ATTACH_RETURN_NEW,
                        f"{label}: attach exit")
    text = replace_once(text, DRIVER_WALK_OLD, DRIVER_WALK_NEW,
                        f"{label}: driver walk")
    for token in (
        MARKER,
        'A52_R238_CXW("cand r=%.24s of=%d"',
        'A52_R238_CXW("match r=%.24s rc=%d"',
        'A52_R238_CXW("probe r=%.24s rc=%d"',
        'A52_R238_CXW("attach-in a=%d dead=%d cur=%.20s"',
        'A52_R238_CXW("attach-out rc=%d async=%d cur=%.20s"',
        'a52_r230_gpu_drv(drv) || a52_r238_cx_walk_drv(drv)',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    return text


def patch_recorder(text: str, label: str) -> str:
    if JOURNAL_NEW in text:
        return text
    return replace_once(text, JOURNAL_OLD, JOURNAL_NEW, label)


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
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def root_matches(root: Path) -> bool:
    dd = root / DD
    recorder = root / RECORDER
    if not dd.is_file() or not recorder.is_file():
        return False
    dtext = dd.read_text(encoding="utf-8")
    rtext = recorder.read_text(encoding="utf-8")
    if "A52_PHASE238_CX_JOURNAL_DD_V1" not in dtext:
        return False
    if "A52_PHASE230_KGSL_LATE_REPLAY" not in rtext:
        return False
    if MARKER in dtext:
        return dtext.count(MARKER) == 1 and JOURNAL_NEW in rtext
    return HELPER_ANCHOR in dtext and JOURNAL_OLD in rtext


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    matches = [root for root in candidate_roots(args, base) if root_matches(root)]
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
            "expected exactly one generated Phase 238 CX journal source root, "
            f"found {len(unique)}: {rendered}"
        )
    return unique[0]


def self_test() -> None:
    dd_fixture = (
        INCLUDE_OLD
        + "\n"
        + HELPER_ANCHOR
        + "\n"
        + ATTACH_DRIVER_OLD
        + "\t\tA52_R230_DD(\"fixture\");\n"
        + PROBE_OLD
        + "\n"
        + DEVICE_ATTACH_OLD
        + DEVICE_ATTACH_RETURN_OLD
        + "\n"
        + DRIVER_WALK_OLD
    )
    patched = patch_dd(dd_fixture, "fixture/dd.c")
    if patch_dd(patched, "fixture/dd.c/idempotent") != patched:
        raise AssertionError("CX driver-walk DD patch is not idempotent")
    recorder = "/* A52_PHASE230_KGSL_LATE_REPLAY */\n" + JOURNAL_OLD
    recorder_patched = patch_recorder(recorder, "fixture/recorder.c")
    if patch_recorder(recorder_patched, "fixture/recorder.c/idempotent") != recorder_patched:
        raise AssertionError("CX driver-walk journal patch is not idempotent")
    if "128U" not in recorder_patched:
        raise AssertionError("CX driver-walk journal capacity did not increase")

    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        generated = repo / "gki/common"
        dd = generated / DD
        rec = generated / RECORDER
        dd.parent.mkdir(parents=True, exist_ok=True)
        rec.parent.mkdir(parents=True, exist_ok=True)
        dd.write_text(dd_fixture, encoding="utf-8")
        rec.write_text(recorder, encoding="utf-8")
        found = locate_generated([], cwd=repo)
        if found.resolve() != generated.resolve():
            raise AssertionError(f"locator chose {found}, expected {generated}")

    print("Phase 238 CX pre-match driver-walk extension self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    dd_path = root / DD
    rec_path = root / RECORDER
    dd_path.write_text(patch_dd(dd_path.read_text(encoding="utf-8"), str(dd_path)),
                       encoding="utf-8")
    rec_path.write_text(
        patch_recorder(rec_path.read_text(encoding="utf-8"), str(rec_path)),
        encoding="utf-8",
    )
    print(
        "Phase 238 CX pre-match driver-walk extension applied: exact CX attach, "
        "OF-matching candidates, probe returns, and custom-driver walk are retained",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
