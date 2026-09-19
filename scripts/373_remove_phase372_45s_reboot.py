#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

SYS = Path("arch/arm64/kernel/syscall.c")
MARK = "A52_PHASE373_REMOVE_45S_REBOOT_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase373 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    if MARK in text:
        return text
    if "A52_PHASE372_APEXD_SURVIVOR_FORENSICS_V1" not in text:
        raise SystemExit("Phase373 requires Phase372 lineage")

    old = '''static int a52_r372_thread(void *unused)
{
\tmsleep(30000);
\ta52_r372_snapshot();
\tmsleep(15000);
\twmb();
\t__flush_dcache_area(a52_r372_sideband, A52_R372_SIDEBAND_BYTES);
\tkernel_restart("recovery");
\treturn 0;
}
'''
    new = '''/* A52_PHASE373_REMOVE_45S_REBOOT_V1
 *
 * Phase372 hardware showed odsign/odrefresh still actively progressing at
 * ~44.9 s. Do not terminate that real Android work. Keep the 30 s forensic
 * snapshot but return from the diagnostic thread afterward. Phase339's
 * ~330 s safety recovery reboot remains intact.
 */
static const char a52_r373_marker[] __used =
\t"A52_PHASE373_REMOVE_45S_REBOOT_V1";

static int a52_r372_thread(void *unused)
{
\tmsleep(30000);
\ta52_r372_snapshot();
\twmb();
\t__flush_dcache_area(a52_r372_sideband, A52_R372_SIDEBAND_BYTES);
\treturn 0;
}
'''
    return one(text, old, new, "remove Phase372 45s reboot")


def validate(text: str) -> None:
    for token in (
        "A52_PHASE372_APEXD_SURVIVOR_FORENSICS_V1",
        MARK,
        "a52_r372_snapshot();",
        "late_initcall(a52_r372_init);",
    ):
        if token not in text:
            raise SystemExit("Phase373 required token missing: " + token)
    if 'kernel_restart("recovery");' in text[text.find("static int a52_r372_thread"):text.find("static int __init a52_r372_init")]:
        raise SystemExit("Phase373 failed to remove Phase372 45s recovery reboot")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = ns.root / SYS
    if not path.is_file():
        raise SystemExit("Phase373 syscall source missing")
    before = path.read_text(encoding="utf-8")

    if MARK in before:
        validate(before)
        print("Phase373 remove-45s-reboot audit: PASS")
        return 0
    if ns.check_only:
        raise SystemExit("Phase373 marker missing in check-only mode")

    after = patch(before)
    validate(after)
    path.write_text(after, encoding="utf-8")
    print("Phase373 removed Phase372 45s recovery reboot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
