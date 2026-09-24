#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

kernel = Path(sys.argv[1]).resolve()
dd = kernel / "drivers/base/dd.c"

if not dd.is_file():
    raise SystemExit(f"missing required source: {dd}")

s = dd.read_text()

marker = "/* A52 P153: force all initial driver probing asynchronous */"
if marker in s:
    print("A52 P153 global async probe policy already applied")
    raise SystemExit(0)

old = """bool driver_allows_async_probing(struct device_driver *drv)
{
	switch (drv->probe_type) {
	case PROBE_PREFER_ASYNCHRONOUS:
		return true;

	case PROBE_FORCE_SYNCHRONOUS:
		return false;

	default:
		if (module_requested_async_probing(drv->owner))
			return true;

		return false;
	}
}
"""

new = """bool driver_allows_async_probing(struct device_driver *drv)
{
	/*
	 * A52 P153: force all initial driver probing asynchronous.
	 *
	 * This phase is deliberately maximal so ordering-sensitive drivers become
	 * obvious in one test. The attach path, deferred-probe machinery and
	 * wait_for_device_probe()/async_synchronize_full() are left untouched.
	 */
	return true;
}
"""

count = s.count(old)
if count != 1:
    raise SystemExit(
        f"driver_allows_async_probing function anchor: expected exactly one, found {count}"
    )

s = s.replace(old, new, 1)

for required in (
    marker,
    "bool driver_allows_async_probing(struct device_driver *drv)",
    "return true;",
    "async_synchronize_full();",
    "deferred_probe_work",
):
    if required not in s:
        raise SystemExit(f"P153 structural audit missing: {required}")

for forbidden in (
    "case PROBE_FORCE_SYNCHRONOUS:",
    "module_requested_async_probing(drv->owner)",
):
    start = s.find("bool driver_allows_async_probing(struct device_driver *drv)")
    end = s.find("struct device_attach_data", start)
    body = s[start:end]
    if forbidden in body:
        raise SystemExit(f"P153 global policy still contains old gate: {forbidden}")

dd.write_text(s)

print("A52 P153: ALL initial driver probing forced asynchronous")
print("  PROBE_PREFER_ASYNCHRONOUS: asynchronous")
print("  PROBE_FORCE_SYNCHRONOUS: overridden to asynchronous for this experiment")
print("  unspecified/default drivers: asynchronous")
print("  deferred probe logic: unchanged")
print("  wait_for_device_probe()/async_synchronize_full(): unchanged")
