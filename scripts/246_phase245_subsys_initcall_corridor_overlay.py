#!/usr/bin/env python3
"""Phase 246: identify the exact subsys initcall reached by the Phase245 boot.

Phase245 hardware produced a clean contiguous R48/RS48 capture through
OFPOP exit at ~576 ms, but no later BOOT phase=subsys, CXF243, or A52GDSC
registration records.  Keep the Phase245 fw_devlink=PERMISSIVE experiment and
all Phase243 hooks unchanged.  Add only a bounded level-4 initcall corridor:
three records at subsys-level entry/exit and one critical record immediately
before each subsys initcall, including its kallsyms name via %ps.

No initcall order, return value, supplier decision, provider behavior, DT,
fw_devlink setting, or recorder transport is changed.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
INIT = Path("init/main.c")
CORE = Path("drivers/base/core.c")
MARKER = "A52_PHASE246_SUBSYS_INITCALL_CORRIDOR_V1"
INIT_MARKER = "A52_PHASE246_SUBSYS_INITCALL_TRACE_V1"
PERMISSIVE = "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, pattern: str, label: str) -> tuple[int, int]:
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError(f"{label}: function not found")
    brace = text.find("{", match.start())
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace missing")
    depth = 0
    state = "code"
    i = brace
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == '"':
                state = "str"
            elif c == "'":
                state = "char"
            elif c == "/" and n == "/":
                state = "line"; i += 1
            elif c == "/" and n == "*":
                state = "block"; i += 1
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return match.start(), i + 1
        elif state == "str":
            if c == "\\":
                i += 1
            elif c == '"':
                state = "code"
        elif state == "char":
            if c == "\\":
                i += 1
            elif c == "'":
                state = "code"
        elif state == "line":
            if c == "\n":
                state = "code"
        elif state == "block":
            if c == "*" and n == "/":
                state = "code"; i += 1
        i += 1
    raise RuntimeError(f"{label}: closing brace missing")


REC_MARK_OLD = "\t * A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1\n"
REC_MARK_NEW = REC_MARK_OLD + f"\t * {MARKER}\n"
FILTER_OLD = 'if (strncmp(fmt, "CXF243", 6) &&\n'
FILTER_NEW = 'if (strncmp(fmt, "CXF246", 6) &&\n\t    strncmp(fmt, "CXF243", 6) &&\n'
CRIT_OLD = 'return !strncmp(message, "CXF243 ", 7) ||\n'
CRIT_NEW = 'return !strncmp(message, "CXF246 ", 7) ||\n\t       !strncmp(message, "CXF243 ", 7) ||\n'


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        validate_recorder(text, label)
        return text
    if "A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1" not in text:
        raise RuntimeError(f"{label}: Phase243 identity missing")
    text = one(text, REC_MARK_OLD, REC_MARK_NEW, f"{label}: marker")
    text = one(text, FILTER_OLD, FILTER_NEW, f"{label}: format filter")
    text = one(text, CRIT_OLD, CRIT_NEW, f"{label}: critical filter")
    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        MARKER,
        'strncmp(fmt, "CXF246", 6)',
        'return !strncmp(message, "CXF246 ", 7) ||',
        'strncmp(fmt, "CXF243", 6)',
        '!strncmp(message, "CXF243 ", 7) ||',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


INIT_PATTERN = (
    r"static\s+void\s+__init\s+do_initcall_level\s*\(\s*int\s+level\s*,\s*"
    r"char\s*\*\s*command_line\s*\)"
)
LOOP_OLD = (
    "\tfor (fn = initcall_levels[level]; fn < initcall_levels[level+1]; fn++)\n"
    "\t\tdo_one_initcall(initcall_from_entry(fn));\n"
)
LOOP_NEW = f'''\t/* {INIT_MARKER}: observe only subsys (level 4); no ordering change. */
\tif (level == 4)
\t\tfor (a52_r246_q = 0; a52_r246_q < 3; a52_r246_q++)
\t\t\ta52_ackfr_record("CXF246 V q=%d l=%d", a52_r246_q, level);

\tfor (fn = initcall_levels[level]; fn < initcall_levels[level+1]; fn++) {{
\t\ta52_r246_call = initcall_from_entry(fn);
\t\tif (level == 4)
\t\t\ta52_ackfr_record("CXF246 S n=%d f=%ps", a52_r246_n,
\t\t\t\t(void *)a52_r246_call);
\t\tdo_one_initcall(a52_r246_call);
\t\ta52_r246_n++;
\t}}

\tif (level == 4)
\t\tfor (a52_r246_q = 0; a52_r246_q < 3; a52_r246_q++)
\t\t\ta52_ackfr_record("CXF246 X q=%d n=%d", a52_r246_q, a52_r246_n);
'''


def patch_init(text: str, label: str) -> str:
    if INIT_MARKER in text:
        validate_init(text, label)
        return text
    if '"subsys",' not in text or '"fs",' not in text:
        raise RuntimeError(f"{label}: initcall level table shape missing")
    include = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if include not in text:
        anchor = "#include <linux/init.h>\n"
        text = one(text, anchor, anchor + include, f"{label}: recorder include")

    start, end = function_bounds(text, INIT_PATTERN, f"{label}: do_initcall_level")
    fn = text[start:end]
    decl = "\tinitcall_entry_t *fn;\n"
    additions = (
        decl
        + "\tinitcall_t a52_r246_call;\n"
        + "\tint a52_r246_n = 0;\n"
        + "\tint a52_r246_q;\n"
    )
    fn = one(fn, decl, additions, f"{label}: declarations")
    fn = one(fn, LOOP_OLD, LOOP_NEW, f"{label}: initcall loop")
    text = text[:start] + fn + text[end:]
    validate_init(text, label)
    return text


def validate_init(text: str, label: str) -> None:
    for token in (
        INIT_MARKER,
        '#include <linux/a52_ack_secure_flight_recorder.h>',
        'static void __init do_initcall_level(int level, char *command_line)',
        'initcall_t a52_r246_call;',
        'int a52_r246_n = 0;',
        'if (level == 4)',
        'CXF246 V q=%d l=%d',
        'CXF246 S n=%d f=%ps',
        'CXF246 X q=%d n=%d',
        'do_one_initcall(a52_r246_call);',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    if LOOP_OLD in text:
        raise RuntimeError(f"{label}: uninstrumented initcall loop remains")


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        paths = [root / p for p in (RECORDER, INIT, CORE)]
        if not all(path.is_file() for path in paths):
            continue
        rec = paths[0].read_text(encoding="utf-8")
        core = paths[2].read_text(encoding="utf-8")
        if "A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1" not in rec:
            continue
        if PERMISSIVE not in core:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        rendered = ", ".join(str(path) for path in hits) or "none"
        raise RuntimeError(f"expected one generated Phase245 root, found {len(hits)}: {rendered}")
    return hits[0]


def self_test() -> None:
    rec = (
        "/*\n" + REC_MARK_OLD + " */\n"
        "A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1\n"
        + FILTER_OLD + CRIT_OLD
    )
    rec2 = patch_recorder(rec, "fixture/rec")
    assert patch_recorder(rec2, "fixture/rec2") == rec2

    init = (
        '#include <linux/init.h>\n'
        'static const char *initcall_level_names[] __initdata = {\n'
        '\t"pure",\n\t"core",\n\t"postcore",\n\t"arch",\n\t"subsys",\n\t"fs",\n\t"device",\n\t"late",\n};\n'
        'static void __init do_initcall_level(int level, char *command_line)\n'
        '{\n'
        '\tinitcall_entry_t *fn;\n\n'
        '\tparse_args(initcall_level_names[level], command_line, 0, 0, level, level, 0, 0);\n'
        '\ttrace_initcall_level(initcall_level_names[level]);\n'
        + LOOP_OLD +
        '}\n'
    )
    init2 = patch_init(init, "fixture/init")
    assert patch_init(init2, "fixture/init2") == init2

    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        root = repo / "gki/common"
        for rel, data in (
            (RECORDER, rec),
            (INIT, init),
            (CORE, PERMISSIVE + "\n"),
        ):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data, encoding="utf-8")
        if locate([], cwd=repo).resolve() != root.resolve():
            raise AssertionError("generated Phase245 tree locator failed")

    print(
        "Phase 246 subsys-initcall corridor self-test: PASS "
        "(correct f960 signature; Phase245 permissive retained)",
        flush=True,
    )


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    rec_path = root / RECORDER
    init_path = root / INIT
    rec_path.write_text(
        patch_recorder(rec_path.read_text(encoding="utf-8"), str(rec_path)),
        encoding="utf-8",
    )
    init_path.write_text(
        patch_init(init_path.read_text(encoding="utf-8"), str(init_path)),
        encoding="utf-8",
    )
    print(
        "Phase 246 subsys initcall corridor applied: level entry + per-initcall pre-call names",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
