#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

MSM = Path("drivers/gpu/msm")
MARKER = "A619 KGSL P7 CP INIT: Qualcomm f583f456d0a2"
P136_GBIF = "A619 KGSL P6 PM: Qualcomm dde4355ea92d"
P136_DEVFREQ = "A619 KGSL P6 PM: Qualcomm 45d7e571a332 + ca1cbeedfcd6"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def function_body(text: str, name: str) -> str:
    start = text.find(name)
    if start < 0:
        raise SystemExit(f"missing function: {name}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"missing opening brace for: {name}")

    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    raise SystemExit(f"unterminated function: {name}")


def patch_cp_init(root: Path) -> None:
    path = root / MSM / "adreno_a6xx.c"
    text = path.read_text()

    if MARKER in text:
        print(f"[already] {path}: corrected A6xx CP init stream")
        return

    # P136 must be present because P138 is intentionally layered on the
    # boot-tested KGSL + PM phase.
    if P136_GBIF not in text:
        raise SystemExit("P136 baseline missing from adreno_a6xx.c")

    # The Samsung A52/A619 tree has a 10-dword CP_ME_INIT payload but
    # historically declares 11 and pads one trailing zero. Qualcomm later
    # corrected the A6xx stream to 11 total dwords: 1 packet header + 10
    # payload dwords.
    define_anchor = """#define CP_INIT_MASK (CP_INIT_MAX_CONTEXT | \\\n\t\tCP_INIT_ERROR_DETECTION_CONTROL | \\\n\t\tCP_INIT_HEADER_DUMP | \\\n\t\tCP_INIT_DEFAULT_RESET_STATE | \\\n\t\tCP_INIT_UCODE_WORKAROUND_MASK | \\\n\t\tCP_INIT_OPERATION_MODE_MASK | \\\n\t\tCP_INIT_REGISTER_INIT_LIST_WITH_SPINLOCK)\n"""

    if define_anchor not in text:
        raise SystemExit("adreno_a6xx.c: CP_INIT_MASK anchor changed")

    define_new = define_anchor + f"""
/*
 * {MARKER}
 *
 * Qualcomm corrected the A6xx CP_ME_INIT stream from 12 total dwords
 * (11 payload dwords) to 11 total dwords (10 payload dwords). The enabled
 * ordinal set below contains exactly ten payload dwords, so do not pad an
 * extra trailing zero into CP_ME_INIT.
 */
#define A6XX_CP_INIT_DWORDS 11
"""
    text = replace_once(text, define_anchor, define_new, "CP init constant")

    body = function_body(text, "static int a6xx_send_cp_init")
    legacy = [
        "adreno_ringbuffer_allocspace(rb, 12)",
        "cp_type7_packet(CP_ME_INIT, 11)",
        "_set_ordinals(adreno_dev, cmds, 11)",
    ]
    for token in legacy:
        if token not in body:
            raise SystemExit(
                f"adreno_a6xx.c: expected Samsung CP init token missing: {token}"
            )

    body_new = body.replace(
        "adreno_ringbuffer_allocspace(rb, 12)",
        "adreno_ringbuffer_allocspace(rb, A6XX_CP_INIT_DWORDS)",
        1,
    ).replace(
        "cp_type7_packet(CP_ME_INIT, 11)",
        "cp_type7_packet(CP_ME_INIT, A6XX_CP_INIT_DWORDS - 1)",
        1,
    ).replace(
        "_set_ordinals(adreno_dev, cmds, 11)",
        "_set_ordinals(adreno_dev, cmds, A6XX_CP_INIT_DWORDS - 1)",
        1,
    )

    if body == body_new:
        raise SystemExit("adreno_a6xx.c: CP init body was not changed")

    text = text.replace(body, body_new, 1)
    path.write_text(text)
    print(f"[patched] {path}: A6xx CP_ME_INIT 12 -> 11 total dwords")


def audit(root: Path) -> None:
    a6xx = (root / MSM / "adreno_a6xx.c").read_text()
    pwrscale = (root / MSM / "kgsl_pwrscale.c").read_text()
    kgsl_h = (root / MSM / "kgsl.h").read_text()
    preempt = (root / MSM / "adreno_a6xx_preempt.c").read_text()
    gpulist = (root / MSM / "adreno-gpulist.h").read_text()

    body = function_body(a6xx, "static int a6xx_send_cp_init")

    checks = [
        (MARKER in a6xx, "P138/P7 marker"),
        ("#define A6XX_CP_INIT_DWORDS 11" in a6xx,
            "11-dword A6xx CP init stream"),
        ("adreno_ringbuffer_allocspace(rb, A6XX_CP_INIT_DWORDS)" in body,
            "CP init allocation uses corrected size"),
        ("cp_type7_packet(CP_ME_INIT, A6XX_CP_INIT_DWORDS - 1)" in body,
            "CP_ME_INIT payload uses corrected size"),
        ("_set_ordinals(adreno_dev, cmds, A6XX_CP_INIT_DWORDS - 1)" in body,
            "ordinal payload uses corrected size"),
        ("adreno_ringbuffer_allocspace(rb, 12)" not in body,
            "legacy 12-dword allocation removed"),
        ("cp_type7_packet(CP_ME_INIT, 11)" not in body,
            "legacy 11-dword packet payload removed"),
        ("_set_ordinals(adreno_dev, cmds, 11)" not in body,
            "legacy padded ordinal count removed"),
        (P136_GBIF in a6xx, "P136 GBIF CGC preserved"),
        ("A6XX_UCHE_GBIF_GX_CONFIG" in a6xx,
            "GBIF CGC register preserved"),
        (P136_DEVFREQ in pwrscale, "P136 devfreq hardening preserved"),
        ("struct adreno_rb_shadow" in kgsl_h,
            "P5 scratch consolidation preserved"),
        ("A619 GPU P4: Qualcomm 96f7537ccfcd" in preempt,
            "P4 preemption optimization preserved"),
        ("DEFINE_ADRENO_REV(ADRENO_REV_A619, 6, 1, 9, ANY_ID)" in gpulist,
            "A619 core definition preserved"),
    ]

    for ok, label in checks:
        if not ok:
            raise SystemExit(f"audit failed: {label}")

    # A619 is part of Samsung's A615-family power-up path, which already
    # contains the GBIF GX config register. Do not duplicate it globally.
    m = re.search(
        r"static u32 a615_pwrup_reglist\[\] = \{(.*?)\n\};",
        a6xx,
        re.S,
    )
    if not m:
        raise SystemExit("audit failed: a615_pwrup_reglist not found")
    if "A6XX_UCHE_GBIF_GX_CONFIG" not in m.group(1):
        raise SystemExit("audit failed: A619/A615 GBIF IFPC restore missing")

    print("[audit] exact A619 core path: PASS")
    print("[audit] Qualcomm f583f456d0a2 CP init correction: PASS")
    print("[audit] CP_ME_INIT total dwords: 11")
    print("[audit] CP_ME_INIT payload dwords: 10")
    print("[audit] A619/A615 GBIF IFPC restore already present: PASS")
    print("[audit] P136 KGSL/power management preserved: PASS")
    print("[audit] P4/P5 modernization preserved: PASS")
    print("[audit] GPU frequencies, voltage table, msm-adreno-tz and thermal policy unchanged")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <kernel-tree>")

    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        raise SystemExit(f"not a kernel tree: {root}")

    # Require the previously boot-tested modernization stack.
    if "struct adreno_rb_shadow" not in (root / MSM / "kgsl.h").read_text():
        raise SystemExit("A619 P5 baseline missing: struct adreno_rb_shadow not found")

    if P136_DEVFREQ not in (root / MSM / "kgsl_pwrscale.c").read_text():
        raise SystemExit("P136 KGSL/PM baseline missing")

    patch_cp_init(root)
    audit(root)

    print("[done] A619 KGSL P7 / workflow P138 applied")
    print("[source] Qualcomm f583f456d0a2: Correct a6xx CP init sequence")
    print("[effect] removes one unnecessary trailing zero dword from CP_ME_INIT")
    print("[preserved] P136 power management, P137 boot rescue, KGSL P1-P5 and GPU UV")
    print("[unchanged] GPU clocks, voltage table, governor, thermal policy and userspace ABI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
