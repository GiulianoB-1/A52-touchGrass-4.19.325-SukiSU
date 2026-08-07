#!/usr/bin/env python3
'''Phase 237: trace OF default population and synchronous platform probes.

Runs after Phase 236 inside the cumulative Phase 230 hook. The Phase 210
R48/RS48/CRC32C transport remains unchanged. Phase 237 adds an OF-population
activity gate and bounded platform probe stage records so a non-returning
probe between BOOT phase=arch and BOOT phase=subsys can be identified exactly.
'''
from __future__ import annotations

import sys
from pathlib import Path

PHASE236_MARKER = "A52_PHASE236_DISPLAY_INIT_RECORDER_V1"
PHASE237_MARKER = "A52_PHASE237_OFPOP_PLATFORM_PROBE_RECORDER_V1"
OFPOP_TRACE_MARKER = "A52_PHASE237_OFPOP_TRACE_V1"
P3P_TRACE_MARKER = "A52_PHASE237_PLATFORM_PROBE_TRACE_V1"

PHASE236_BOOT = (
    "BOOT rs=ready phase=236 focus=display-init roots=%u copies=3 crc=crc32c"
)
PHASE237_BOOT = (
    "BOOT rs=ready phase=237 focus=ofpop-probe roots=%u copies=3 crc=crc32c"
)

RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
OF_PLATFORM_REL = Path("drivers/of/platform.c")
BASE_PLATFORM_REL = Path("drivers/base/platform.c")

PHASE236_FILTER = '''\tif (strncmp(fmt, "DISPINIT", 8) &&
\t    strncmp(fmt, "RSCC", 4) &&
\t    strncmp(fmt, "DRMCOMP", 7) &&
\t    strncmp(fmt, "COMP ", 5) &&
\t    strncmp(fmt, "BOOT ctl=", 9) &&
\t    strncmp(fmt, "BOOT rs=ready", 13) &&
\t    strncmp(fmt, "BOOT phase=", 11))
\t\treturn;'''

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


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_once_in(
    text: str, start: str, end: str, old: str, new: str, label: str
) -> str:
    begin = text.find(start)
    if begin < 0:
        raise RuntimeError(f"{label}: function start missing")
    finish = text.find(end, begin)
    if finish < 0:
        raise RuntimeError(f"{label}: function end missing")
    body = text[begin:finish]
    count = body.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one scoped anchor, found {count}")
    body = body.replace(old, new, 1)
    return text[:begin] + body + text[finish:]


