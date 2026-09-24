#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

TARGET = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
TG_PROXY = Path("drivers/regulator/proxy-consumer.c")
TG_CORE = Path("drivers/regulator/core.c")
MARK = "A52_PHASE374_MDSS_GDSC_PROXY_PARITY_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Phase374 {label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def validate_touchgrass(tg: Path) -> None:
    proxy_path = tg / TG_PROXY
    core_path = tg / TG_CORE
    if not proxy_path.is_file() or not core_path.is_file():
        raise SystemExit("Phase374 pinned TouchGrass proxy-consumer sources missing")

    proxy = proxy_path.read_text(errors="replace")
    core = core_path.read_text(errors="replace")
    required_proxy = (
        'of_find_property(reg_node, "qcom,proxy-consumer-enable", NULL)',
        'consumer->enable',
        'of_property_read_bool(reg_node, "qcom,proxy-consumer-enable")',
        'consumer->reg = regulator_get(reg_dev, "proxy");',
        'rc = regulator_enable(consumer->reg);',
        'rc = regulator_disable(consumer->reg);',
        'late_initcall_sync(regulator_proxy_consumer_remove_all);',
    )
    for token in required_proxy:
        if token not in proxy:
            raise SystemExit("Phase374 TouchGrass proxy contract drifted: " + token)

    for token in (
        "rdev->proxy_consumer = regulator_proxy_consumer_register(dev,",
        "config->of_node);",
    ):
        if token not in core:
            raise SystemExit(
                "Phase374 TouchGrass regulator-core proxy hook drifted: " + token
            )


BLOCK = r'''
/* A52_PHASE374_MDSS_GDSC_PROXY_PARITY_V1
 *
 * TouchGrass 4.19 regulator core creates a temporary proxy consumer for any
 * regulator node carrying qcom,proxy-consumer-enable. The A52 mdss_core_gdsc
 * node points proxy-supply back to itself, so the proxy holds one enable vote
 * through early boot and releases it at late_initcall_sync.
 *
 * Android common 5.10 has no downstream proxy-consumer extension. Reproduce
 * only the exact A52 MDSS enable-only contract here. Do not keep the domain
 * forced on past late init and do not alter UFS/GPU GDSC profiles.
 */
static struct regulator *a52_p374_mdss_proxy;
static struct a52_legacy_gdsc *a52_p374_mdss_gdsc;
static bool a52_p374_mdss_proxy_enabled;

static void a52_p374_mdss_proxy_acquire(struct platform_device *pdev,
                                        struct a52_legacy_gdsc *gdsc)
{
	struct regulator *proxy;
	u32 reg = 0;
	int rc;

	if (!pdev || !gdsc)
		return;
	if (!of_property_read_bool(pdev->dev.of_node,
				   "qcom,proxy-consumer-enable")) {
		a52_ackfr_record("P374 PX+ missing");
		return;
	}
	if (a52_p374_mdss_proxy) {
		a52_ackfr_record("P374 PX+ duplicate");
		return;
	}

	proxy = regulator_get(&pdev->dev, "proxy");
	if (IS_ERR(proxy)) {
		rc = PTR_ERR(proxy);
		a52_ackfr_record("P374 PX+ get=%d", rc);
		return;
	}

	rc = regulator_enable(proxy);
	if (rc) {
		a52_ackfr_record("P374 PX+ en=%d", rc);
		regulator_put(proxy);
		return;
	}

	a52_p374_mdss_proxy = proxy;
	a52_p374_mdss_gdsc = gdsc;
	a52_p374_mdss_proxy_enabled = true;
	reg = readl_relaxed(gdsc->gdscr);
	a52_ackfr_record("P374 PX+ en=0 reg=%x", reg);
}

static int __init a52_p374_mdss_proxy_release(void)
{
	struct regulator *proxy = a52_p374_mdss_proxy;
	struct a52_legacy_gdsc *gdsc = a52_p374_mdss_gdsc;
	u32 before = 0, after = 0;
	int rc = 0;

	if (!proxy || IS_ERR(proxy)) {
		a52_ackfr_record("P374 PX- none");
		return 0;
	}

	if (gdsc)
		before = readl_relaxed(gdsc->gdscr);

	if (a52_p374_mdss_proxy_enabled)
		rc = regulator_disable(proxy);

	if (gdsc)
		after = readl_relaxed(gdsc->gdscr);

	a52_ackfr_record("P374 PX- rc=%d before=%x after=%x",
			 rc, before, after);

	regulator_put(proxy);
	a52_p374_mdss_proxy = NULL;
	a52_p374_mdss_gdsc = NULL;
	a52_p374_mdss_proxy_enabled = false;
	return 0;
}
late_initcall_sync(a52_p374_mdss_proxy_release);
'''


