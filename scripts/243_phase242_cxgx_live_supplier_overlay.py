#!/usr/bin/env python3
"""Phase 243: live CX/GX match and own-supplier corridor.

Phase 241 proved GX binds while CX remains unbound. Phase 242's late snapshots
were not trustworthy because persistent late slots were stale. Phase 243
therefore records only live, phase-unique, critical evidence at the exact GX/CX
platform match, really_probe supplier gate, supplier list, and provider-probe
entry. Each record is emitted three times into adjacent logical sequence slots.
Phase 242 sticky latch/snapshot runtime hooks are disabled. No match result,
supplier link, probe return, deferred-probe decision, driver order, provider
behavior, or recorder transport is changed.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
PLATFORM = Path("drivers/base/platform.c")
DD = Path("drivers/base/dd.c")
GDSC = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
MARKER = "A52_PHASE243_CXGX_LIVE_SUPPLIER_V1"
DISABLE242 = "A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, pattern: str, label: str) -> tuple[int, int]:
    m = re.search(pattern, text)
    if not m:
        raise RuntimeError(f"{label}: function not found")
    brace = text.find("{", m.start())
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace missing")
    depth = 0
    i = brace
    state = "code"
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == '"': state = "str"
            elif c == "'": state = "char"
            elif c == "/" and n == "/": state = "line"; i += 1
            elif c == "/" and n == "*": state = "block"; i += 1
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), i + 1
        elif state == "str":
            if c == "\\": i += 1
            elif c == '"': state = "code"
        elif state == "char":
            if c == "\\": i += 1
            elif c == "'": state = "code"
        elif state == "line":
            if c == "\n": state = "code"
        elif state == "block":
            if c == "*" and n == "/": state = "code"; i += 1
        i += 1
    raise RuntimeError(f"{label}: closing brace missing")


REC_MARK_OLD = "\t * A52_PHASE242_CX_STICKY_STATE_IDENTITY_V1\n"
REC_MARK_NEW = REC_MARK_OLD + f"\t * {MARKER}\n\t * {DISABLE242}\n"
FILTER_OLD = 'if (strncmp(fmt, "CXF242", 6) &&\n'
FILTER_NEW = 'if (strncmp(fmt, "CXF243", 6) &&\n\t    strncmp(fmt, "CXF242", 6) &&\n'
CRIT_OLD = 'return !strncmp(message, "CXF242 ", 7) ||\n'
CRIT_NEW = 'return !strncmp(message, "CXF243 ", 7) ||\n\t       !strncmp(message, "CXF242 ", 7) ||\n'
LATCH_OLD = "\ta52_r242_sticky_latch(event.message);\n\ta52_r241_corridor_latch(event.message);\n"
LATCH_NEW = f"\t/* {DISABLE242}: no sticky parsing in Phase 243 */\n\ta52_r241_corridor_latch(event.message);\n"
SNAP_OLD = "\ta52_r242_snapshot(tick);\n\ta52_ackfr_record(\"HB tick=%u online=%u run=%lu j=%lu\", tick,\n"
SNAP_NEW = f"\t/* {DISABLE242}: no Phase 242 heartbeat snapshot */\n\ta52_ackfr_record(\"HB tick=%u online=%u run=%lu j=%lu\", tick,\n"


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        validate_recorder(text, label)
        return text
    if "A52_PHASE242_CX_STICKY_STATE_IDENTITY_V1" not in text:
        raise RuntimeError(f"{label}: Phase 242 identity missing")
    text = one(text, REC_MARK_OLD, REC_MARK_NEW, f"{label}: marker")
    text = one(text, FILTER_OLD, FILTER_NEW, f"{label}: format filter")
    text = one(text, CRIT_OLD, CRIT_NEW, f"{label}: critical filter")
    text = one(text, LATCH_OLD, LATCH_NEW, f"{label}: disable sticky latch")
    text = one(text, SNAP_OLD, SNAP_NEW, f"{label}: disable sticky snapshot")
    if "static void __maybe_unused a52_r242_sticky_latch" not in text:
        text = one(text, "static void a52_r242_sticky_latch(const char *message)",
                   "static void __maybe_unused a52_r242_sticky_latch(const char *message)",
                   f"{label}: sticky maybe-unused")
    if "static void __maybe_unused a52_r242_snapshot(unsigned int tick)" not in text:
        text = one(text, "static void a52_r242_snapshot(unsigned int tick)",
                   "static void __maybe_unused a52_r242_snapshot(unsigned int tick)",
                   f"{label}: snapshot maybe-unused")
    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (MARKER, DISABLE242, 'strncmp(fmt, "CXF243", 6)',
                  'return !strncmp(message, "CXF243 ", 7) ||',
                  '__maybe_unused a52_r242_sticky_latch',
                  '__maybe_unused a52_r242_snapshot'):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    record = text.find("void a52_ackfr_record(const char *fmt, ...)")
    if record < 0:
        raise RuntimeError(f"{label}: record function missing")
    if text.find("a52_r242_sticky_latch(event.message);", record) >= 0:
        raise RuntimeError(f"{label}: Phase242 sticky latch still live")
    fn = text.find("static void a52_r179_heartbeat_fn")
    end = text.find("static int __init a52_r179_early_heartbeat", fn)
    if fn >= 0 and end >= 0 and "a52_r242_snapshot(tick);" in text[fn:end]:
        raise RuntimeError(f"{label}: Phase242 snapshot still live")


PLATFORM_HELPER = r'''/* A52_PHASE243_CXGX_LIVE_SUPPLIER_V1 */
static char a52_r243_gdsc_tag(const struct device *dev,
		const struct device_driver *drv)
{
	const char *name;

	if (!dev || !drv || !drv->name ||
	    strcmp(drv->name, "a52-legacy-gdsc-regulator"))
		return 0;
	name = dev_name(dev);
	if (!name)
		return 0;
	if (strstr(name, "3d9106c"))
		return 'C';
	if (strstr(name, "3d9100c"))
		return 'G';
	return 0;
}

static void a52_r243_match3(struct device *dev, struct device_driver *drv,
		int rc)
{
	char tag = a52_r243_gdsc_tag(dev, drv);
	int q;

	if (!tag)
		return;
	for (q = 0; q < 3; q++)
		a52_ackfr_record("CXF243 M c=%c q=%d rc=%d", tag, q, rc);
}

'''


def patch_platform(text: str, label: str) -> str:
    if MARKER in text:
        validate_platform(text, label)
        return text
    include = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if include not in text:
        anchor = "#include <linux/device.h>\n"
        text = one(text, anchor, anchor + include, f"{label}: recorder include")
    start, end = function_bounds(text, r"static\s+int\s+platform_match\s*\(", f"{label}: platform_match")
    text = text[:start] + PLATFORM_HELPER + text[start:]
    start, end = function_bounds(text, r"static\s+int\s+platform_match\s*\(", f"{label}: platform_match patched")
    fn = text[start:end]
    idx = fn.rfind("\treturn ret;\n")
    if idx < 0:
        idx = fn.rfind("\treturn ret;\r\n")
    if idx < 0:
        raise RuntimeError(f"{label}: platform_match final return missing")
    fn = fn[:idx] + "\ta52_r243_match3(dev, drv, ret);\n" + fn[idx:]
    text = text[:start] + fn + text[end:]
    validate_platform(text, label)
    return text


def validate_platform(text: str, label: str) -> None:
    for token in (MARKER, "a52_r243_match3(dev, drv, ret);",
                  'CXF243 M c=%c q=%d rc=%d', '3d9106c', '3d9100c'):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


DD_HELPER = r'''/* A52_PHASE243_CXGX_LIVE_SUPPLIER_V1 */
static char a52_r243_dd_tag(const struct device *dev,
		const struct device_driver *drv)
{
	const char *name;

	if (!dev || !drv || !drv->name ||
	    strcmp(drv->name, "a52-legacy-gdsc-regulator"))
		return 0;
	name = dev_name(dev);
	if (!name)
		return 0;
	if (strstr(name, "3d9106c"))
		return 'C';
	if (strstr(name, "3d9100c"))
		return 'G';
	return 0;
}

static void a52_r243_rp_enter3(struct device *dev,
		struct device_driver *drv)
{
	char tag = a52_r243_dd_tag(dev, drv);
	int q;

	if (!tag)
		return;
	for (q = 0; q < 3; q++)
		a52_ackfr_record("CXF243 R c=%c q=%d ls=%d", tag, q,
			dev->links.status);
}

static void a52_r243_rp_links3(struct device *dev,
		struct device_driver *drv)
{
	struct device_link *link;
	char tag = a52_r243_dd_tag(dev, drv);
	int n = 0;
	int q;

	if (!tag)
		return;
	list_for_each_entry(link, &dev->links.suppliers, c_node) {
		const char *s = link->supplier ? dev_name(link->supplier) : "-";
		int ds = link->supplier ? link->supplier->links.status : -1;
		int bound = link->supplier && link->supplier->driver;

		if (++n > 8)
			break;
		for (q = 0; q < 3; q++)
			a52_ackfr_record(
				"CXF243 L c=%c q=%d n=%d s=%.36s st=%u ds=%d b=%d",
				tag, q, n, s, link->status, ds, bound);
	}
}

static void a52_r243_rp_gate3(struct device *dev,
		struct device_driver *drv, int rc)
{
	char tag = a52_r243_dd_tag(dev, drv);
	int q;

	if (!tag)
		return;
	for (q = 0; q < 3; q++)
		a52_ackfr_record("CXF243 G c=%c q=%d rc=%d ls=%d", tag, q,
			rc, dev->links.status);
}

'''


def patch_dd(text: str, label: str) -> str:
    if MARKER in text:
        validate_dd(text, label)
        return text
    include = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if include not in text:
        anchor = "#include <linux/device.h>\n"
        text = one(text, anchor, anchor + include, f"{label}: recorder include")
    start, end = function_bounds(text, r"static\s+int\s+really_probe\s*\(", f"{label}: really_probe")
    text = text[:start] + DD_HELPER + text[start:]
    start, end = function_bounds(text, r"static\s+int\s+really_probe\s*\(", f"{label}: really_probe patched")
    fn = text[start:end]
    anchor = "ret = device_links_check_suppliers(dev);"
    if fn.count(anchor) != 1:
        raise RuntimeError(f"{label}: expected one supplier gate, found {fn.count(anchor)}")
    repl = ("a52_r243_rp_enter3(dev, drv);\n\t"
            "a52_r243_rp_links3(dev, drv);\n\t"
            + anchor + "\n\t"
            "a52_r243_rp_gate3(dev, drv, ret);")
    fn = fn.replace(anchor, repl, 1)
    text = text[:start] + fn + text[end:]
    validate_dd(text, label)
    return text


def validate_dd(text: str, label: str) -> None:
    for token in (MARKER, 'CXF243 R c=%c q=%d ls=%d',
                  'CXF243 L c=%c q=%d n=%d s=%.36s st=%u ds=%d b=%d',
                  'CXF243 G c=%c q=%d rc=%d ls=%d',
                  "a52_r243_rp_enter3(dev, drv);",
                  "a52_r243_rp_links3(dev, drv);",
                  "a52_r243_rp_gate3(dev, drv, ret);",
                  "device_links_check_suppliers(dev)"):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


GDSC_HELPER = r'''/* A52_PHASE243_CXGX_LIVE_SUPPLIER_V1 */
static void a52_r243_provider3(struct platform_device *pdev, const char *name)
{
	char tag = 0;
	int q;

	if (name && !strcmp(name, "gpu_cx_gdsc"))
		tag = 'C';
	else if (name && !strcmp(name, "gpu_gx_gdsc"))
		tag = 'G';
	if (!tag)
		return;
	for (q = 0; q < 3; q++)
		a52_ackfr_record("CXF243 P c=%c q=%d", tag, q);
}

'''


def patch_gdsc(text: str, label: str) -> str:
    if MARKER in text:
        validate_gdsc(text, label)
        return text
    start, end = function_bounds(text, r"static\s+int\s+a52_legacy_gdsc_probe\s*\(", f"{label}: gdsc probe")
    text = text[:start] + GDSC_HELPER + text[start:]
    start, end = function_bounds(text, r"static\s+int\s+a52_legacy_gdsc_probe\s*\(", f"{label}: gdsc probe patched")
    fn = text[start:end]
    anchor = 'if (of_property_read_string(pdev->dev.of_node, "regulator-name", &name))\n        return -EINVAL;\n'
    if anchor not in fn:
        anchor = 'if (of_property_read_string(pdev->dev.of_node, "regulator-name", &name))\n\t\treturn -EINVAL;\n'
    if anchor not in fn:
        raise RuntimeError(f"{label}: regulator-name anchor missing")
    fn = fn.replace(anchor, anchor + "\n\ta52_r243_provider3(pdev, name);\n", 1)
    text = text[:start] + fn + text[end:]
    validate_gdsc(text, label)
    return text


def validate_gdsc(text: str, label: str) -> None:
    for token in (MARKER, 'CXF243 P c=%c q=%d',
                  'a52_r243_provider3(pdev, name);', 'gpu_cx_gdsc', 'gpu_gx_gdsc'):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


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
            seen.add(key); out.append(root)
    return out


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        paths = [root / p for p in (RECORDER, PLATFORM, DD, GDSC)]
        if not all(p.is_file() for p in paths):
            continue
        if "A52_PHASE242_CX_STICKY_STATE_IDENTITY_V1" not in paths[0].read_text(encoding="utf-8"):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key); hits.append(root)
    if len(hits) != 1:
        raise RuntimeError("expected one generated Phase242 root, found " +
                           (", ".join(map(str, hits)) or "none"))
    return hits[0]


def self_test() -> None:
    rec = ("/*\n" + REC_MARK_OLD + " */\nA52_PHASE242_CX_STICKY_STATE_IDENTITY_V1\n"
           + FILTER_OLD + CRIT_OLD
           + "static void a52_r242_sticky_latch(const char *message) {}\n"
           + "static void a52_r242_snapshot(unsigned int tick) {}\n"
           + "void a52_ackfr_record(const char *fmt, ...)\n{\n"
           + LATCH_OLD + "}\n"
           + "static void a52_r179_heartbeat_fn(void) {\n" + SNAP_OLD + "\t\t0,0,0,0);\n}\n"
           + "static int __init a52_r179_early_heartbeat(void) { return 0; }\n")
    rec2 = patch_recorder(rec, "fixture/rec")
    assert patch_recorder(rec2, "fixture/rec2") == rec2

    plat = '#include <linux/device.h>\nstatic int platform_match(struct device *dev, struct device_driver *drv)\n{\n\tint ret = 1;\n\treturn ret;\n}\n'
    plat2 = patch_platform(plat, "fixture/platform")
    assert patch_platform(plat2, "fixture/platform2") == plat2

    dd = '#include <linux/device.h>\nstatic int really_probe(struct device *dev, struct device_driver *drv)\n{\n\tint ret;\n\tret = device_links_check_suppliers(dev);\n\treturn ret;\n}\n'
    dd2 = patch_dd(dd, "fixture/dd")
    assert patch_dd(dd2, "fixture/dd2") == dd2

    gd = ('static int a52_legacy_gdsc_probe(struct platform_device *pdev)\n{\n'
          '    const char *name;\n'
          '    if (of_property_read_string(pdev->dev.of_node, "regulator-name", &name))\n'
          '        return -EINVAL;\n'
          '    return 0;\n}\n')
    gd2 = patch_gdsc(gd, "fixture/gdsc")
    assert patch_gdsc(gd2, "fixture/gdsc2") == gd2
    print("Phase 243 live CX/GX own-supplier overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test(); return 0
    root = locate(sys.argv[1:])
    for rel, fn in ((RECORDER, patch_recorder), (PLATFORM, patch_platform),
                    (DD, patch_dd), (GDSC, patch_gdsc)):
        path = root / rel
        path.write_text(fn(path.read_text(encoding="utf-8"), str(path)), encoding="utf-8")
    print("Phase 243 live CX/GX match/supplier/provider diagnostics applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())