#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path("workspace/touchgrass-a52xq")
OUT = Path("artifacts/p0-boot-probe/source-patches")
OUT.mkdir(parents=True, exist_ok=True)

CALLS = {
    ROOT / "kernel/power/main.c": "msm_drm_register_notifier_client",
    ROOT / "fs/pstore/ss_platform_log.c": "sec_boot_stat_add",
    ROOT / "drivers/mfd/sec_ap_pmic.c": "do_keyboard_notifier",
}

for path, symbol in CALLS.items():
    if not path.is_file():
        raise SystemExit(f"missing source file: {path}")

    text = path.read_text()
    pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(symbol)}\s*\([^;\n]*\);[ \t]*$"
    )
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise SystemExit(
            f"{path}: expected exactly one standalone {symbol} call, found {len(matches)}"
        )

    safe_name = str(path.relative_to(ROOT)).replace("/", "__")
    (OUT / f"{safe_name}.before").write_text(text)

    replacement = f"\t/* P0 omits optional callback: {symbol} */"
    patched = pattern.sub(replacement, text)
    path.write_text(patched)
    (OUT / f"{safe_name}.p0").write_text(patched)

    if symbol in path.read_text():
        raise SystemExit(f"{path}: {symbol} still present after patch")

(OUT / "README.txt").write_text(
    "P0-only removal of three standalone optional callbacks whose providers are "
    "intentionally absent from the reduced early-boot build.\n"
)