def patch(text: str) -> str:
    if MARK in text:
        return text

    for token in (
        '"mdss_core_gdsc"',
        "A52_GDSC_PROFILE_MDSS",
        "static int a52_legacy_gdsc_probe(struct platform_device *pdev)",
        "a52_legacy_gdsc_mdss_ops",
    ):
        if token not in text:
            raise SystemExit("Phase374 inherited MDSS GDSC contract missing: " + token)

    if "regulator_proxy_consumer_register" in text or "qcom,proxy-consumer-enable" in text:
        raise SystemExit("Phase374 baseline unexpectedly already implements proxy-consumer semantics")

    if "#include <linux/regulator/consumer.h>\n" not in text:
        text = one(
            text,
            "#include <linux/regulator/driver.h>\n",
            "#include <linux/regulator/driver.h>\n"
            "#include <linux/regulator/consumer.h>\n",
            "consumer include",
        )

    probe_anchor = "static int a52_legacy_gdsc_probe(struct platform_device *pdev)\n"
    text = one(text, probe_anchor, BLOCK + "\n" + probe_anchor, "proxy helper insertion")

    call_anchor = "\tplatform_set_drvdata(pdev, gdsc);\n"
    call = (
        "\tif (gdsc->profile == A52_GDSC_PROFILE_MDSS)\n"
        "\t\ta52_p374_mdss_proxy_acquire(pdev, gdsc);\n\n"
        + call_anchor
    )
    text = one(text, call_anchor, call, "MDSS probe acquisition")
    return text


def validate(text: str) -> None:
    required = (
        MARK,
        '#include <linux/regulator/consumer.h>',
        'of_property_read_bool(pdev->dev.of_node,\n\t\t\t\t   "qcom,proxy-consumer-enable")',
        'proxy = regulator_get(&pdev->dev, "proxy");',
        "rc = regulator_enable(proxy);",
        "rc = regulator_disable(proxy);",
        "late_initcall_sync(a52_p374_mdss_proxy_release);",
        "if (gdsc->profile == A52_GDSC_PROFILE_MDSS)",
        "a52_p374_mdss_proxy_acquire(pdev, gdsc);",
        'a52_ackfr_record("P374 PX+ en=0 reg=%x", reg);',
        'a52_ackfr_record("P374 PX- rc=%d before=%x after=%x",',
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase374 patched source missing: " + token)

    if text.count("a52_p374_mdss_proxy_acquire(pdev, gdsc);") != 1:
        raise SystemExit("Phase374 proxy acquire call count mismatch")
    if text.count("late_initcall_sync(a52_p374_mdss_proxy_release);") != 1:
        raise SystemExit("Phase374 proxy release initcall count mismatch")


def validate_dt(root: Path) -> None:
    matches = []
    dts_root = root / "arch/arm64/boot/dts"
    if dts_root.is_dir():
        for path in dts_root.rglob("*"):
            if path.suffix not in {".dts", ".dtsi"} or not path.is_file():
                continue
            data = path.read_text(errors="replace")
            if 'regulator-name = "mdss_core_gdsc"' in data:
                matches.append((path, data))

    if not matches:
        # The final merged DT can come from a staged/generated vendor tree. The
        # exact runtime DT property is separately hardware-proven, so absence of
        # a source match is informational rather than a build blocker.
        print("Phase374 DT source note: mdss_core_gdsc source node not found in tree")
        return

    good = [
        path for path, data in matches
        if "qcom,proxy-consumer-enable" in data and "proxy-supply" in data
    ]
    if not good:
        raise SystemExit("Phase374 mdss_core_gdsc source lacks proxy-consumer contract")
    print("Phase374 DT proxy source:", ", ".join(str(p) for p in good))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--touchgrass", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    root = args.root.resolve()
    tg = args.touchgrass.resolve()
    target = root / TARGET
    if not target.is_file():
        raise SystemExit("Phase374 generated legacy GDSC source missing")

    validate_touchgrass(tg)

    text = target.read_text(errors="replace")
    if args.check_only:
        validate(text)
        validate_dt(root)
        print("Phase374 MDSS GDSC proxy parity audit: PASS")
        return 0

    patched = patch(text)
    validate(patched)
    target.write_text(patched)
    validate_dt(root)
    print("Phase374 MDSS GDSC proxy parity applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
