#!/usr/bin/env python3
"""Phase 238: broad focused GPU supplier-chain recorder over Phase 237.

This overlay keeps the Phase 210 R48/RS48/CRC32C transport unchanged. It adds
focused diagnostics for the Lagoon GPU dependency chain, especially
3d9106c.qcom,gdsc / gpu_cx_gdsc, without changing graphics-provider behavior.

Coverage:
  * generic platform probe attempts for GPU/GDSC/QFPROM/RPMh focus devices
  * driver-core supplier lists before device_links_check_suppliers()
  * exact return from really_probe() and platform_drv_probe()
  * broad gpu_cx_gdsc/gpu_gx_gdsc provider entry, DT/resource/property dump
  * suspicious provider call-site checkpoints and every provider return code
  * late (~145 s) replay summaries so early evidence survives ramoops retention
  * existing Phase 230 KGPPOST supplier replay is retained and admitted directly
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PHASE237_MARKER = "A52_PHASE237_OFPOP_PLATFORM_PROBE_RECORDER_V1"
PHASE238_MARKER = "A52_PHASE238_BROAD_GPU_SUPPLIER_RECORDER_V1"
PLATFORM_MARKER = "A52_PHASE238_PLATFORM_GPU_TRACE_V1"
DD_MARKER = "A52_PHASE238_DRIVER_CORE_GPU_TRACE_V1"
GDSC_MARKER = "A52_PHASE238_GDSC_PROVIDER_TRACE_V1"

PHASE237_BOOT = "BOOT rs=ready phase=237 focus=ofpop-probe roots=%u copies=3 crc=crc32c"
PHASE238_BOOT = "BOOT rs=ready phase=238 focus=gpu-supplier-broad roots=%u copies=3 crc=crc32c"

RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
PLATFORM_REL = Path("drivers/base/platform.c")
DD_REL = Path("drivers/base/dd.c")
GDSC_REL = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")

PHASE237_FILTER = '''\tif (strncmp(fmt, "OFPOP", 5) &&
\t    strncmp(fmt, "P3P", 3) &&
\t    strncmp(fmt, "DISPINIT", 8) &&
\t    strncmp(fmt, "RSCC", 4) &&
\t    strncmp(fmt, "DRMCOMP", 7) &&
\t    strncmp(fmt, "COMP ", 5) &&
\t    strncmp(fmt, "BOOT ctl=", 9) &&
\t    strncmp(fmt, "BOOT rs=ready", 13) &&
\t    strncmp(fmt, "BOOT phase=", 11))
\t\treturn;'''

PHASE238_FILTER = '''\tif (strncmp(fmt, "G238", 4) &&
\t    strncmp(fmt, "KGPPOST", 7) &&
\t    strncmp(fmt, "OFPOP", 5) &&
\t    strncmp(fmt, "P3P", 3) &&
\t    strncmp(fmt, "DISPINIT", 8) &&
\t    strncmp(fmt, "RSCC", 4) &&
\t    strncmp(fmt, "DRMCOMP", 7) &&
\t    strncmp(fmt, "COMP ", 5) &&
\t    strncmp(fmt, "BOOT ctl=", 9) &&
\t    strncmp(fmt, "BOOT rs=ready", 13) &&
\t    strncmp(fmt, "BOOT phase=", 11))
\t\treturn;'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def candidate_roots(arguments: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        p = Path(value)
        roots.extend((p, p.parent))
    roots.extend((Path("workspace/gki-phase199-src"), Path("gki/common")))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate_root(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for root in candidate_roots(arguments):
        files = [root / p for p in (RECORDER_REL, PLATFORM_REL, DD_REL, GDSC_REL)]
        if not all(p.is_file() for p in files):
            continue
        recorder = files[0].read_text(encoding="utf-8")
        if PHASE237_MARKER not in recorder and PHASE238_MARKER not in recorder:
            continue
        matches.append(root)
    uniq: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            uniq.append(root)
    if len(uniq) != 1:
        rendered = ", ".join(str(p) for p in uniq) or "none"
        raise RuntimeError(f"expected one generated Phase 237 root, found {len(uniq)}: {rendered}")
    return uniq[0]


def find_function(text: str, pattern: str, label: str) -> tuple[int, int, int]:
    m = re.search(pattern, text, re.M)
    if not m:
        raise RuntimeError(f"{label}: function signature not found")
    brace = text.find("{", m.start(), m.end() + 4)
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace missing")

    i = brace
    depth = 0
    state = "code"
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*":
                state = "block"
                i += 2
                continue
            if c == "/" and n == "/":
                state = "line"
                i += 2
                continue
            if c == '"':
                state = "string"
                i += 1
                continue
            if c == "'":
                state = "char"
                i += 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), brace, i + 1
        elif state == "block":
            if c == "*" and n == "/":
                state = "code"
                i += 2
                continue
        elif state == "line":
            if c == "\n":
                state = "code"
        elif state in ("string", "char"):
            quote = '"' if state == "string" else "'"
            if c == "\\":
                i += 2
                continue
            if c == quote:
                state = "code"
        i += 1
    raise RuntimeError(f"{label}: unterminated function")


def patch_function_returns_int(body: str, dev_expr: str, helper: str) -> str:
    pat = re.compile(r"\breturn\s+([^;\n]+);")
    return pat.sub(lambda m: f"return {helper}({dev_expr}, ({m.group(1).strip()}), __LINE__);", body)


def patch_recorder(text: str, label: str) -> str:
    if PHASE238_MARKER in text:
        validate_recorder(text, label)
        return text
    if PHASE237_MARKER not in text:
        raise RuntimeError(f"{label}: Phase 237 marker missing")
    text = replace_once(
        text,
        PHASE237_MARKER,
        PHASE237_MARKER + "\n\t * " + PHASE238_MARKER,
        f"{label}: marker",
    )
    text = replace_once(text, PHASE237_FILTER, PHASE238_FILTER, f"{label}: filter")
    text = replace_once(text, PHASE237_BOOT, PHASE238_BOOT, f"{label}: identity")
    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        PHASE238_MARKER,
        PHASE238_BOOT,
        'strncmp(fmt, "G238", 4)',
        'strncmp(fmt, "KGPPOST", 7)',
        'strncmp(fmt, "OFPOP", 5)',
        'strncmp(fmt, "P3P", 3)',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    if PHASE237_BOOT in text:
        raise RuntimeError(f"{label}: stale Phase 237 runtime identity remains")


PLATFORM_HELPERS = r'''
/* A52_PHASE238_PLATFORM_GPU_TRACE_V1 */
extern struct bus_type platform_bus_type;
#define A52_G238_PLATFORM_LIMIT 768
static atomic_t a52_g238_platform_events = ATOMIC_INIT(0);
static atomic_t a52_g238_cx_platform_attempts = ATOMIC_INIT(0);
static int a52_g238_cx_last_rc = -9999;
static int a52_g238_cx_last_stage;
static const char *a52_g238_cx_last_driver = "-";

static bool a52_g238_gpu_name(const char *name)
{
\tif (!name)
\t\treturn false;
\treturn strstr(name, "3d00000") || strstr(name, "3d90000") ||
\t\tstrstr(name, "3d9100c") || strstr(name, "3d9106c") ||
\t\tstrstr(name, "780000") || strstr(name, "c350000") ||
\t\tstrstr(name, "kgsl") || strstr(name, "adreno") ||
\t\tstrstr(name, "gpu") || strstr(name, "gdsc") ||
\t\tstrstr(name, "qfprom");
}

static bool a52_g238_cx_name(const char *name)
{
\treturn name && strstr(name, "3d9106c");
}

static bool a52_g238_platform_can_log(struct device *dev)
{
\tint n;

\tif (!dev || !a52_g238_gpu_name(dev_name(dev)))
\t\treturn false;
\tn = atomic_inc_return(&a52_g238_platform_events);
\treturn n <= A52_G238_PLATFORM_LIMIT || a52_g238_cx_name(dev_name(dev));
}

static void a52_g238_platform_enter(struct device *dev)
{
\tconst char *drv;

\tif (!a52_g238_platform_can_log(dev))
\t\treturn;
\tdrv = dev->driver ? dev->driver->name : "-";
\tif (a52_g238_cx_name(dev_name(dev))) {
\t\tatomic_inc(&a52_g238_cx_platform_attempts);
\t\tWRITE_ONCE(a52_g238_cx_last_driver, drv);
\t\tWRITE_ONCE(a52_g238_cx_last_stage, 1);
\t}
\ta52_ackfr_record("G238 P in dev=%s drv=%s node=%s",
\t\t\t dev_name(dev), drv,
\t\t\t dev->of_node ? dev->of_node->full_name : "-");
}

static void a52_g238_platform_stage(struct device *dev, int stage)
{
\tif (!dev || !a52_g238_gpu_name(dev_name(dev)))
\t\treturn;
\tif (a52_g238_cx_name(dev_name(dev)))
\t\tWRITE_ONCE(a52_g238_cx_last_stage, stage);
\tif (atomic_read(&a52_g238_platform_events) <= A52_G238_PLATFORM_LIMIT ||
\t    a52_g238_cx_name(dev_name(dev)))
\t\ta52_ackfr_record("G238 P st dev=%s s=%d drv=%s",
\t\t\t\t dev_name(dev), stage,
\t\t\t\t dev->driver ? dev->driver->name : "-");
}

static int a52_g238_platform_return(struct device *dev, int rc, int line)
{
\tif (dev && a52_g238_gpu_name(dev_name(dev))) {
\t\tif (a52_g238_cx_name(dev_name(dev)))
\t\t\tWRITE_ONCE(a52_g238_cx_last_rc, rc);
\t\tif (atomic_read(&a52_g238_platform_events) <= A52_G238_PLATFORM_LIMIT ||
\t\t    a52_g238_cx_name(dev_name(dev)))
\t\t\ta52_ackfr_record("G238 P out dev=%s rc=%d l=%d drv=%s",
\t\t\t\t\t dev_name(dev), rc, line,
\t\t\t\t\t dev->driver ? dev->driver->name : "-");
\t}
\treturn rc;
}

static void a52_g238_platform_replay_one(const char *name)
{
\tstruct device *dev;
\tstruct device_link *link;
\tint n = 0;

\tdev = bus_find_device_by_name(&platform_bus_type, NULL, name);
\tif (!dev) {
\t\ta52_ackfr_record("G238 RP dev=%s found=0", name);
\t\treturn;
\t}
\ta52_ackfr_record("G238 RP dev=%s found=1 drv=%s",
\t\t\t name, dev->driver ? dev->driver->name : "-");
\tlist_for_each_entry(link, &dev->links.suppliers, c_node) {
\t\tif (++n > 24) {
\t\t\ta52_ackfr_record("G238 RP dev=%s sup-limit=%d", name, n);
\t\t\tbreak;
\t\t}
\t\ta52_ackfr_record("G238 RP sup c=%s s=%s drv=%s st=%u fl=%u",
\t\t\t\t name, dev_name(link->supplier),
\t\t\t\t link->supplier->driver ? link->supplier->driver->name : "-",
\t\t\t\t link->status, link->flags);
\t}
\tput_device(dev);
}

static void a52_g238_platform_replay_workfn(struct work_struct *work)
{
\ta52_ackfr_record("G238 RP plat cx-att=%d stage=%d rc=%d drv=%s",
\t\t\t atomic_read(&a52_g238_cx_platform_attempts),
\t\t\t READ_ONCE(a52_g238_cx_last_stage),
\t\t\t READ_ONCE(a52_g238_cx_last_rc),
\t\t\t READ_ONCE(a52_g238_cx_last_driver));
\ta52_g238_platform_replay_one("3d9106c.qcom,gdsc");
\ta52_g238_platform_replay_one("3d9100c.qcom,gdsc");
\ta52_g238_platform_replay_one("3d90000.qcom,gpucc");
\ta52_g238_platform_replay_one("3d00000.qcom,kgsl-3d0");
\ta52_g238_platform_replay_one("780000.qfprom");
}

static DECLARE_DELAYED_WORK(a52_g238_platform_replay_work,
\t\t\t    a52_g238_platform_replay_workfn);

static int __init a52_g238_platform_replay_init(void)
{
\tschedule_delayed_work(&a52_g238_platform_replay_work,
\t\t\t      msecs_to_jiffies(145000));
\treturn 0;
}
late_initcall(a52_g238_platform_replay_init);
'''


def ensure_include(text: str, anchor: str, include: str, label: str) -> str:
    if include in text:
        return text
    return replace_once(text, anchor, anchor + include, label)


def patch_platform(text: str, label: str) -> str:
    if PLATFORM_MARKER in text:
        validate_platform(text, label)
        return text

    text = ensure_include(
        text,
        "#include <linux/a52_ack_secure_flight_recorder.h>\n",
        "#include <linux/workqueue.h>\n#include <linux/jiffies.h>\n#include <linux/string.h>\n",
        f"{label}: phase238 includes",
    )

    anchor = "#define A52_PHASE237_P3P_LIMIT 192\n"
    text = replace_once(text, anchor, anchor + PLATFORM_HELPERS + "\n", f"{label}: helpers")

    start, brace, end = find_function(
        text, r"static\s+int\s+platform_drv_probe\s*\(\s*struct\s+device\s*\*\s*_dev\s*\)\s*\{",
        f"{label}: platform_drv_probe",
    )
    fn = text[start:end]
    if "a52_g238_platform_enter(_dev)" not in fn:
        first = "ret = of_clk_set_defaults(_dev->of_node, false);"
        if first not in fn:
            raise RuntimeError(f"{label}: of_clk_set_defaults anchor missing")
        fn = fn.replace(
            first,
            "a52_g238_platform_enter(_dev);\n"
            "\ta52_g238_platform_stage(_dev, 10);\n\t" + first,
            1,
        )

        pm = "ret = dev_pm_domain_attach(_dev, true);"
        if pm in fn:
            fn = fn.replace(pm, "a52_g238_platform_stage(_dev, 20);\n\t" + pm, 1)

        p3p_call = 'a52_ackfr_record("P3P call n=%d drv=%s"'
        idx = fn.find(p3p_call)
        if idx >= 0:
            line_start = fn.rfind("\n", 0, idx) + 1
            fn = fn[:line_start] + "\ta52_g238_platform_stage(_dev, 30);\n" + fn[line_start:]
        else:
            m = re.search(r"(?m)^([ \t]*)([^\n;]*->probe\s*\([^;]+;)", fn)
            if m:
                ins = m.group(1) + "a52_g238_platform_stage(_dev, 30);\n"
                fn = fn[:m.start()] + ins + fn[m.start():]

        fn = patch_function_returns_int(fn, "_dev", "a52_g238_platform_return")

    text = text[:start] + fn + text[end:]
    validate_platform(text, label)
    return text


def validate_platform(text: str, label: str) -> None:
    for token in (
        PLATFORM_MARKER,
        "A52_G238_PLATFORM_LIMIT 768",
        'a52_ackfr_record("G238 P in dev=%s drv=%s node=%s"',
        'a52_ackfr_record("G238 P out dev=%s rc=%d l=%d drv=%s"',
        'a52_ackfr_record("G238 RP plat cx-att=%d stage=%d rc=%d drv=%s"',
        'a52_g238_platform_replay_one("3d9106c.qcom,gdsc")',
        "msecs_to_jiffies(145000)",
        "a52_g238_platform_enter(_dev)",
        "a52_g238_platform_stage(_dev, 10)",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


DD_HELPERS = r'''
/* A52_PHASE238_DRIVER_CORE_GPU_TRACE_V1 */
#define A52_G238_DD_LIMIT 768
static atomic_t a52_g238_dd_events = ATOMIC_INIT(0);

static bool a52_g238_dd_focus(struct device *dev)
{
\tconst char *n;

\tif (!dev)
\t\treturn false;
\tn = dev_name(dev);
\treturn n && (strstr(n, "3d00000") || strstr(n, "3d90000") ||
\t\t     strstr(n, "3d9100c") || strstr(n, "3d9106c") ||
\t\t     strstr(n, "780000") || strstr(n, "c350000") ||
\t\t     strstr(n, "kgsl") || strstr(n, "gpu") ||
\t\t     strstr(n, "gdsc") || strstr(n, "qfprom"));
}

static bool a52_g238_dd_can_log(struct device *dev)
{
\tint n;

\tif (!a52_g238_dd_focus(dev))
\t\treturn false;
\tn = atomic_inc_return(&a52_g238_dd_events);
\treturn n <= A52_G238_DD_LIMIT ||
\t       (dev_name(dev) && strstr(dev_name(dev), "3d9106c"));
}

static void a52_g238_dd_dump_suppliers(struct device *dev, struct device_driver *drv)
{
\tstruct device_link *link;
\tint n = 0;

\tif (!a52_g238_dd_can_log(dev))
\t\treturn;
\ta52_ackfr_record("G238 D in dev=%s drv=%s cur=%s",
\t\t\t dev_name(dev), drv ? drv->name : "-",
\t\t\t dev->driver ? dev->driver->name : "-");
\tlist_for_each_entry(link, &dev->links.suppliers, c_node) {
\t\tif (++n > 32) {
\t\t\ta52_ackfr_record("G238 D slimit dev=%s n=%d", dev_name(dev), n);
\t\t\tbreak;
\t\t}
\t\ta52_ackfr_record("G238 D sup c=%s s=%s drv=%s st=%u fl=%u",
\t\t\t\t dev_name(dev), dev_name(link->supplier),
\t\t\t\t link->supplier->driver ? link->supplier->driver->name : "-",
\t\t\t\t link->status, link->flags);
\t}
}

static void a52_g238_dd_supplier_result(struct device *dev, int rc)
{
\tif (a52_g238_dd_focus(dev))
\t\ta52_ackfr_record("G238 D sup-out dev=%s rc=%d", dev_name(dev), rc);
}

static int a52_g238_dd_return(struct device *dev, int rc, int line)
{
\tif (a52_g238_dd_focus(dev))
\t\ta52_ackfr_record("G238 D out dev=%s rc=%d l=%d cur=%s",
\t\t\t\t dev_name(dev), rc, line,
\t\t\t\t dev->driver ? dev->driver->name : "-");
\treturn rc;
}
'''


def patch_dd(text: str, label: str) -> str:
    if DD_MARKER in text:
        validate_dd(text, label)
        return text

    include_anchor = "#include <linux/device.h>\n"
    additions = ""
    for inc in (
        "#include <linux/a52_ack_secure_flight_recorder.h>\n",
        "#include <linux/atomic.h>\n",
        "#include <linux/string.h>\n",
    ):
        if inc not in text:
            additions += inc
    if additions:
        text = replace_once(
            text, include_anchor, include_anchor + additions, f"{label}: includes"
        )

    start, brace, end = find_function(
        text,
        r"static\s+int\s+really_probe\s*\(\s*struct\s+device\s*\*\s*dev\s*,\s*struct\s+device_driver\s*\*\s*drv\s*\)\s*\{",
        f"{label}: really_probe",
    )
    text = text[:start] + DD_HELPERS + "\n" + text[start:]
    start, brace, end = find_function(
        text,
        r"static\s+int\s+really_probe\s*\(\s*struct\s+device\s*\*\s*dev\s*,\s*struct\s+device_driver\s*\*\s*drv\s*\)\s*\{",
        f"{label}: really_probe after helper",
    )
    fn = text[start:end]

    supplier = "ret = device_links_check_suppliers(dev);"
    if supplier not in fn:
        raise RuntimeError(f"{label}: device_links_check_suppliers anchor missing")
    fn = fn.replace(
        supplier,
        "a52_g238_dd_dump_suppliers(dev, drv);\n"
        "\t" + supplier + "\n"
        "\ta52_g238_dd_supplier_result(dev, ret);",
        1,
    )
    fn = patch_function_returns_int(fn, "dev", "a52_g238_dd_return")
    text = text[:start] + fn + text[end:]
    validate_dd(text, label)
    return text


def validate_dd(text: str, label: str) -> None:
    for token in (
        DD_MARKER,
        "A52_G238_DD_LIMIT 768",
        "a52_g238_dd_dump_suppliers(dev, drv)",
        "device_links_check_suppliers(dev)",
        'a52_ackfr_record("G238 D sup c=%s s=%s drv=%s st=%u fl=%u"',
        'a52_ackfr_record("G238 D sup-out dev=%s rc=%d"',
        'a52_ackfr_record("G238 D out dev=%s rc=%d l=%d cur=%s"',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


GDSC_HELPERS_TEMPLATE = r'''
/* A52_PHASE238_GDSC_PROVIDER_TRACE_V1 */
#define A52_G238_GDSC_PROP_LIMIT 40
static atomic_t a52_g238_gd_probe_count = ATOMIC_INIT(0);
static int a52_g238_gd_last_rc = -9999;
static int a52_g238_gd_last_stage;
static unsigned long a52_g238_gd_res_start;
static unsigned long a52_g238_gd_res_end;
static u32 a52_g238_gd_wait;
static u32 a52_g238_gd_timeout;
static int a52_g238_gd_wait_rc = -9999;
static int a52_g238_gd_timeout_rc = -9999;
static bool a52_g238_gd_nostatus;
static bool a52_g238_gd_parent_prop;

static bool a52_g238_gd_is_cx(struct platform_device *pdev, const char **rname)
{
\tconst char *name = NULL;

\tif (pdev->dev.of_node)
\t\tof_property_read_string(pdev->dev.of_node, "regulator-name", &name);
\tif (rname)
\t\t*rname = name;
\treturn (name && !strcmp(name, "gpu_cx_gdsc")) ||
\t       strstr(dev_name(&pdev->dev), "3d9106c");
}

static bool a52_g238_gd_focus(struct platform_device *pdev, const char **rname)
{
\tconst char *name = NULL;

\tif (pdev->dev.of_node)
\t\tof_property_read_string(pdev->dev.of_node, "regulator-name", &name);
\tif (rname)
\t\t*rname = name;
\treturn (name && (!strcmp(name, "gpu_cx_gdsc") ||
\t\t\t !strcmp(name, "gpu_gx_gdsc"))) ||
\t       strstr(dev_name(&pdev->dev), "3d9106c") ||
\t       strstr(dev_name(&pdev->dev), "3d9100c");
}

static void a52_g238_gd_phandle(struct platform_device *pdev, const char *prop)
{
\tstruct device_node *np;
\tstruct platform_device *spdev;

\tif (!pdev->dev.of_node)
\t\treturn;
\tnp = of_parse_phandle(pdev->dev.of_node, prop, 0);
\tif (!np) {
\t\ta52_ackfr_record("G238 GD ph dev=%s p=%s node=-",
\t\t\t\t dev_name(&pdev->dev), prop);
\t\treturn;
\t}
\tspdev = of_find_device_by_node(np);
\ta52_ackfr_record("G238 GD ph dev=%s p=%s node=%s pdrv=%s",
\t\t\t dev_name(&pdev->dev), prop, np->full_name,
\t\t\t spdev && spdev->dev.driver ? spdev->dev.driver->name : "-");
\tif (spdev)
\t\tput_device(&spdev->dev);
\tof_node_put(np);
}

static int a52_g238_gd_enter(struct platform_device *pdev)
{
\tconst char *rname = NULL;
\tconst char *compat = NULL;
\tconst struct property *prop;
\tstruct resource *res;
\tu32 val = 0;
\tint n = 0;
\tbool focus;
\tbool cx;

\tfocus = a52_g238_gd_focus(pdev, &rname);
\tif (!focus)
\t\treturn 0;
\tcx = a52_g238_gd_is_cx(pdev, NULL);
\tif (cx)
\t\tatomic_inc(&a52_g238_gd_probe_count);

\tif (pdev->dev.of_node)
\t\tcompat = of_get_property(pdev->dev.of_node, "compatible", NULL);
\ta52_ackfr_record("G238 GD in dev=%s drv=%s rn=%s comp=%s cx=%u",
\t\t\t dev_name(&pdev->dev),
\t\t\t pdev->dev.driver ? pdev->dev.driver->name : "-",
\t\t\t rname ? rname : "-", compat ? compat : "-", cx);

\tres = platform_get_resource(pdev, IORESOURCE_MEM, 0);
\tif (res)
\t\ta52_ackfr_record("G238 GD res dev=%s ok=1 s=%llx e=%llx",
\t\t\t\t dev_name(&pdev->dev),
\t\t\t\t (unsigned long long)res->start,
\t\t\t\t (unsigned long long)res->end);
\telse
\t\ta52_ackfr_record("G238 GD res dev=%s ok=0", dev_name(&pdev->dev));
\tif (cx && res) {
\t\tWRITE_ONCE(a52_g238_gd_res_start, (unsigned long)res->start);
\t\tWRITE_ONCE(a52_g238_gd_res_end, (unsigned long)res->end);
\t}

\tif (pdev->dev.of_node) {
\t\tint rc_wait = of_property_read_u32(pdev->dev.of_node,
\t\t\t\t\t\t   "qcom,clk-dis-wait-val", &val);
\t\ta52_ackfr_record("G238 GD prop dev=%s wait-rc=%d wait=%u",
\t\t\t\t dev_name(&pdev->dev), rc_wait, val);
\t\tif (cx) {
\t\t\tWRITE_ONCE(a52_g238_gd_wait_rc, rc_wait);
\t\t\tWRITE_ONCE(a52_g238_gd_wait, val);
\t\t}

\t\tval = 0;
\t\t{
\t\t\tint rc_timeout = of_property_read_u32(pdev->dev.of_node,
\t\t\t\t\t\t\t      "qcom,gds-timeout", &val);
\t\t\ta52_ackfr_record("G238 GD prop dev=%s timeout-rc=%d timeout=%u",
\t\t\t\t\t dev_name(&pdev->dev), rc_timeout, val);
\t\t\tif (cx) {
\t\t\t\tWRITE_ONCE(a52_g238_gd_timeout_rc, rc_timeout);
\t\t\t\tWRITE_ONCE(a52_g238_gd_timeout, val);
\t\t\t}
\t\t}
\t\ta52_ackfr_record("G238 GD prop dev=%s nostatus=%u parent=%u hwctl=%u hwctrl=%u",
\t\t\t\t dev_name(&pdev->dev),
\t\t\t\t of_property_read_bool(pdev->dev.of_node,
\t\t\t\t\t"qcom,no-status-check-on-disable"),
\t\t\t\t of_find_property(pdev->dev.of_node,
\t\t\t\t\t"vdd_parent-supply", NULL) != NULL,
\t\t\t\t of_find_property(pdev->dev.of_node,
\t\t\t\t\t"hw-ctl-addr", NULL) != NULL,
\t\t\t\t of_find_property(pdev->dev.of_node,
\t\t\t\t\t"hw-ctrl-addr", NULL) != NULL);
\t\tif (cx) {
\t\t\tWRITE_ONCE(a52_g238_gd_nostatus,
\t\t\t\tof_property_read_bool(pdev->dev.of_node,
\t\t\t\t\t"qcom,no-status-check-on-disable"));
\t\t\tWRITE_ONCE(a52_g238_gd_parent_prop,
\t\t\t\tof_find_property(pdev->dev.of_node,
\t\t\t\t\t"vdd_parent-supply", NULL) != NULL);
\t\t}

\t\tfor_each_property_of_node(pdev->dev.of_node, prop) {
\t\t\tif (++n > A52_G238_GDSC_PROP_LIMIT) {
\t\t\t\ta52_ackfr_record("G238 GD plist dev=%s limit=%d",
\t\t\t\t\t\t dev_name(&pdev->dev), n);
\t\t\t\tbreak;
\t\t\t}
\t\t\ta52_ackfr_record("G238 GD p dev=%s n=%s",
\t\t\t\t\t dev_name(&pdev->dev), prop->name);
\t\t}
\t}

\ta52_g238_gd_phandle(pdev, "vdd_parent-supply");
\ta52_g238_gd_phandle(pdev, "hw-ctl-addr");
\ta52_g238_gd_phandle(pdev, "hw-ctrl-addr");
\treturn 0;
}

static void a52_g238_gd_stage(struct platform_device *pdev, int stage,
\t\t\t      const char *op, int line)
{
\tif (!a52_g238_gd_focus(pdev, NULL))
\t\treturn;
\tif (a52_g238_gd_is_cx(pdev, NULL))
\t\tWRITE_ONCE(a52_g238_gd_last_stage, stage);
\ta52_ackfr_record("G238 GD st dev=%s s=%d op=%s l=%d",
\t\t\t dev_name(&pdev->dev), stage, op, line);
}

static int a52_g238_gd_return(struct platform_device *pdev, int rc, int line)
{
\tif (a52_g238_gd_focus(pdev, NULL)) {
\t\tif (a52_g238_gd_is_cx(pdev, NULL))
\t\t\tWRITE_ONCE(a52_g238_gd_last_rc, rc);
\t\ta52_ackfr_record("G238 GD out dev=%s rc=%d l=%d drv=%s",
\t\t\t\t dev_name(&pdev->dev), rc, line,
\t\t\t\t pdev->dev.driver ? pdev->dev.driver->name : "-");
\t}
\treturn rc;
}

static void a52_g238_gd_replay_workfn(struct work_struct *work)
{
\ta52_ackfr_record("G238 RP gd cx-probes=%d stage=%d rc=%d",
\t\t\t atomic_read(&a52_g238_gd_probe_count),
\t\t\t READ_ONCE(a52_g238_gd_last_stage),
\t\t\t READ_ONCE(a52_g238_gd_last_rc));
\ta52_ackfr_record("G238 RP gd res=%lx-%lx waitrc=%d wait=%u torc=%d to=%u ns=%u par=%u",
\t\t\t READ_ONCE(a52_g238_gd_res_start),
\t\t\t READ_ONCE(a52_g238_gd_res_end),
\t\t\t READ_ONCE(a52_g238_gd_wait_rc),
\t\t\t READ_ONCE(a52_g238_gd_wait),
\t\t\t READ_ONCE(a52_g238_gd_timeout_rc),
\t\t\t READ_ONCE(a52_g238_gd_timeout),
\t\t\t READ_ONCE(a52_g238_gd_nostatus),
\t\t\t READ_ONCE(a52_g238_gd_parent_prop));
}

static DECLARE_DELAYED_WORK(a52_g238_gd_replay_work,
\t\t\t    a52_g238_gd_replay_workfn);

static int __init a52_g238_gd_replay_init(void)
{
\tschedule_delayed_work(&a52_g238_gd_replay_work,
\t\t\t      msecs_to_jiffies(145000));
\treturn 0;
}
late_initcall(a52_g238_gd_replay_init);
'''


SUSPICIOUS = (
    ("devm_regulator_get", 110, "devm-reg-get"),
    ("regulator_get", 111, "reg-get"),
    ("platform_get_resource", 120, "resource"),
    ("devm_ioremap_resource", 121, "ioremap"),
    ("devm_regulator_register", 130, "devm-reg-register"),
    ("regulator_register", 131, "reg-register"),
    ("syscon_node_to_regmap", 140, "syscon"),
    ("devm_regmap_init_mmio", 141, "regmap-mmio"),
    ("of_parse_phandle", 142, "phandle"),
    ("of_property_read", 150, "of-prop"),
    ("regulator_enable", 160, "reg-enable"),
    ("regulator_set_voltage", 161, "reg-voltage"),
    ("regulator_set_load", 162, "reg-load"),
    ("clk_prepare_enable", 170, "clk-enable"),
    ("readl", 180, "readl"),
    ("writel", 181, "writel"),
)


def add_gdsc_call_stages(fn: str, pdev_name: str) -> str:
    lines = fn.splitlines(True)
    out: list[str] = []
    type_prefixes = (
        "struct ", "const ", "int ", "bool ", "u32 ", "u64 ", "unsigned ",
        "long ", "char ", "enum ", "void ", "static ",
    )
    for line in lines:
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        stage = None
        for token, sid, op in SUSPICIOUS:
            if token + "(" in stripped:
                stage = (sid, op)
                break
        if stage and not stripped.startswith(type_prefixes):
            sid, op = stage
            out.append(
                f'{indent}a52_g238_gd_stage({pdev_name}, {sid}, "{op}", __LINE__);\n'
            )
        if ("gpu_cx_gdsc" in stripped or "GPU_CX_PROFILE" in stripped or
                "3d9106c" in stripped) and not stripped.startswith(type_prefixes):
            out.append(
                f'{indent}a52_g238_gd_stage({pdev_name}, 200, "cx-profile", __LINE__);\n'
            )
        out.append(line)
    return "".join(out)


def patch_gdsc(text: str, label: str) -> str:
    if GDSC_MARKER in text:
        validate_gdsc(text, label)
        return text

    include_anchor = "#include <linux/platform_device.h>\n"
    if include_anchor not in text:
        m = re.search(r"(?m)^#include <[^>]+>\n", text)
        if not m:
            raise RuntimeError(f"{label}: include anchor missing")
        include_anchor = m.group(0)

    additions = ""
    for inc in (
        "#include <linux/a52_ack_secure_flight_recorder.h>\n",
        "#include <linux/atomic.h>\n",
        "#include <linux/jiffies.h>\n",
        "#include <linux/of_platform.h>\n",
        "#include <linux/workqueue.h>\n",
        "#include <linux/string.h>\n",
    ):
        if inc not in text:
            additions += inc
    if additions:
        text = replace_once(text, include_anchor, include_anchor + additions, f"{label}: includes")

    probe_re = (
        r"static\s+int\s+([A-Za-z0-9_]*gdsc[A-Za-z0-9_]*probe)"
        r"\s*\(\s*struct\s+platform_device\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\{"
    )
    m = re.search(probe_re, text, re.M)
    if not m:
        probe_re = (
            r"static\s+int\s+([A-Za-z0-9_]*probe)"
            r"\s*\(\s*struct\s+platform_device\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*\{"
        )
        m = re.search(probe_re, text, re.M)
    if not m:
        raise RuntimeError(f"{label}: platform GDSC probe not found")
    pdev = m.group(2)

    start, brace, end = find_function(text, probe_re, f"{label}: gdsc probe")
    text = text[:start] + GDSC_HELPERS_TEMPLATE + "\n" + text[start:]

    start, brace, end = find_function(text, probe_re, f"{label}: gdsc probe after helpers")
    fn = text[start:end]
    if "a52_g238_probe_entry" not in fn:
        insert = (
            f"\n\tint a52_g238_probe_entry __maybe_unused = "
            f"a52_g238_gd_enter({pdev});"
        )
        rel_brace = fn.find("{")
        fn = fn[: rel_brace + 1] + insert + fn[rel_brace + 1:]
        fn = add_gdsc_call_stages(fn, pdev)
        fn = patch_function_returns_int(fn, pdev, "a52_g238_gd_return")

    text = text[:start] + fn + text[end:]
    validate_gdsc(text, label)
    return text


def validate_gdsc(text: str, label: str) -> None:
    for token in (
        GDSC_MARKER,
        "gpu_cx_gdsc",
        "gpu_gx_gdsc",
        '"qcom,clk-dis-wait-val"',
        '"qcom,gds-timeout"',
        '"qcom,no-status-check-on-disable"',
        '"vdd_parent-supply"',
        '"hw-ctl-addr"',
        '"hw-ctrl-addr"',
        'a52_ackfr_record("G238 GD in dev=%s drv=%s rn=%s comp=%s cx=%u"',
        'a52_ackfr_record("G238 GD out dev=%s rc=%d l=%d drv=%s"',
        'a52_ackfr_record("G238 RP gd cx-probes=%d stage=%d rc=%d"',
        "msecs_to_jiffies(145000)",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def patch_file(path: Path, patcher, label: str) -> None:
    before = path.read_text(encoding="utf-8")
    after = patcher(before, label)
    if after != before:
        path.write_text(after, encoding="utf-8")
        print(f"Phase 238 patched {path}")
    else:
        print(f"Phase 238 already present in {path}")


def main() -> int:
    root = locate_root(sys.argv[1:])
    patch_file(root / RECORDER_REL, patch_recorder, str(RECORDER_REL))
    patch_file(root / PLATFORM_REL, patch_platform, str(PLATFORM_REL))
    patch_file(root / DD_REL, patch_dd, str(DD_REL))
    patch_file(root / GDSC_REL, patch_gdsc, str(GDSC_REL))
    print("Phase 238 broad GPU supplier recorder overlay applied")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 238 overlay failed: {exc}", file=sys.stderr)
        raise