def candidate_roots(arguments: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        path = Path(value)
        roots.extend((path, path.parent))
    roots.extend((Path("workspace/gki-phase199-src"), Path("gki/common")))
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def locate_root(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for root in candidate_roots(arguments):
        recorder = root / RECORDER_REL
        of_platform = root / OF_PLATFORM_REL
        base_platform = root / BASE_PLATFORM_REL
        if not recorder.is_file() or not of_platform.is_file() or not base_platform.is_file():
            continue
        recorder_text = recorder.read_text(encoding="utf-8")
        if PHASE236_MARKER not in recorder_text and PHASE237_MARKER not in recorder_text:
            continue
        matches.append(root)

    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(path) for path in unique) or "none"
        raise RuntimeError(
            f"expected one generated Phase 236 kernel root, found {len(unique)}: {rendered}"
        )
    return unique[0]


def patch_recorder(text: str, label: str) -> str:
    if PHASE237_MARKER in text:
        validate_recorder(text, label)
        return text
    if PHASE236_MARKER not in text:
        raise RuntimeError(f"{label}: Phase 236 recorder marker missing")
    text = replace_once(
        text,
        PHASE236_MARKER,
        PHASE236_MARKER + "\n\t * " + PHASE237_MARKER,
        f"{label}: Phase 237 marker",
    )
    text = replace_once(
        text, PHASE236_FILTER, PHASE237_FILTER, f"{label}: Phase 237 event filter"
    )
    text = replace_once(
        text, PHASE236_BOOT, PHASE237_BOOT, f"{label}: Phase 237 boot identity"
    )
    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        PHASE236_MARKER,
        PHASE237_MARKER,
        PHASE237_FILTER,
        PHASE237_BOOT,
        'strncmp(fmt, "OFPOP", 5)',
        'strncmp(fmt, "P3P", 3)',
        'strncmp(fmt, "BOOT phase=", 11)',
        'strncmp(fmt, "DISPINIT", 8)',
        'strncmp(fmt, "RSCC", 4)',
        'strncmp(fmt, "DRMCOMP", 7)',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing Phase 237 recorder token: {token}")
    if PHASE236_BOOT in text:
        raise RuntimeError(f"{label}: stale Phase 236 runtime identity remains")


def patch_of_platform(text: str, label: str) -> str:
    if OFPOP_TRACE_MARKER in text:
        validate_of_platform(text, label)
        return text

    include_anchor = "#include <linux/platform_device.h>\n"
    text = replace_once(
        text,
        include_anchor,
        include_anchor + "#include <linux/a52_ack_secure_flight_recorder.h>\n",
        f"{label}: recorder include",
    )

    export_anchor = "EXPORT_SYMBOL_GPL(of_platform_default_populate);\n"
    text = replace_once(
        text,
        export_anchor,
        export_anchor
        + f"\n/* {OFPOP_TRACE_MARKER} */\n"
        + "bool a52_phase237_ofpop_active;\n",
        f"{label}: OFPOP gate",
    )

    fn_start = "static int __init of_platform_default_populate_init(void)\n{\n"
    fn_end = "arch_initcall_sync(of_platform_default_populate_init);"

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        "\tstruct device_node *node;\n\n\tdevice_links_supplier_sync_state_pause();\n",
        "\tstruct device_node *node;\n\n"
        "\tWRITE_ONCE(a52_phase237_ofpop_active, true);\n"
        '\ta52_ackfr_record("OFPOP enter");\n'
        "\tdevice_links_supplier_sync_state_pause();\n"
        '\ta52_ackfr_record("OFPOP links-paused");\n',
        f"{label}: OFPOP entry",
    )

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        "\tif (!of_have_populated_dt())\n\t\treturn -ENODEV;\n",
        "\tif (!of_have_populated_dt()) {\n"
        '\t\ta52_ackfr_record("OFPOP no-dt rc=%d", -ENODEV);\n'
        "\t\tWRITE_ONCE(a52_phase237_ofpop_active, false);\n"
        "\t\treturn -ENODEV;\n"
        "\t}\n",
        f"{label}: no-DT exit",
    )

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        "\tfor_each_matching_node(node, reserved_mem_matches)\n"
        "\t\tof_platform_device_create(node, NULL, NULL);\n",
        '\ta52_ackfr_record("OFPOP reserved begin");\n'
        "\tfor_each_matching_node(node, reserved_mem_matches) {\n"
        '\t\ta52_ackfr_record("OFPOP reserved node=%s", node->name);\n'
        "\t\tof_platform_device_create(node, NULL, NULL);\n"
        "\t}\n"
        '\ta52_ackfr_record("OFPOP reserved end");\n',
        f"{label}: reserved population trace",
    )

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        '\tnode = of_find_node_by_path("/firmware");\n',
        '\ta52_ackfr_record("OFPOP firmware begin");\n'
        '\tnode = of_find_node_by_path("/firmware");\n',
        f"{label}: firmware begin",
    )

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        "\tif (node) {\n"
        "\t\tof_platform_default_populate(node, NULL, NULL);\n"
        "\t\tof_node_put(node);\n"
        "\t}\n\n"
        "\t/* Populate everything else. */\n"
        "\tof_platform_default_populate(NULL, NULL, NULL);\n\n"
        "\treturn 0;\n",
        "\tif (node) {\n"
        "\t\tof_platform_default_populate(node, NULL, NULL);\n"
        "\t\tof_node_put(node);\n"
        "\t}\n"
        '\ta52_ackfr_record("OFPOP firmware end");\n\n'
        "\t/* Populate everything else. */\n"
        '\ta52_ackfr_record("OFPOP root begin");\n'
        "\tof_platform_default_populate(NULL, NULL, NULL);\n"
        '\ta52_ackfr_record("OFPOP root end");\n\n'
        "\tWRITE_ONCE(a52_phase237_ofpop_active, false);\n"
        '\ta52_ackfr_record("OFPOP exit rc=0");\n'
        "\treturn 0;\n",
        f"{label}: firmware/root/exit trace",
    )

    validate_of_platform(text, label)
    return text


