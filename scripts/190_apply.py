#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_lagoon(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    old_reserved = '''static const int lagoon_reserved_gpios[] = {
#if defined(CONFIG_FINGERPRINT_SECURE) && !defined(CONFIG_SEC_FACTORY)
\t13, 14, 15, 16,
#endif
#if defined(CONFIG_MST_LDO)
        86, 87,
#endif
\t-1
};
'''
    new_reserved = '''static const int lagoon_reserved_gpios[] = {
\t/*
\t * The A52 production firmware owns the secure-fingerprint TLMM lines.
\t * Phase 189 stopped synchronously on the first registration-time read of
\t * GPIO 13. Keep the vendor reservation even though the downstream Samsung
\t * fingerprint Kconfig symbols are not available in this GKI source tree.
\t */
\t13, 14, 15, 16,
#if defined(CONFIG_MST_LDO)
        86, 87,
#endif
\t-1
};
'''
    text = one(
        text,
        old_reserved,
        new_reserved,
        "reserve secure fingerprint GPIOs independently of Samsung Kconfig",
    )

    old_probe = '''static int lagoon_pinctrl_probe(struct platform_device *pdev)
{
\tint rc;

\ta52_ackfr_record("PINCTRL Lagoon probe enter dev=%s node=%s",
'''
    new_probe = '''static int lagoon_pinctrl_probe(struct platform_device *pdev)
{
\tint rc;

\ta52_ackfr_record("PINCTRL Lagoon reserved secure=13-16");
\ta52_ackfr_record("PINCTRL Lagoon probe enter dev=%s node=%s",
'''
    text = one(text, old_probe, new_probe, "add phase190 reservation marker")

    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    patch_lagoon(args.root / "drivers/pinctrl/qcom/pinctrl-lagoon.c")
    print("phase190 Lagoon secure-fingerprint GPIO reservation applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
