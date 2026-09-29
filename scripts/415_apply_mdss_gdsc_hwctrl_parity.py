#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

GDSC = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
REC = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MARK = "A52_PHASE415_MDSS_GDSC_HWCTRL_PARITY_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase415 {label}: expected exactly 1 match, found {n}")
    return text.replace(old, new, 1)


def patch_gdsc(text: str) -> str:
    if MARK in text:
        return text
    required = (
        "static int a52_legacy_gdsc_enable(struct regulator_dev *rdev)",
        "static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)",
        "static int a52_legacy_gdsc_set_mode(struct regulator_dev *rdev,",
        "A52_GDSC_HW_CONTROL",
        "A52_GDSC_SW_COLLAPSE",
        "A52_GDSC_PROFILE_MDSS",
    )
    for token in required:
        if token not in text:
            raise SystemExit("Phase415 missing GDSC prerequisite: " + token)

    enable_old = """    before = readl_relaxed(gdsc->gdscr);
    val = before;
    val &= ~(A52_GDSC_HW_CONTROL | A52_GDSC_SW_OVERRIDE |
             A52_GDSC_SW_COLLAPSE);
"""
    enable_new = """    before = readl_relaxed(gdsc->gdscr);

    /*
     * A52_PHASE415_MDSS_GDSC_HWCTRL_PARITY_V1
     *
     * TouchGrass refuses a software enable while an HW-trigger-capable GDSC
     * is already under HW_CONTROL. Do the same for MDSS only. Clearing
     * HW_CONTROL here would steal ownership from SDE RSC after its FAST-mode
     * handoff.
     */
    if (gdsc->profile == A52_GDSC_PROFILE_MDSS &&
        (before & A52_GDSC_HW_CONTROL)) {
        a52_ackfr_record(
            "P415 GDSC enable-block before=%x hw=1 collapse=%u pwr=%u",
            before, !!(before & A52_GDSC_SW_COLLAPSE),
            !!(before & A52_GDSC_PWR_ON));
        return -EBUSY;
    }

    val = before;
    val &= ~(A52_GDSC_HW_CONTROL | A52_GDSC_SW_OVERRIDE |
             A52_GDSC_SW_COLLAPSE);
"""
    text = one(text, enable_old, enable_new, "MDSS enable HW-control guard")

    disable_old = """    before = readl_relaxed(gdsc->gdscr);
    val = before;
    if (val & A52_GDSC_HW_CONTROL) {
        val &= ~A52_GDSC_HW_CONTROL;
        writel_relaxed(val, gdsc->gdscr);
        mb();
        udelay(1);
    }

    val |= A52_GDSC_SW_COLLAPSE;
"""
    disable_new = """    before = readl_relaxed(gdsc->gdscr);
    val = before;

    /*
     * A52_PHASE415_MDSS_GDSC_HWCTRL_PARITY_V1
     *
     * TouchGrass gdsc_disable() asserts SW_COLLAPSE without clearing
     * HW_CONTROL. SDE RSC deliberately calls regulator_set_mode(FAST)
     * before its first mode-2 entry, then drops its software regulator vote.
     * Preserve that HW ownership across the disable handoff.
     */
    a52_ackfr_record(
        "P415 GDSC disable-pre before=%x hw=%u collapse=%u pwr=%u",
        before, !!(before & A52_GDSC_HW_CONTROL),
        !!(before & A52_GDSC_SW_COLLAPSE),
        !!(before & A52_GDSC_PWR_ON));

    val |= A52_GDSC_SW_COLLAPSE;
"""
    text = one(text, disable_old, disable_new, "preserve MDSS HW_CONTROL on disable")

    disable_tail = """    a52_ackfr_record(
        "A52GDSC disable profile=mdss name=%s rc=%d before=0x%x after=0x%x",
        gdsc->desc.name, ret, before, val);
"""
    disable_tail_new = disable_tail + """    a52_ackfr_record(
        "P415 GDSC disable-post rc=%d after=%x hw=%u collapse=%u pwr=%u",
        ret, val, !!(val & A52_GDSC_HW_CONTROL),
        !!(val & A52_GDSC_SW_COLLAPSE),
        !!(val & A52_GDSC_PWR_ON));
"""
    text = one(text, disable_tail, disable_tail_new, "disable post-state record")

    mode_tail = """    a52_ackfr_record(
        "A52GDSC mode profile=%s name=%s mode=%u rc=%d before=0x%x after=0x%x",
        a52_legacy_gdsc_profile_name(gdsc), gdsc->desc.name,
        mode, ret, before, val);
"""
    mode_tail_new = mode_tail + """    if (gdsc->profile == A52_GDSC_PROFILE_MDSS)
        a52_ackfr_record(
            "P415 GDSC mode mode=%u rc=%d before=%x after=%x hw=%u pwr=%u",
            mode, ret, before, val, !!(val & A52_GDSC_HW_CONTROL),
            !!(val & A52_GDSC_PWR_ON));
"""
    text = one(text, mode_tail, mode_tail_new, "MDSS mode record")

    text += "\n/* " + MARK + " */\n"
    return text