def validate_of_platform(text: str, label: str) -> None:
    for token in (
        OFPOP_TRACE_MARKER,
        "#include <linux/a52_ack_secure_flight_recorder.h>",
        "bool a52_phase237_ofpop_active;",
        "WRITE_ONCE(a52_phase237_ofpop_active, true);",
        'a52_ackfr_record("OFPOP enter")',
        'a52_ackfr_record("OFPOP links-paused")',
        'a52_ackfr_record("OFPOP reserved begin")',
        'a52_ackfr_record("OFPOP reserved node=%s", node->name)',
        'a52_ackfr_record("OFPOP firmware begin")',
        'a52_ackfr_record("OFPOP root begin")',
        'a52_ackfr_record("OFPOP root end")',
        'a52_ackfr_record("OFPOP exit rc=0")',
        "arch_initcall_sync(of_platform_default_populate_init);",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing OFPOP trace token: {token}")


def patch_base_platform(text: str, label: str) -> str:
    if P3P_TRACE_MARKER in text:
        validate_base_platform(text, label)
        return text

    include_anchor = "#include <linux/types.h>\n"
    text = replace_once(
        text,
        include_anchor,
        include_anchor
        + "#include <linux/atomic.h>\n"
        + "#include <linux/a52_ack_secure_flight_recorder.h>\n",
        f"{label}: recorder/atomic includes",
    )

    globals_anchor = '#include "power/power.h"\n'
    text = replace_once(
        text,
        globals_anchor,
        globals_anchor
        + f"\n/* {P3P_TRACE_MARKER} */\n"
        + "extern bool a52_phase237_ofpop_active;\n"
        + "static atomic_t a52_phase237_p3p_count = ATOMIC_INIT(0);\n"
        + "#define A52_PHASE237_P3P_LIMIT 192\n",
        f"{label}: platform probe globals",
    )

    fn_start = "static int platform_drv_probe(struct device *_dev)\n{\n"
    fn_end = "static int platform_drv_probe_fail(struct device *_dev)"

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        "\tstruct platform_device *dev = to_platform_device(_dev);\n"
        "\tint ret;\n\n"
        "\tret = of_clk_set_defaults(_dev->of_node, false);\n",
        "\tstruct platform_device *dev = to_platform_device(_dev);\n"
        "\tint ret;\n"
        "\tint a52_p3p_seq = 0;\n"
        "\tbool a52_p3p_trace = READ_ONCE(a52_phase237_ofpop_active);\n\n"
        "\tif (a52_p3p_trace) {\n"
        "\t\ta52_p3p_seq = atomic_inc_return(&a52_phase237_p3p_count);\n"
        "\t\tif (a52_p3p_seq > A52_PHASE237_P3P_LIMIT) {\n"
        "\t\t\tif (a52_p3p_seq == A52_PHASE237_P3P_LIMIT + 1)\n"
        '\t\t\t\ta52_ackfr_record("P3P limit n=%d", a52_p3p_seq);\n'
        "\t\t\ta52_p3p_trace = false;\n"
        "\t\t}\n"
        "\t}\n"
        "\tif (a52_p3p_trace)\n"
        '\t\ta52_ackfr_record("P3P enter n=%d dev=%s drv=%s", a52_p3p_seq,\n'
        "\t\t\t\t dev_name(_dev), _dev->driver->name);\n\n"
        "\tret = of_clk_set_defaults(_dev->of_node, false);\n",
        f"{label}: platform probe entry",
    )

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        "\tif (ret < 0)\n\t\treturn ret;\n\n"
        "\tret = dev_pm_domain_attach(_dev, true);\n",
        "\tif (ret < 0) {\n"
        "\t\tif (a52_p3p_trace)\n"
        '\t\t\ta52_ackfr_record("P3P exit n=%d stage=clk rc=%d",\n'
        "\t\t\t\t\t a52_p3p_seq, ret);\n"
        "\t\treturn ret;\n"
        "\t}\n\n"
        "\tif (a52_p3p_trace)\n"
        '\t\ta52_ackfr_record("P3P pd n=%d drv=%s", a52_p3p_seq,\n'
        "\t\t\t\t _dev->driver->name);\n"
        "\tret = dev_pm_domain_attach(_dev, true);\n",
        f"{label}: platform probe clk/pd trace",
    )

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        "\tif (drv->probe) {\n"
        "\t\tret = drv->probe(dev);\n",
        "\tif (drv->probe) {\n"
        "\t\tif (a52_p3p_trace)\n"
        '\t\t\ta52_ackfr_record("P3P call n=%d drv=%s", a52_p3p_seq,\n'
        "\t\t\t\t\t _dev->driver->name);\n"
        "\t\tret = drv->probe(dev);\n",
        f"{label}: driver call trace",
    )

    text = replace_once_in(
        text,
        fn_start,
        fn_end,
        "\tif (drv->prevent_deferred_probe && ret == -EPROBE_DEFER) {\n"
        '\t\tdev_warn(_dev, "probe deferral not supported\\n");\n'
        "\t\tret = -ENXIO;\n"
        "\t}\n\n"
        "\treturn ret;\n",
        "\tif (drv->prevent_deferred_probe && ret == -EPROBE_DEFER) {\n"
        '\t\tdev_warn(_dev, "probe deferral not supported\\n");\n'
        "\t\tret = -ENXIO;\n"
        "\t}\n\n"
        "\tif (a52_p3p_trace)\n"
        '\t\ta52_ackfr_record("P3P exit n=%d rc=%d", a52_p3p_seq, ret);\n'
        "\treturn ret;\n",
        f"{label}: platform probe exit",
    )

    validate_base_platform(text, label)
    return text


