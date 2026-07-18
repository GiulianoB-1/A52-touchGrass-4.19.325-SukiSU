#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

BASELINE_RAW_SHA256 = "d2c21f394ec477a975ce96f59959fa265acde60a4a28ef4d200c9912dfb624d1"
BASELINE_NON_SUSFS_CANONICAL_SHA256 = "494a12a758bec9a7500b3370c4059c989254a468aaf895d43a8e6ea9a0441b92"
BASELINE_NON_SUSFS_SYMBOL_COUNT = 5427


def parse_config(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
            continue
        match = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        if match:
            result[match.group(1)] = "n"
    return result


def require(config: dict[str, str], key: str, expected: str) -> None:
    actual = config.get(key)
    if actual != expected:
        raise SystemExit(f"{key}: expected {expected!r}, found {actual!r}")


def canonical_non_susfs(config: dict[str, str]) -> tuple[str, int]:
    filtered = {
        key: value
        for key, value in config.items()
        if not key.startswith("CONFIG_KSU_SUSFS")
    }
    canonical = "".join(f"{key}={filtered[key]}\n" for key in sorted(filtered))
    return hashlib.sha256(canonical.encode()).hexdigest(), len(filtered)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: 56_compare_config_to_working_baseline.py FINAL REPORT"
        )

    final_path = Path(sys.argv[1])
    report_path = Path(sys.argv[2])
    final = parse_config(final_path.read_text())

    require(final, "CONFIG_KSU", "y")
    require(final, "CONFIG_KSU_MANUAL_HOOK", "y")
    require(final, "CONFIG_KSU_MANUAL_HOOK_AUTO_SETUID_HOOK", "y")
    require(final, "CONFIG_KSU_MANUAL_HOOK_AUTO_INITRC_HOOK", "y")
    require(final, "CONFIG_KSU_MANUAL_HOOK_AUTO_INPUT_HOOK", "y")
    require(final, "CONFIG_KSU_TRACEPOINT_HOOK", "n")
    require(final, "CONFIG_KPROBES", "n")
    require(final, "CONFIG_KSU_SUSFS", "y")

    feature_symbols = (
        "CONFIG_KSU_SUSFS_SUS_PATH",
        "CONFIG_KSU_SUSFS_SUS_MOUNT",
        "CONFIG_KSU_SUSFS_SUS_KSTAT",
        "CONFIG_KSU_SUSFS_SPOOF_UNAME",
        "CONFIG_KSU_SUSFS_ENABLE_LOG",
        "CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS",
        "CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG",
        "CONFIG_KSU_SUSFS_OPEN_REDIRECT",
        "CONFIG_KSU_SUSFS_SUS_MAP",
    )
    for key in feature_symbols:
        require(final, key, "n")

    # No future or branch-specific SUSFS feature may silently resolve enabled.
    for key, value in sorted(final.items()):
        if key.startswith("CONFIG_KSU_SUSFS_") and value != "n":
            raise SystemExit(f"unexpected enabled SUSFS feature: {key}={value}")

    final_non_susfs_hash, final_non_susfs_count = canonical_non_susfs(final)
    baseline_match = (
        final_non_susfs_hash == BASELINE_NON_SUSFS_CANONICAL_SHA256
        and final_non_susfs_count == BASELINE_NON_SUSFS_SYMBOL_COUNT
    )

    susfs_symbols = sorted(
        (key, value)
        for key, value in final.items()
        if key.startswith("CONFIG_KSU_SUSFS")
    )
    lines = [
        "baseline_status=hardware-booted",
        f"baseline_raw_sha256={BASELINE_RAW_SHA256}",
        f"baseline_non_susfs_canonical_sha256={BASELINE_NON_SUSFS_CANONICAL_SHA256}",
        f"final_non_susfs_canonical_sha256={final_non_susfs_hash}",
        f"baseline_non_susfs_symbol_count={BASELINE_NON_SUSFS_SYMBOL_COUNT}",
        f"final_non_susfs_symbol_count={final_non_susfs_count}",
        f"non_susfs_config_match={'yes' if baseline_match else 'no'}",
        f"final_config_sha256={hashlib.sha256(final_path.read_bytes()).hexdigest()}",
        "hook_mode=manual-auto",
        "susfs_profile=core-only-all-features-off",
        "",
        "final_susfs_symbols:",
    ]
    lines.extend(f"{key}={value}" for key, value in susfs_symbols)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n")

    if not baseline_match:
        raise SystemExit(
            "final non-SUSFS configuration does not exactly match the "
            "hardware-booted 4.19.200 baseline"
        )


if __name__ == "__main__":
    main()
