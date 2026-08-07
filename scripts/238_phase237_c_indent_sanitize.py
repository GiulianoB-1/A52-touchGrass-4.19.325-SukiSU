#!/usr/bin/env python3
"""Phase 238 post-generation guard for raw-string C helper indentation.

The broad recorder overlay deliberately uses raw Python strings so C escape
sequences remain untouched.  Raw strings also preserve indentation tokens such
as ``\\t``.  A leading literal ``\\t`` is not valid C, so normalize only those
leading indentation tokens after all Phase 238 source transforms are complete.
String-literal escapes and non-leading backslash sequences are left untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARKERS = {
    Path("drivers/base/platform.c"): "A52_PHASE238_PLATFORM_GPU_TRACE_V1",
    Path("drivers/base/dd.c"): "A52_PHASE238_DRIVER_CORE_GPU_TRACE_V1",
    Path("drivers/regulator/a52-legacy-gdsc-regulator.c"): "A52_PHASE238_GDSC_PROVIDER_TRACE_V1",
}


def candidate_roots(arguments: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        p = Path(value)
        roots.extend((p, p.parent))
    roots.extend((Path("workspace/gki-phase199-src"), Path("gki/common")))

    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate_root(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for root in candidate_roots(arguments):
        ok = True
        for rel, marker in MARKERS.items():
            path = root / rel
            if not path.is_file() or marker not in path.read_text(encoding="utf-8"):
                ok = False
                break
        if ok:
            matches.append(root)

    uniq: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            uniq.append(root)
    if len(uniq) != 1:
        rendered = ", ".join(str(p) for p in uniq) or "none"
        raise RuntimeError(
            f"expected one generated Phase 238 source root, found {len(uniq)}: {rendered}"
        )
    return uniq[0]


def normalize_leading_tab_escapes(text: str) -> tuple[str, int]:
    fixed: list[str] = []
    replacements = 0

    for line in text.splitlines(keepends=True):
        pos = 0
        while pos < len(line) and line[pos] == " ":
            pos += 1
        tabs = 0
        while line.startswith("\\t", pos):
            tabs += 1
            pos += 2
        if tabs:
            line = line[: pos - 2 * tabs] + ("\t" * tabs) + line[pos:]
            replacements += tabs
        fixed.append(line)

    return "".join(fixed), replacements


def has_leading_tab_escape(text: str) -> bool:
    for line in text.splitlines():
        stripped_spaces = line.lstrip(" ")
        if stripped_spaces.startswith("\\t"):
            return True
    return False


def main() -> int:
    root = locate_root(sys.argv[1:])
    total = 0

    for rel, marker in MARKERS.items():
        path = root / rel
        before = path.read_text(encoding="utf-8")
        if marker not in before:
            raise RuntimeError(f"{path}: missing Phase 238 marker {marker}")
        after, count = normalize_leading_tab_escapes(before)
        if has_leading_tab_escape(after):
            raise RuntimeError(f"{path}: leading literal \\t survived normalization")
        if after != before:
            path.write_text(after, encoding="utf-8")
        total += count
        print(f"Phase 238 C indentation sanitize: {path} replacements={count}", flush=True)

    if total == 0:
        print("Phase 238 C indentation sanitize: no escaped indentation found", flush=True)
    else:
        print(f"Phase 238 C indentation sanitize: PASS replacements={total}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