def patch_recorder(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE414_SEQUENTIAL_3M_RECORDER_V1" not in text:
        raise SystemExit("Phase415 requires Phase414 recorder")

    # Admit P415 records only to the 3 MiB sequential recorder. Do not widen
    # R48/RS48; its existing three-copy transport remains unchanged.
    first = 'if (strncmp(fmt, "P414", 4) &&\n'
    if first not in text:
        raise SystemExit("Phase415 first recorder admission gate missing")
    text = text.replace(
        first,
        'if (strncmp(fmt, "P415", 4) &&\n'
        '    strncmp(fmt, "P414", 4) &&\n',
        1,
    )

    second = '\t    strncmp(fmt, "P414", 4) &&\n'
    if second not in text:
        raise SystemExit("Phase415 second recorder admission gate missing")
    text = text.replace(
        second,
        '\t    strncmp(fmt, "P415", 4) &&\n'
        '\t    strncmp(fmt, "P414", 4) &&\n',
        1,
    )

    text += "\n/* " + MARK + ": P415 GDSC events admitted to 3 MiB recorder only. */\n"
    return text


def function_slice(text: str, signature: str, next_signature: str) -> str:
    start = text.find(signature)
    end = text.find(next_signature, start + len(signature))
    if start < 0 or end < 0:
        raise SystemExit("Phase415 function boundary missing: " + signature)
    return text[start:end]


def validate(root: Path) -> None:
    gdsc = (root / GDSC).read_text(errors="replace")
    rec = (root / REC).read_text(errors="replace")

    for token in (
        MARK,
        "P415 GDSC enable-block before=%x hw=1 collapse=%u pwr=%u",
        "P415 GDSC disable-pre before=%x hw=%u collapse=%u pwr=%u",
        "P415 GDSC disable-post rc=%d after=%x hw=%u collapse=%u pwr=%u",
        "P415 GDSC mode mode=%u rc=%d before=%x after=%x hw=%u pwr=%u",
        "gdsc->profile == A52_GDSC_PROFILE_MDSS &&",
        "return -EBUSY;",
    ):
        if token not in gdsc:
            raise SystemExit("Phase415 GDSC validation missing: " + token)

    disable = function_slice(
        gdsc,
        "static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)",
        "static unsigned int a52_legacy_gdsc_get_mode(struct regulator_dev *rdev)",
    )
    if "val &= ~A52_GDSC_HW_CONTROL" in disable:
        raise SystemExit("Phase415 MDSS disable still clears HW_CONTROL")
    if "val |= A52_GDSC_SW_COLLAPSE;" not in disable:
        raise SystemExit("Phase415 MDSS disable lost SW_COLLAPSE")
    if disable.index("P415 GDSC disable-pre") > disable.index("val |= A52_GDSC_SW_COLLAPSE;"):
        raise SystemExit("Phase415 pre-state record moved after collapse")

    # This phase deliberately does NOT add the downstream proxy consumer.
    # Keeping that gap unchanged makes this a one-variable HW-control A/B.
    if "REGULATOR_PROXY_CONSUMER" in gdsc or "proxy_consumer" in gdsc:
        raise SystemExit("Phase415 unexpectedly added proxy-consumer behavior")

    if 'strncmp(fmt, "P415", 4)' not in rec:
        raise SystemExit("Phase415 recorder admission missing")
    if 'return !strncmp(message, "P415 ", 5)' in rec:
        raise SystemExit("Phase415 must not alter R48/RS48 admission")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    for rel in (GDSC, REC):
        if not (ns.root / rel).is_file():
            raise SystemExit("Phase415 source missing: " + str(rel))

    if not ns.check_only:
        p = ns.root / GDSC
        p.write_text(patch_gdsc(p.read_text(errors="replace")))
        p = ns.root / REC
        p.write_text(patch_recorder(p.read_text(errors="replace")))

    validate(ns.root)
    print("Phase415 MDSS GDSC HW-control parity: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
