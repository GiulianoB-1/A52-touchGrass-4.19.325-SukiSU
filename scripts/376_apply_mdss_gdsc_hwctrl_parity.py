#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

TARGET = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
TG_TARGET = Path("drivers/clk/qcom/gdsc-regulator.c")
MARK = "A52_PHASE376_MDSS_GDSC_HWCTRL_PARITY_V1"


def extract_function(text: str, start: str, end: str, label: str) -> str:
    i = text.find(start)
    if i < 0:
        raise SystemExit(f"Phase376 {label}: start anchor missing")
    j = text.find(end, i)
    if j < 0:
        raise SystemExit(f"Phase376 {label}: end anchor missing")
    return text[i:j]


def validate_touchgrass(tg: Path) -> None:
    path = tg / TG_TARGET
    if not path.is_file():
        raise SystemExit("Phase376 pinned TouchGrass GDSC source missing")

    text = path.read_text(errors="replace")
    fn = extract_function(
        text,
        "static int gdsc_disable(struct regulator_dev *rdev)",
        "static unsigned int gdsc_get_mode",
        "TouchGrass gdsc_disable",
    )
    for token in (
        "regval |= SW_COLLAPSE_MASK;",
        "poll_gdsc_status(sc, DISABLED)",
    ):
        if token not in fn:
            raise SystemExit("Phase376 TouchGrass gdsc_disable drifted: " + token)

    if "regval &= ~HW_CONTROL_MASK" in fn:
        raise SystemExit(
            "Phase376 TouchGrass unexpectedly clears HW_CONTROL in gdsc_disable"
        )


def patch(text: str) -> str:
    if MARK in text:
        return text

    fn_start = "static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)"
    fn_end = "static unsigned int a52_legacy_gdsc_get_mode"
    start = text.find(fn_start)
    if start < 0:
        raise SystemExit("Phase376 Phase346 MDSS disable start missing")
    end = text.find(fn_end, start)
    if end < 0:
        raise SystemExit("Phase376 Phase346 MDSS disable end missing")
    fn = text[start:end]

    old = """    if (val & A52_GDSC_HW_CONTROL) {
        val &= ~A52_GDSC_HW_CONTROL;
        writel_relaxed(val, gdsc->gdscr);
        mb();
        udelay(1);
    }

"""
    count = fn.count(old)
    if count != 1:
        raise SystemExit(
            "Phase376 Phase346 mdss HW_CONTROL-clear sequence: "
            f"expected exactly one match, found {count}"
        )

    new = """    /* A52_PHASE376_MDSS_GDSC_HWCTRL_PARITY_V1
     *
     * TouchGrass gdsc_disable() preserves HW_CONTROL while dropping the
     * software enable vote. RSC v3 intentionally sets FAST/HW-control mode
     * before regulator_disable(), so clearing bit 1 here breaks the handoff.
     */
"""
    fn2 = fn.replace(old, new, 1)
    return text[:start] + fn2 + text[end:]


def validate(before: str, after: str) -> None:
    if MARK not in after:
        raise SystemExit("Phase376 marker missing")

    before_fn = extract_function(
        before,
        "static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)",
        "static unsigned int a52_legacy_gdsc_get_mode",
        "before mdss disable",
    )
    after_fn = extract_function(
        after,
        "static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)",
        "static unsigned int a52_legacy_gdsc_get_mode",
        "after mdss disable",
    )

    if "val &= ~A52_GDSC_HW_CONTROL;" not in before_fn:
        raise SystemExit("Phase376 baseline no longer clears MDSS HW_CONTROL")
    if "val &= ~A52_GDSC_HW_CONTROL;" in after_fn:
        raise SystemExit("Phase376 MDSS disable still clears HW_CONTROL")

    for token in (
        "val |= A52_GDSC_SW_COLLAPSE;",
        "ret = a52_legacy_gdsc_poll(gdsc, false, &val);",
        "A52GDSC disable profile=mdss",
    ):
        if token not in after_fn:
            raise SystemExit("Phase376 inherited MDSS disable contract lost: " + token)

    # One-variable delta: remove the software takeover that clears HW_CONTROL.
    if after.count("val &= ~A52_GDSC_HW_CONTROL;") !=             before.count("val &= ~A52_GDSC_HW_CONTROL;") - 1:
        raise SystemExit("Phase376 global HW_CONTROL-clear count delta mismatch")
    if after.count("writel_relaxed(val, gdsc->gdscr);") !=             before.count("writel_relaxed(val, gdsc->gdscr);") - 1:
        raise SystemExit("Phase376 GDSCR write count delta mismatch")
    if after.count("mb();") != before.count("mb();") - 1:
        raise SystemExit("Phase376 barrier count delta mismatch")
    if after.count("udelay(1);") != before.count("udelay(1);") - 1:
        raise SystemExit("Phase376 delay count delta mismatch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--touchgrass", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()

    root = args.root.resolve()
    target = root / TARGET
    if not target.is_file():
        raise SystemExit("Phase376 generated legacy GDSC source missing")

    validate_touchgrass(args.touchgrass.resolve())
    text = target.read_text(errors="replace")

    if args.check_only:
        if MARK not in text:
            raise SystemExit("Phase376 check-only: marker missing")
        fn = extract_function(
            text,
            "static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)",
            "static unsigned int a52_legacy_gdsc_get_mode",
            "patched mdss disable",
        )
        if "val &= ~A52_GDSC_HW_CONTROL;" in fn:
            raise SystemExit(
                "Phase376 check-only: MDSS disable still clears HW_CONTROL"
            )
        print("Phase376 MDSS GDSC HW-control parity audit: PASS")
        return 0

    patched = patch(text)
    validate(text, patched)
    target.write_text(patched)
    print("Phase376 MDSS GDSC HW-control parity applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
