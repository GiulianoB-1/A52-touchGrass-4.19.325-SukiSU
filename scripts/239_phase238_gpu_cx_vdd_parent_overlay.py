#!/usr/bin/env python3
"""Phase 239: restore Qualcomm vdd_parent semantics for Lagoon GPU CX GDSC.

Phase 233 added the exact gpu_cx_gdsc profile but only preserved the generic
regulator-core ``parent-supply`` ordering.  The downstream Qualcomm GDSC
contract also gives this node a separate ``vdd_parent-supply`` consumer.  That
consumer carries the LOW_SVS operational vote required while the CX GDSC is
accessed/enabled.  Restore that behavior without changing GX/UFS/MDSS profiles,
device links, or probe return values.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

GDSC = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
MARKER = "A52_PHASE239_GPU_CX_VDD_PARENT_V1"
PHASE233_MARKER = "A52_PHASE233_FINAL_GRAPHICS_PARITY"

INCLUDE_ANCHOR = "#include <linux/platform_device.h>\n"
INCLUDE_ADD = (
    "#include <linux/platform_device.h>\n"
    "#include <linux/kernel.h>\n"
    "#include <linux/regulator/consumer.h>\n"
    "#include <dt-bindings/regulator/qcom,rpmh-regulator-levels.h>\n"
)

STRUCT_OLD = '''    struct regmap *sw_reset;\n    bool support_hw_trigger;\n'''
STRUCT_NEW = '''    struct regmap *sw_reset;\n    struct regulator *parent_regulator; /* A52_PHASE239_GPU_CX_VDD_PARENT_V1 */\n    bool support_hw_trigger;\n'''

IS_ENABLED_OLD = r'''static int a52_legacy_gdsc_is_enabled(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    u32 val = readl_relaxed(gdsc->gdscr);

    if (gdsc->profile == A52_GDSC_PROFILE_GPU_GX &&
        gdsc->skip_disable_before_enable)
        return 0;

    return !!((val & A52_GDSC_PWR_ON) &&
              !(val & A52_GDSC_SW_COLLAPSE));
}
'''

IS_ENABLED_NEW = r'''static int a52_legacy_gdsc_parent_vote(struct a52_legacy_gdsc *gdsc)
{
    int ret;

    if (!gdsc->parent_regulator)
        return 0;

    ret = regulator_set_voltage(gdsc->parent_regulator,
                                RPMH_REGULATOR_LEVEL_LOW_SVS, INT_MAX);
    a52_ackfr_record("A52GDSC CX_VDD_PARENT_VOTE_V1 name=%s rc=%d",
                     gdsc->desc.name, ret);
    return ret;
}

static void a52_legacy_gdsc_parent_unvote(struct a52_legacy_gdsc *gdsc)
{
    int ret;

    if (!gdsc->parent_regulator)
        return;

    ret = regulator_set_voltage(gdsc->parent_regulator, 0, INT_MAX);
    a52_ackfr_record("A52GDSC CX_VDD_PARENT_UNVOTE_V1 name=%s rc=%d",
                     gdsc->desc.name, ret);
}

static int a52_legacy_gdsc_is_enabled(struct regulator_dev *rdev)
{
    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);
    bool parent_ref = false;
    u32 val;
    int enabled;
    int ret;

    if (gdsc->profile == A52_GDSC_PROFILE_GPU_GX &&
        gdsc->skip_disable_before_enable)
        return 0;

    if (gdsc->parent_regulator) {
        enabled = regulator_is_enabled(gdsc->parent_regulator);
        a52_ackfr_record("A52GDSC CX_VDD_PARENT_STATE_V1 name=%s enabled=%d",
                         gdsc->desc.name, enabled);
        if (enabled <= 0)
            return 0;

        ret = a52_legacy_gdsc_parent_vote(gdsc);
        if (ret)
            return 0;
        ret = regulator_enable(gdsc->parent_regulator);
        if (ret) {
            a52_legacy_gdsc_parent_unvote(gdsc);
            return 0;
        }
        parent_ref = true;
    }

    val = readl_relaxed(gdsc->gdscr);
    enabled = !!((val & A52_GDSC_PWR_ON) &&
                 !(val & A52_GDSC_SW_COLLAPSE));

    if (parent_ref) {
        regulator_disable(gdsc->parent_regulator);
        a52_legacy_gdsc_parent_unvote(gdsc);
    }
    return enabled;
}
'''

ENABLE_ANCHOR_OLD = '''    before = readl_relaxed(gdsc->gdscr);\n    val = before;\n'''
ENABLE_ANCHOR_NEW = '''    ret = a52_legacy_gdsc_parent_vote(gdsc);\n    if (ret)\n        return ret;\n\n    before = readl_relaxed(gdsc->gdscr);\n    val = before;\n'''

ENABLE_RETURN_OLD = '''    if (ret)\n        dev_err(gdsc->dev, "enable timed out, GDSCR=0x%08x\\n", val);\n    return ret;\n}\n'''
ENABLE_RETURN_NEW = '''    if (ret) {\n        dev_err(gdsc->dev, "enable timed out, GDSCR=0x%08x\\n", val);\n        a52_legacy_gdsc_parent_unvote(gdsc);\n    }\n    return ret;\n}\n'''

CX_PROBE_OLD = r'''        if (of_get_property(pdev->dev.of_node, "parent-supply", NULL))
            init_data->supply_regulator = "parent";

        gdsc->timeout_us = A52_GDSC_TIMEOUT_US;
'''
CX_PROBE_NEW = r'''        if (of_get_property(pdev->dev.of_node, "parent-supply", NULL))
            init_data->supply_regulator = "parent";

        if (of_find_property(pdev->dev.of_node, "vdd_parent-supply", NULL)) {
            int parent_ret;

            gdsc->parent_regulator = devm_regulator_get(&pdev->dev,
                                                        "vdd_parent");
            if (IS_ERR(gdsc->parent_regulator)) {
                parent_ret = PTR_ERR(gdsc->parent_regulator);
                gdsc->parent_regulator = NULL;
                a52_ackfr_record(
                    "A52GDSC CX_VDD_PARENT_GET_V1 dev=%s rc=%d",
                    dev_name(&pdev->dev), parent_ret);
                if (parent_ret != -EPROBE_DEFER)
                    dev_err(&pdev->dev,
                            "Unable to get vdd_parent regulator: %d\n",
                            parent_ret);
                return parent_ret;
            }
            a52_ackfr_record(
                "A52GDSC CX_VDD_PARENT_GET_V1 dev=%s rc=0",
                dev_name(&pdev->dev));
        }

        gdsc->timeout_us = A52_GDSC_TIMEOUT_US;
'''

CX_DISABLE_RETURN_OLD = '''    if (ret)\n        dev_err(gdsc->dev, "GPU CX disable failed: %d, GDSCR=0x%08x\\n",\n                ret, val);\n    return ret;\n}\n'''
CX_DISABLE_RETURN_NEW = '''    if (ret)\n        dev_err(gdsc->dev, "GPU CX disable failed: %d, GDSCR=0x%08x\\n",\n                ret, val);\n    a52_legacy_gdsc_parent_unvote(gdsc);\n    return ret;\n}\n'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_gdsc(text: str, label: str) -> str:
    if MARKER in text:
        validate(text, label)
        return text
    if PHASE233_MARKER not in text:
        raise RuntimeError(f"{label}: Phase 233 GDSC marker missing")

    if "#include <linux/regulator/consumer.h>" not in text:
        text = replace_once(text, INCLUDE_ANCHOR, INCLUDE_ADD, f"{label}: includes")
    text = replace_once(text, STRUCT_OLD, STRUCT_NEW, f"{label}: parent field")
    text = replace_once(text, IS_ENABLED_OLD, IS_ENABLED_NEW, f"{label}: parent helpers")

    enable_start = text.find("static int a52_legacy_gdsc_enable(struct regulator_dev *rdev)")
    enable_end = text.find("static int a52_legacy_gdsc_regmap_bit", enable_start)
    if enable_start < 0 or enable_end < 0:
        raise RuntimeError(f"{label}: generic enable function not found")
    enable = text[enable_start:enable_end]
    enable = replace_once(enable, ENABLE_ANCHOR_OLD, ENABLE_ANCHOR_NEW,
                          f"{label}: enable vote")
    enable = replace_once(enable, ENABLE_RETURN_OLD, ENABLE_RETURN_NEW,
                          f"{label}: enable unwind")
    text = text[:enable_start] + enable + text[enable_end:]

    text = replace_once(text, CX_PROBE_OLD, CX_PROBE_NEW, f"{label}: CX acquire")

    disable_start = text.find("static int a52_legacy_gdsc_disable_gpu_cx")
    disable_end = text.find("static int a52_legacy_gdsc_disable_ufs", disable_start)
    if disable_start < 0 or disable_end < 0:
        raise RuntimeError(f"{label}: GPU CX disable function not found")
    disable = text[disable_start:disable_end]
    disable = replace_once(disable, CX_DISABLE_RETURN_OLD, CX_DISABLE_RETURN_NEW,
                           f"{label}: CX unvote")
    text = text[:disable_start] + disable + text[disable_end:]

    validate(text, label)
    return text


def validate(text: str, label: str) -> None:
    for token in (
        MARKER,
        '#include <linux/regulator/consumer.h>',
        '#include <dt-bindings/regulator/qcom,rpmh-regulator-levels.h>',
        'struct regulator *parent_regulator',
        '"vdd_parent-supply"',
        'devm_regulator_get(&pdev->dev',
        '"vdd_parent"',
        'RPMH_REGULATOR_LEVEL_LOW_SVS',
        'A52GDSC CX_VDD_PARENT_GET_V1',
        'A52GDSC CX_VDD_PARENT_VOTE_V1',
        'A52GDSC CX_VDD_PARENT_UNVOTE_V1',
        'A52GDSC CX_VDD_PARENT_STATE_V1',
        'init_data->supply_regulator = "parent"',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


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
    path = root / GDSC
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return PHASE233_MARKER in text and (
        MARKER in text or '"gpu_cx_gdsc"' in text
    )


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
            "expected exactly one generated Phase 233 GDSC source root, "
            f"found {len(unique)}: {rendered}"
        )
    return unique[0]


def self_test() -> None:
    fixture = (
        '#include <linux/platform_device.h>\n'
        '/* A52_PHASE233_FINAL_GRAPHICS_PARITY */\n'
        '"gpu_cx_gdsc"\n'
        'struct a52_legacy_gdsc {\n'
        '    struct regmap *sw_reset;\n'
        '    bool support_hw_trigger;\n'
        '};\n'
        + IS_ENABLED_OLD
        + '''static int a52_legacy_gdsc_enable(struct regulator_dev *rdev)\n{\n'''
          '''    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);\n'''
          '''    u32 before, val;\n    int ret;\n\n'''
          + ENABLE_ANCHOR_OLD
          + '''    ret = a52_legacy_gdsc_poll(gdsc, true, &val);\n'''
          + ENABLE_RETURN_OLD
          + '''static int a52_legacy_gdsc_regmap_bit(void) { return 0; }\n'''
          '''static int a52_legacy_gdsc_disable_gpu_cx(struct regulator_dev *rdev)\n{\n'''
          '''    struct a52_legacy_gdsc *gdsc = rdev_get_drvdata(rdev);\n'''
          '''    u32 val = 0;\n    int ret = 0;\n'''
          + CX_DISABLE_RETURN_OLD
          + '''static int a52_legacy_gdsc_disable_ufs(struct regulator_dev *rdev) { return 0; }\n'''
          '''static int probe_fixture(struct platform_device *pdev)\n{\n'''
          '''    struct regulator_init_data *init_data;\n'''
          '''    struct a52_legacy_gdsc *gdsc;\n'''
          + CX_PROBE_OLD
          + '''    return 0;\n}\n'''
    )
    patched = patch_gdsc(fixture, "fixture/gdsc.c")
    if patch_gdsc(patched, "fixture/gdsc.c/idempotent") != patched:
        raise AssertionError("Phase 239 CX vdd_parent patch is not idempotent")
    if patched.count('devm_regulator_get(&pdev->dev') != 1:
        raise AssertionError("Phase 239 CX parent acquisition is not exact")
    if patched.count('RPMH_REGULATOR_LEVEL_LOW_SVS') != 1:
        raise AssertionError("Phase 239 CX LOW_SVS vote count drifted")

    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        generated = repo / "gki/common"
        path = generated / GDSC
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fixture, encoding="utf-8")
        found = locate_generated([], cwd=repo)
        if found.resolve() != generated.resolve():
            raise AssertionError(f"locator chose {found}, expected {generated}")

    print("Phase 239 GPU CX vdd_parent parity self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    path = root / GDSC
    before = path.read_text(encoding="utf-8")
    after = patch_gdsc(before, str(path))
    path.write_text(after, encoding="utf-8")
    print(
        "Phase 239 GPU CX vdd_parent parity applied: separate vdd_parent supply "
        "acquired and LOW_SVS vote semantics restored",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