def validate_base_platform(text: str, label: str) -> None:
    for token in (
        P3P_TRACE_MARKER,
        "#include <linux/a52_ack_secure_flight_recorder.h>",
        "extern bool a52_phase237_ofpop_active;",
        "A52_PHASE237_P3P_LIMIT 192",
        'a52_ackfr_record("P3P enter n=%d dev=%s drv=%s"',
        'a52_ackfr_record("P3P pd n=%d drv=%s"',
        'a52_ackfr_record("P3P call n=%d drv=%s"',
        'a52_ackfr_record("P3P exit n=%d stage=clk rc=%d"',
        'a52_ackfr_record("P3P exit n=%d rc=%d"',
        'a52_ackfr_record("P3P limit n=%d"',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing platform probe trace token: {token}")


def apply(arguments: list[str]) -> Path:
    root = locate_root(arguments)
    recorder_path = root / RECORDER_REL
    of_platform_path = root / OF_PLATFORM_REL
    base_platform_path = root / BASE_PLATFORM_REL

    recorder_path.write_text(
        patch_recorder(recorder_path.read_text(encoding="utf-8"), str(recorder_path)),
        encoding="utf-8",
    )
    of_platform_path.write_text(
        patch_of_platform(of_platform_path.read_text(encoding="utf-8"), str(of_platform_path)),
        encoding="utf-8",
    )
    base_platform_path.write_text(
        patch_base_platform(
            base_platform_path.read_text(encoding="utf-8"), str(base_platform_path)
        ),
        encoding="utf-8",
    )

    print(
        "Phase 237 OF/platform-probe recorder applied: transport unchanged; "
        "OF population gated P3P tracing limited to 192 probes",
        flush=True,
    )
    return root


def self_test() -> None:
    recorder_fixture = f'''void record(const char *fmt)
{{
\t/* {PHASE236_MARKER}
\t * inherited
\t */
{PHASE236_FILTER}
}}
const char *id = "{PHASE236_BOOT}";
'''
    patched_recorder = patch_recorder(recorder_fixture, "phase237-recorder-fixture")
    if patch_recorder(patched_recorder, "phase237-recorder-idempotence") != patched_recorder:
        raise AssertionError("Phase 237 recorder patch is not idempotent")

    of_fixture = r'''#include <linux/platform_device.h>

int of_platform_default_populate(struct device_node *root,
\t\t\t\t const struct of_dev_auxdata *lookup,
\t\t\t\t struct device *parent)
{
\treturn 0;
}
EXPORT_SYMBOL_GPL(of_platform_default_populate);

#ifndef CONFIG_PPC
static const struct of_device_id reserved_mem_matches[] = {
\t{ .compatible = "ramoops" },
\t{}
};

static int __init of_platform_default_populate_init(void)
{
\tstruct device_node *node;

\tdevice_links_supplier_sync_state_pause();

\tif (!of_have_populated_dt())
\t\treturn -ENODEV;

\tfor_each_matching_node(node, reserved_mem_matches)
\t\tof_platform_device_create(node, NULL, NULL);

\tnode = of_find_node_by_path("/firmware");
\tif (node) {
\t\tof_platform_default_populate(node, NULL, NULL);
\t\tof_node_put(node);
\t}

\t/* Populate everything else. */
\tof_platform_default_populate(NULL, NULL, NULL);

\treturn 0;
}
arch_initcall_sync(of_platform_default_populate_init);
#endif
'''
    patched_of = patch_of_platform(of_fixture, "phase237-of-fixture")
    if patch_of_platform(patched_of, "phase237-of-idempotence") != patched_of:
        raise AssertionError("Phase 237 OF patch is not idempotent")

    base_fixture = r'''#include <linux/types.h>

#include "base.h"
#include "power/power.h"

static int platform_drv_probe(struct device *_dev)
{
\tstruct platform_driver *drv = to_platform_driver(_dev->driver);
\tstruct platform_device *dev = to_platform_device(_dev);
\tint ret;

\tret = of_clk_set_defaults(_dev->of_node, false);
\tif (ret < 0)
\t\treturn ret;

\tret = dev_pm_domain_attach(_dev, true);
\tif (ret)
\t\tgoto out;

\tif (drv->probe) {
\t\tret = drv->probe(dev);
\t\tif (ret)
\t\t\tdev_pm_domain_detach(_dev, true);
\t}

out:
\tif (drv->prevent_deferred_probe && ret == -EPROBE_DEFER) {
\t\tdev_warn(_dev, "probe deferral not supported\n");
\t\tret = -ENXIO;
\t}

\treturn ret;
}

static int platform_drv_probe_fail(struct device *_dev)
{
\treturn -ENXIO;
}
'''
    patched_base = patch_base_platform(base_fixture, "phase237-base-fixture")
    if patch_base_platform(patched_base, "phase237-base-idempotence") != patched_base:
        raise AssertionError("Phase 237 platform probe patch is not idempotent")

    print("Phase 237 OF/platform-probe overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
