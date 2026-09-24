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

marker = "/* A52 P153: default all initial driver probing to asynchronous */"
if marker in s:
    print("A52 P153 global async probe policy already applied")
    raise SystemExit(0)

old = """	default:
		if (module_requested_async_probing(drv->owner))
			return true;

		return false;
	}
}
"""

new = """	default:
		/*
		 * A52 P153: default all initial driver probing to asynchronous.
		 *
		 * This is an intentionally aggressive boot-time experiment. The
		 * driver core still honors PROBE_FORCE_SYNCHRONOUS if a driver
		 * explicitly requires synchronous probing. This tree currently has
		 * no such driver declarations, so ordinary built-in drivers become
		 * asynchronous during their initial bind.
		 *
		 * Deferred probing and wait_for_device_probe() remain unchanged.
		 */
		return true;
	}
}
"""

count = s.count(old)
if count != 1:
    raise SystemExit(
        f"driver_allows_async_probing default anchor: expected exactly one, found {count}"
    )

s = s.replace(old, new, 1)

for required in (
    marker,
    "case PROBE_PREFER_ASYNCHRONOUS:",
    "case PROBE_FORCE_SYNCHRONOUS:",
    "return true;",
    "async_synchronize_full();",
):
    if required not in s:
        raise SystemExit(f"P153 structural audit missing: {required}")

# Make sure we changed only the default policy, not the explicit forced-sync path.
force_block = """	case PROBE_FORCE_SYNCHRONOUS:
		return false;
"""
if force_block not in s:
    raise SystemExit("P153 must retain PROBE_FORCE_SYNCHRONOUS semantics")

dd.write_text(s)

print("A52 P153: global asynchronous initial probing policy applied")
print("  default driver probe policy: asynchronous")
print("  PROBE_PREFER_ASYNCHRONOUS: asynchronous")
print("  PROBE_FORCE_SYNCHRONOUS: still synchronous")
print("  deferred probe logic: unchanged")
print("  wait_for_device_probe()/async_synchronize_full(): unchanged")
