#!/usr/bin/env python3
from __future__ import annotations

import base64
import re
import sys
import zlib
from pathlib import Path


def _arg_value(name: str) -> str | None:
    prefix = name + "="
    for i, arg in enumerate(sys.argv[1:]):
        if arg == name:
            j = i + 2
            return sys.argv[j] if j < len(sys.argv) else None
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return None


def _phase446_msm_drv_path() -> Path | None:
    root = _arg_value("--root")
    if not root:
        return None
    path = Path(root) / "drivers/a52_display/msm/msm_drv.c"
    return path if path.is_file() else None


def _fix_phase446_mark_decl_order(path: Path) -> None:
    text = path.read_text()
    if "a52_p446_mark(" not in text:
        return

    decl_re = re.compile(
        r"(?m)^[ \t]*extern[ \t]+void[ \t]+a52_p446_mark"
        r"\s*\(\s*u32\s+event\s*,\s*u32\s+aux0\s*,\s*u32\s+aux1\s*\)"
        r"\s*;[ \t]*\n?"
    )
    text = decl_re.sub("", text)

    declaration = "extern void a52_p446_mark(u32 event, u32 aux0, u32 aux1);\n"
    includes = list(re.finditer(r"(?m)^#include[^\n]*\n", text))
    insert_at = includes[-1].end() if includes else 0

    prefix = "\n" if insert_at and not text[:insert_at].endswith("\n\n") else ""
    text = text[:insert_at] + prefix + declaration + text[insert_at:]
    path.write_text(text)


def _validate_phase446_mark_decl_order(path: Path) -> None:
    text = path.read_text()
    decl = re.search(
        r"(?m)^[ \t]*extern[ \t]+void[ \t]+a52_p446_mark"
        r"\s*\(\s*u32\s+event\s*,\s*u32\s+aux0\s*,\s*u32\s+aux1\s*\)"
        r"\s*;",
        text,
    )
    use = re.search(
        r"(?m)^(?![ \t]*extern\b)[^\n]*\ba52_p446_mark\s*\(",
        text,
    )
    if use and not decl:
        raise SystemExit("Phase446 msm_drv contract failed: a52_p446_mark prototype missing")
    if use and decl and decl.start() > use.start():
        raise SystemExit(
            "Phase446 msm_drv contract failed: a52_p446_mark prototype appears after first use"
        )


payload = zlib.decompress(
    base64.b64decode(
        Path(__file__).with_name(Path(__file__).name + ".z64").read_text().strip()
    )
)

_check_only = "--check-only" in sys.argv[1:]
_target = _phase446_msm_drv_path()

try:
    exec(
        compile(
            payload.decode("utf-8"),
            str(Path(__file__).with_suffix(".expanded.py")),
            "exec",
        ),
        globals(),
    )
finally:
    if _target is not None:
        if not _check_only:
            _fix_phase446_mark_decl_order(_target)
        _validate_phase446_mark_decl_order(_target)
