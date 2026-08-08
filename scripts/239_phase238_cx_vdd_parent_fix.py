#!/usr/bin/env python3
"""Phase 239: restore TouchGrass gpu_cx_gdsc vdd_parent semantics.

Runs after the SHA-locked Phase 233 graphics parity source is generated and
before Phase 238 instrumentation. The Lagoon CX GDSC carries both
`parent-supply` and `vdd_parent-supply`; Phase 233 preserved only the former.
This overlay restores the latter only for GPU CX.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

PHASE233_MARKER = "A52_PHASE233_FINAL_GRAPHICS_PARITY"
PHASE238_GDSC_MARKER = "A52_PHASE238_GDSC_PROVIDER_TRACE_V1"
PHASE239_MARKER = "A52_PHASE239_GPU_CX_VDD_PARENT_V1"
GDSC_REL = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}: {old[:90]!r}")
    return text.replace(old, new, 1)


def find_function(text: str, pattern: str, label: str) -> tuple[int, int]:
    m = re.search(pattern, text, re.M)
    if not m:
        raise RuntimeError(f"{label}: function signature not found")
    brace = text.find("{", m.start(), m.end() + 2)
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace missing")
    depth = 0
    state = "code"
    i = brace
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*":
                state = "block"; i += 2; continue
            if c == "/" and n == "/":
                state = "line"; i += 2; continue
            if c == '"':
                state = "string"; i += 1; continue
            if c == "'":
                state = "char"; i += 1; continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), i + 1
        elif state == "block":
            if c == "*" and n == "/":
                state = "code"; i += 2; continue
        elif state == "line":
            if c == "\n":
                state = "code"
        else:
            quote = '"' if state == "string" else "'"
            if c == "\\":
                i += 2; continue
            if c == quote:
                state = "code"
        i += 1
    raise RuntimeError(f"{label}: unterminated function")


IS_ENABLED = r'''static int a52_legacy_gdsc_is_enabled(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 val;
    int ret;
    bool enabled;

    if (gdsc->profile == A52_GDSC_PROFILE_GPU_GX &&
        gdsc->skip_disable_before_enable)
        return 0;

    if (!gdsc->parent_regulator) {
        val = readl_relaxed(gdsc->gdscr);
        return !!((val & A52_GDSC_PWR_ON) &&
                  !(val & A52_GDSC_SW_COLLAPSE));
    }

    ret = regulator_is_enabled(gdsc->parent_regulator);
    if (ret <= 0) {
        a52_ackfr_record("A52GDSC CX_VDD_PARENT is-enabled parent=%d", ret);
        return 0;
    }

    ret = regulator_set_voltage(gdsc->parent_regulator,
                                A52_GDSC_VDD_PARENT_LOW_SVS, INT_MAX);
    if (ret) {
        a52_ackfr_record("A52GDSC CX_VDD_PARENT is-vote rc=%d", ret);
        return 0;
    }

    ret = regulator_enable(gdsc->parent_regulator);
    if (ret) {
        regulator_set_voltage(gdsc->parent_regulator, 0, INT_MAX);
        a52_ackfr_record("A52GDSC CX_VDD_PARENT is-enable rc=%d", ret);
        return 0;
    }

    val = readl_relaxed(gdsc->gdscr);
    enabled = !!((val & A52_GDSC_PWR_ON) &&
                 !(val & A52_GDSC_SW_COLLAPSE));
    regulator_disable(gdsc->parent_regulator);
    regulator_set_voltage(gdsc->parent_regulator, 0, INT_MAX);
    a52_ackfr_record("A52GDSC CX_VDD_PARENT is-enabled=%u reg=0x%x",
                     enabled, val);
    return enabled;
}'''


def patch_is_enabled(text: str, label: str) -> str:
    start, end = find_function(
        text,
        r"static\s+int\s+a52_legacy_gdsc_is_enabled\s*\(\s*struct\s+regulator_dev\s*\*\s*rdev\s*\)\s*\{",
        f"{label}: is_enabled",
    )
    return text[:start] + IS_ENABLED + text[end:]


def patch_enable(text: str, label: str) -> str:
    start, end = find_function(
        text,
        r"static\s+int\s+a52_legacy_gdsc_enable\s*\(\s*struct\s+regulator_dev\s*\*\s*rdev\s*\)\s*\{",
        f"{label}: enable",
    )
    fn = text[start:end]
    fn = replace_once(
        fn,
        "    u32 before, val;\n    int ret;\n\n    before = readl_relaxed(gdsc->gdscr);",
        '''    u32 before, val;
    int ret;

    if (gdsc->parent_regulator) {
        ret = regulator_set_voltage(gdsc->parent_regulator,
                                    A52_GDSC_VDD_PARENT_LOW_SVS, INT_MAX);
        a52_ackfr_record("A52GDSC CX_VDD_PARENT enable-vote rc=%d", ret);
        if (ret)
            return ret;
    }

    before = readl_relaxed(gdsc->gdscr);''',
        f"{label}: enable vote",
    )
    fn = replace_once(
        fn,
        '''    if (ret)
        dev_err(gdsc->dev, "enable timed out, GDSCR=0x%08x\\n", val);
    return ret;''',
        '''    if (ret)
        dev_err(gdsc->dev, "enable timed out, GDSCR=0x%08x\\n", val);
    if (ret && gdsc->parent_regulator)
        regulator_set_voltage(gdsc->parent_regulator, 0, INT_MAX);
    return ret;''',
        f"{label}: enable error unwind",
    )
    return text[:start] + fn + text[end:]


def patch_cx_disable(text: str, label: str) -> str:
    start, end = find_function(
        text,
        r"static\s+int\s+a52_legacy_gdsc_disable_gpu_cx\s*\(\s*struct\s+regulator_dev\s*\*\s*rdev\s*\)\s*\{",
        f"{label}: cx disable",
    )
    fn = text[start:end]
    fn = replace_once(
        fn,
        '''    if (ret)
        dev_err(gdsc->dev, "GPU CX disable failed: %d, GDSCR=0x%08x\\n",
                ret, val);
    return ret;''',
        '''    if (ret)
        dev_err(gdsc->dev, "GPU CX disable failed: %d, GDSCR=0x%08x\\n",
                ret, val);
    if (gdsc->parent_regulator) {
        int parent_rc = regulator_set_voltage(gdsc->parent_regulator, 0, INT_MAX);

        a52_ackfr_record("A52GDSC CX_VDD_PARENT disable-unvote rc=%d",
                         parent_rc);
        if (!ret && parent_rc)
            ret = parent_rc;
    }
    return ret;''',
        f"{label}: cx disable unvote",
    )
    return text[:start] + fn + text[end:]


def patch_probe(text: str, label: str) -> str:
    start, end = find_function(
        text,
        r"static\s+int\s+a52_legacy_gdsc_probe\s*\(\s*struct\s+platform_device\s*\*\s*pdev\s*\)\s*\{",
        f"{label}: probe",
    )
    fn = text[start:end]
    fn = replace_once(
        fn,
        "    const char *name;\n    u32 before, val;",
        "    const char *name;\n    u32 before, val;\n    int ret;",
        f"{label}: probe ret",
    )
    fn = replace_once(
        fn,
        '''        if (of_get_property(pdev->dev.of_node, "parent-supply", NULL))
            init_data->supply_regulator = "parent";

        gdsc->timeout_us = A52_GDSC_TIMEOUT_US;''',
        '''        if (of_get_property(pdev->dev.of_node, "parent-supply", NULL))
            init_data->supply_regulator = "parent";

        if (of_find_property(pdev->dev.of_node, "vdd_parent-supply", NULL)) {
            gdsc->parent_regulator = devm_regulator_get(&pdev->dev,
                                                        "vdd_parent");
            if (IS_ERR(gdsc->parent_regulator)) {
                ret = PTR_ERR(gdsc->parent_regulator);
                gdsc->parent_regulator = NULL;
                a52_ackfr_record("A52GDSC CX_VDD_PARENT get rc=%d", ret);
                if (ret != -EPROBE_DEFER)
                    dev_err(&pdev->dev,
                            "Unable to get vdd_parent regulator, ret=%d\\n",
                            ret);
                return ret;
            }
            a52_ackfr_record("A52GDSC CX_VDD_PARENT get rc=0");
        }

        gdsc->timeout_us = A52_GDSC_TIMEOUT_US;''',
        f"{label}: vdd_parent acquire",
    )
    return text[:start] + fn + text[end:]


def patch_gdsc(text: str, label: str) -> str:
    if PHASE239_MARKER in text:
        validate(text, label)
        return text
    if PHASE233_MARKER not in text:
        raise RuntimeError(f"{label}: Phase 233 marker missing")
    if PHASE238_GDSC_MARKER in text:
        raise RuntimeError(f"{label}: Phase 239 must run before Phase 238 GDSC instrumentation")

    text = replace_once(
        text,
        f" * {PHASE233_MARKER}\n",
        f" * {PHASE233_MARKER}\n * {PHASE239_MARKER}\n",
        f"{label}: marker",
    )
    text = replace_once(
        text,
        "#include <linux/regulator/driver.h>\n",
        "#include <linux/regulator/driver.h>\n#include <linux/regulator/consumer.h>\n#include <linux/limits.h>\n",
        f"{label}: consumer include",
    )
    text = replace_once(
        text,
        "#define A52_GDSC_CLK_DIS_WAIT_SHIFT 12\n",
        "#define A52_GDSC_CLK_DIS_WAIT_SHIFT 12\n"
        "/* Exact TouchGrass RPMH_REGULATOR_LEVEL_LOW_SVS value. */\n"
        "#define A52_GDSC_VDD_PARENT_LOW_SVS 64\n",
        f"{label}: LOW_SVS",
    )
    text = replace_once(
        text,
        "    struct regmap *domain_addr;\n    struct regmap *sw_reset;\n",
        "    struct regmap *domain_addr;\n    struct regmap *sw_reset;\n"
        "    struct regulator *parent_regulator;\n",
        f"{label}: parent field",
    )
    text = patch_is_enabled(text, label)
    text = patch_enable(text, label)
    text = patch_cx_disable(text, label)
    text = patch_probe(text, label)
    validate(text, label)
    return text


def validate(text: str, label: str) -> None:
    for token in (
        PHASE233_MARKER,
        PHASE239_MARKER,
        "#include <linux/regulator/consumer.h>",
        "A52_GDSC_VDD_PARENT_LOW_SVS 64",
        "struct regulator *parent_regulator;",
        'of_find_property(pdev->dev.of_node, "vdd_parent-supply", NULL)',
        'devm_regulator_get(&pdev->dev,',
        '"vdd_parent"',
        "regulator_is_enabled(gdsc->parent_regulator)",
        "regulator_enable(gdsc->parent_regulator)",
        "regulator_disable(gdsc->parent_regulator)",
        "regulator_set_voltage(gdsc->parent_regulator,",
        'A52GDSC CX_VDD_PARENT get rc=%d',
        'A52GDSC CX_VDD_PARENT enable-vote rc=%d',
        'A52GDSC CX_VDD_PARENT disable-unvote rc=%d',
        'init_data->supply_regulator = "parent"',
        '"gpu_cx_gdsc"',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    if text.count(PHASE239_MARKER) != 1:
        raise RuntimeError(f"{label}: Phase 239 marker count != 1")
    if text.count('devm_regulator_get(&pdev->dev,') != 1:
        raise RuntimeError(f"{label}: vdd_parent acquisition is not exact")


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
            seen.add(key)
            out.append(root)
    return out


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    matches: list[Path] = []
    for root in candidate_roots(args, base):
        path = root / GDSC_REL
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if PHASE233_MARKER in text and (
            PHASE239_MARKER in text or PHASE238_GDSC_MARKER not in text
        ):
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
        raise RuntimeError(
            f"expected one generated Phase 233 source root, found {len(uniq)}: {rendered}"
        )
    return uniq[0]


def self_test() -> None:
    fixture = r'''/*
 * A52_PHASE233_FINAL_GRAPHICS_PARITY
 */
#include <linux/regulator/driver.h>
#define A52_GDSC_CLK_DIS_WAIT_SHIFT 12
#define A52_GDSC_PWR_ON BIT(31)
#define A52_GDSC_SW_COLLAPSE BIT(0)
#define A52_GDSC_HW_CONTROL BIT(1)
#define A52_GDSC_SW_OVERRIDE BIT(2)
#define A52_GDSC_TIMEOUT_US 100
struct a52_legacy_gdsc {
    struct device *dev;
    void __iomem *gdscr;
    struct regulator_desc desc;
    enum a52_legacy_gdsc_profile profile;
    struct regmap *domain_addr;
    struct regmap *sw_reset;
    bool support_hw_trigger;
    bool reset_aon;
    bool no_status_check_on_disable;
    bool skip_disable_before_enable;
    u32 timeout_us;
};
static int a52_legacy_gdsc_is_enabled(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 val = readl_relaxed(gdsc->gdscr);
    if (gdsc->profile == A52_GDSC_PROFILE_GPU_GX &&
        gdsc->skip_disable_before_enable)
        return 0;
    return !!((val & A52_GDSC_PWR_ON) &&
              !(val & A52_GDSC_SW_COLLAPSE));
}
static int a52_legacy_gdsc_enable(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 before, val;
    int ret;

    before = readl_relaxed(gdsc->gdscr);
    val = before;
    ret = a52_legacy_gdsc_poll(gdsc, true, &val);
    if (ret)
        dev_err(gdsc->dev, "enable timed out, GDSCR=0x%08x\n", val);
    return ret;
}
static int a52_legacy_gdsc_disable_gpu_cx(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 before, val;
    int ret = 0;
    if (ret)
        dev_err(gdsc->dev, "GPU CX disable failed: %d, GDSCR=0x%08x\n",
                ret, val);
    return ret;
}
static int a52_legacy_gdsc_probe(struct platform_device *pdev)
{
    struct regulator_init_data *init_data = NULL;
    struct a52_legacy_gdsc *gdsc;
    const char *name;
    u32 before, val;
    if (gdsc->profile == A52_GDSC_PROFILE_GPU_CX) {
        u32 clk_dis_wait = 0;
        init_data = of_get_regulator_init_data(&pdev->dev,
                                               pdev->dev.of_node,
                                               &gdsc->desc);
        if (!init_data)
            return -ENOMEM;
        if (of_get_property(pdev->dev.of_node, "parent-supply", NULL))
            init_data->supply_regulator = "parent";

        gdsc->timeout_us = A52_GDSC_TIMEOUT_US;
        name = "gpu_cx_gdsc";
    }
    return 0;
}
'''
    patched = patch_gdsc(fixture, "fixture")
    if patch_gdsc(patched, "fixture/idempotent") != patched:
        raise AssertionError("Phase 239 patch is not idempotent")
    if patched.count('"vdd_parent-supply"') != 1:
        raise AssertionError("Phase 239 vdd_parent property scope is not exact")
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        root = repo / "gki/common"
        path = root / GDSC_REL
        path.parent.mkdir(parents=True)
        path.write_text(fixture, encoding="utf-8")
        if locate_generated([], cwd=repo).resolve() != root.resolve():
            raise AssertionError("Phase 239 generated-root locator failed")
    print("Phase 239 gpu_cx_gdsc vdd_parent self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    path = root / GDSC_REL
    before = path.read_text(encoding="utf-8")
    after = patch_gdsc(before, str(path))
    path.write_text(after, encoding="utf-8")
    print(f"Phase 239 CX vdd_parent semantics applied to {path}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 239 CX vdd_parent fix failed: {exc}", file=sys.stderr)
        raise
