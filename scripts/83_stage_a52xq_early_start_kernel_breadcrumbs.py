#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


# Immutable source of the already-audited Workflow 83 staging implementation.
# This wrapper applies that implementation, then fixes the declaration ordering
# found by the first full compiler run.
ORIGINAL_URL = (
    "https://raw.githubusercontent.com/"
    "GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/"
    "5396ec39b1b4ff559e2d3996b17457debd4273e3/"
    "scripts/83_stage_a52xq_early_start_kernel_breadcrumbs.py"
)

DECLARATIONS = (
    "#if IS_BUILTIN(CONFIG_PSTORE_RAM)\n"
    "extern int __init a52_persistent_diag_init(void);\n"
    "extern void a52_persistent_diag_mark(const char *fmt, ...);\n"
    "#else\n"
    "static inline int __init a52_persistent_diag_init(void) { return -ENODEV; }\n"
    "static inline void a52_persistent_diag_mark(const char *fmt, ...) { }\n"
    "#endif\n\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args, _ = parser.parse_known_args()

    with tempfile.TemporaryDirectory(prefix="a52-stage83-") as tmp:
        original = Path(tmp) / "stage83-original.py"
        with urllib.request.urlopen(ORIGINAL_URL, timeout=60) as response:
            original.write_bytes(response.read())
        subprocess.run([sys.executable, str(original), *sys.argv[1:]], check=True)

    main_path = args.gki.resolve() / "init/main.c"
    text = main_path.read_text(encoding="utf-8")

    if text.count(DECLARATIONS) != 1:
        raise SystemExit(
            "declaration-order fix: expected exactly one generated declaration block"
        )

    rest_anchor = "noinline void __ref rest_init(void)\n"
    if text.count(rest_anchor) != 1:
        raise SystemExit(
            "declaration-order fix: expected exactly one rest_init declaration"
        )

    # The first compiler run showed that rest_init() called the diagnostic helper
    # before the block generated near start_kernel(). Move the same block above
    # rest_init so rest_init(), arch_call_rest_init(), start_kernel(), and later
    # initcall code all see a proper prototype.
    text = text.replace(DECLARATIONS, "", 1)
    text = text.replace(rest_anchor, DECLARATIONS + rest_anchor, 1)

    declaration_pos = text.index(DECLARATIONS)
    first_use_pos = text.index(
        'a52_persistent_diag_mark("A52DIAG REST before rcu_scheduler_starting'
    )
    start_kernel_pos = text.index(
        "asmlinkage __visible void __init __no_sanitize_address start_kernel(void)"
    )
    if not declaration_pos < first_use_pos < start_kernel_pos:
        raise SystemExit("declaration-order fix audit failed")

    main_path.write_text(text, encoding="utf-8")

    output = args.output.resolve()
    (output / "patched-init-main.c").write_text(text, encoding="utf-8")

    report_path = output / "stage-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.setdefault("checks", {})["diagnostic_declarations_before_rest_init"] = True
    report["compiler_fix"] = (
        "moved diagnostic helper declarations before rest_init to avoid "
        "implicit-declaration and conflicting-type errors"
    )
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
