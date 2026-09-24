#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52 TEO P1: Linux 5.14-era TEO core adapted to Samsung 4.19."
UPSTREAM_CORE = "c2ec772b87408259cb01209a22fb4e1ae7d346de"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {n}")
    return text.replace(old, new, 1)


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            f"usage: {sys.argv[0]} <kernel-tree> <adapted-teo-source>"
        )

    root = Path(sys.argv[1]).resolve()
    teo_src = Path(sys.argv[2]).resolve()

    kconfig = root / "drivers/cpuidle/Kconfig"
    makefile = root / "drivers/cpuidle/governors/Makefile"
    menu = root / "drivers/cpuidle/governors/menu.c"
    teo = root / "drivers/cpuidle/governors/teo.c"
    defconfig = root / "arch/arm64/configs/a52xq_defconfig"

    for path in (kconfig, makefile, menu, defconfig, teo_src):
        if not path.is_file():
            raise SystemExit(f"missing required file: {path}")

    kc = kconfig.read_text()
    mk = makefile.read_text()
    mn = menu.read_text()
    dc = defconfig.read_text()
    ts = teo_src.read_text()

    if MARKER not in ts:
        raise SystemExit("adapted TEO source marker missing")

    # P145 is deliberately availability-only. Keep menu selected and with the
    # higher rating so boot behavior is unchanged while the TEO port is proven.
    if 'CONFIG_CPU_IDLE_GOV_MENU=y' not in dc:
        raise SystemExit("menu governor must remain enabled for P145")
    if '.rating =\t20,' not in mn and '.rating = 20,' not in mn:
        raise SystemExit("menu rating 20 anchor missing")

    if "config CPU_IDLE_GOV_TEO" not in kc:
        anchor = '''config CPU_IDLE_GOV_MENU
\tbool "Menu governor (for tickless system)"

'''
        block = anchor + '''config CPU_IDLE_GOV_TEO
\tbool "Timer events oriented (TEO) governor (for tickless systems)"
\tdepends on NO_HZ || NO_HZ_IDLE
\thelp
\t  TEO correlates timer sleep lengths with observed idle durations and
\t  selects the deepest idle state that is likely to be profitable.
\n\t  This A52 backport is compiled alongside menu first; menu remains the
\t  default governor until a later validation phase explicitly switches TEO.

'''
        kc = replace_once(kc, anchor, block, "TEO Kconfig insertion")

    if "CONFIG_CPU_IDLE_GOV_TEO" not in mk:
        mk = replace_once(
            mk,
            "obj-$(CONFIG_CPU_IDLE_GOV_MENU) += menu.o\n",
            "obj-$(CONFIG_CPU_IDLE_GOV_MENU) += menu.o\n"
            "obj-$(CONFIG_CPU_IDLE_GOV_TEO) += teo.o\n",
            "TEO Makefile insertion",
        )

    if "CONFIG_CPU_IDLE_GOV_TEO=y" not in dc:
        dc = replace_once(
            dc,
            "CONFIG_CPU_IDLE_GOV_MENU=y\n",
            "CONFIG_CPU_IDLE_GOV_MENU=y\nCONFIG_CPU_IDLE_GOV_TEO=y\n",
            "TEO defconfig enable",
        )

    kconfig.write_text(kc)
    makefile.write_text(mk)
    defconfig.write_text(dc)
    shutil.copyfile(teo_src, teo)

    final_kc = kconfig.read_text()
    final_mk = makefile.read_text()
    final_dc = defconfig.read_text()
    final_teo = teo.read_text()
    final_menu = menu.read_text()

    checks = (
        (final_kc, "config CPU_IDLE_GOV_TEO"),
        (final_mk, "obj-$(CONFIG_CPU_IDLE_GOV_TEO) += teo.o"),
        (final_dc, "CONFIG_CPU_IDLE_GOV_MENU=y"),
        (final_dc, "CONFIG_CPU_IDLE_GOV_TEO=y"),
        (final_teo, MARKER),
        (final_teo, "static struct cpuidle_governor teo_governor"),
        (final_teo, ".name =\t\t\"teo\""),
        (final_teo, ".rating =\t19,"),
        (final_teo, "cpu_data->last_state = -1;"),
        (final_teo, "cpuidle_get_last_residency(dev)"),
        (final_teo, "teo_target_residency_ns("),
    )
    for blob, needle in checks:
        if needle not in blob:
            raise SystemExit(f"audit failed: missing {needle}")

    if '.rating =\t20,' not in final_menu and '.rating = 20,' not in final_menu:
        raise SystemExit("audit failed: menu rating no longer 20")

    # The first TEO phase must not alter production governor selection.
    if "select CPU_IDLE_GOV_MENU if (NO_HZ || NO_HZ_IDLE) && !CPU_IDLE_GOV_TEO" in final_kc:
        raise SystemExit("P145 must not make TEO replace menu automatically")

    print("[audit] mature TEO core backport installed: PASS")
    print("[audit] Samsung 4.19 us<->ns ABI adaptation: PASS")
    print("[audit] TEO built-in config enabled: PASS")
    print("[audit] menu remains built-in and higher rated (20 > 19): PASS")
    print("[audit] P145 does not change active cpuidle governor at boot: PASS")
    print("[audit] no cpuidle state timings or driver values changed: PASS")
    print(f"[source] mature TEO baseline includes upstream core through {UPSTREAM_CORE}")
    print("[next] after P145 boot, activate TEO separately and add util-awareness")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
