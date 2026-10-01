#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_P156_CLANG22_INITCALL_ORDER_FIX_V1"

def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"P156 {label}: expected 1 anchor, found {n}")
    return text.replace(old, new, 1)

def patch(root: Path) -> None:
    p = root / "scripts/generate_initcall_order.pl"
    if not p.is_file():
        raise SystemExit(f"P156 missing {p}")
    s = p.read_text()

    if MARK in s:
        return

    s = one(
        s,
        'open(my $fh, "\\"$nm\\" -just-symbol-name -defined-only \\"$object\\" 2>/dev/null |")\n'
        '\t\tor die "$0: failed to execute \\"$nm\\": $!";',
        'open(my $fh, "\\"$nm\\" --just-symbol-name --defined-only \\"$object\\" |")\n'
        '\t\tor die "$0: failed to execute \\"$nm\\": $!";',
        "llvm-nm long options",
    )

    s = one(
        s,
        "\tclose($fh);\n\n\t# sort initcalls in each object file numerically by the counter value",
        "\tclose($fh) or die \"$0: llvm-nm failed for $object\\n\";\n\n"
        "\t# sort initcalls in each object file numerically by the counter value",
        "llvm-nm exit status",
    )

    s = one(
        s,
        "sub wait_for_results {\n\tmy $pid = wait();\n\tif ($pid > 0) {\n\t\tmy $fh = $jobs->{$pid};",
        "sub wait_for_results {\n\tmy $pid = wait();\n\tmy $status = $?;\n\tif ($pid > 0) {\n\t\tmy $fh = $jobs->{$pid};",
        "child status capture",
    )

    s = one(
        s,
        "\t\tclose($fh);\n\t\tdelete($jobs->{$pid});",
        "\t\tclose($fh);\n"
        "\t\tif ($status != 0) {\n"
        "\t\t\tdie \"$0: initcall scan child $pid failed with status $status\\n\";\n"
        "\t\t}\n"
        "\t\tdelete($jobs->{$pid});",
        "child status enforcement",
    )

    s = one(
        s,
        "if (!keys(%{$sections})) {\n\texit(0); # no initcalls...?\n}",
        "if (!keys(%{$sections})) {\n"
        "\tdie \"$0: no initcalls found while CONFIG_LTO_CLANG is active\\n\";\n"
        "}",
        "empty initcall fatal",
    )

    s += f"\n# {MARK}\n"
    p.write_text(s)

def validate(root: Path) -> None:
    p = root / "scripts/generate_initcall_order.pl"
    s = p.read_text()
    required = (
        MARK,
        '--just-symbol-name --defined-only',
        'llvm-nm failed for $object',
        'initcall scan child $pid failed',
        'no initcalls found while CONFIG_LTO_CLANG is active',
    )
    for token in required:
        if token not in s:
            raise SystemExit("P156 validation missing: " + token)
    if '-just-symbol-name -defined-only' in s:
        raise SystemExit("P156 legacy single-dash llvm-nm options remain")
    print("P156 Clang22 initcall-order fix: PASS")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    if not ns.check_only:
        patch(ns.root)
    validate(ns.root)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
