#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path("workspace/touchgrass-a52xq")
OUT = Path("artifacts/p0-boot-probe/source-patches")
OUT.mkdir(parents=True, exist_ok=True)

SYMBOLS = (
    "msm_drm_register_notifier_client",
    "sec_boot_stat_add",
    "do_keyboard_notifier",
)

resolved = []
for symbol in SYMBOLS:
    pattern = re.compile(
        rf"(?m)^[ \t]*{re.escape(symbol)}\s*\([^;\n]*\);[ \t]*$"
    )
    candidates = []

    for path in ROOT.rglob("*.c"):
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        matches = pattern.findall(text)
        if matches:
            candidates.append((path, text, len(matches)))

    total = sum(count for _, _, count in candidates)
    if total != 1 or len(candidates) != 1:
        details = ", ".join(
            f"{path.relative_to(ROOT)}:{count}" for path, _, count in candidates
        ) or "none"
        raise SystemExit(
            f"expected exactly one standalone {symbol} call in the source tree, "
            f"found {total}; candidates: {details}"
        )

    path, text, _ = candidates[0]
    safe_name = str(path.relative_to(ROOT)).replace("/", "__")
    (OUT / f"{safe_name}.before").write_text(text)

    replacement = f"\t/* P0 omits optional callback: {symbol} */ (void)0;"
    patched, count = pattern.subn(replacement, text)
    if count != 1:
        raise SystemExit(f"{path}: failed to patch {symbol}, replacements={count}")

    path.write_text(patched)
    (OUT / f"{safe_name}.p0").write_text(patched)
    resolved.append(f"{symbol}={path.relative_to(ROOT)}")

(OUT / "resolved-callbacks.txt").write_text("\n".join(resolved) + "\n")
(OUT / "README.txt").write_text(
    "P0-only replacement of three standalone optional callbacks with a valid "
    "no-op statement. Their providers are intentionally absent from the reduced "
    "early-boot build. Source paths are resolved dynamically after reconstruction "
    "and recorded separately.\n"
)
