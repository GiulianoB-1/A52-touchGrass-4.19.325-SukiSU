#!/usr/bin/env python3
"""Phase 244: trace subsys initcall entry and legacy GPU GDSC registration.

Phase243 hardware reached the end of arch OF population but yielded no current-
generation CXF243 or legacy-GDSC registration records. Phase244 therefore adds
phase-unique, critical, triple-emitted records at the subsys initcall framework
boundary and inside a52_legacy_gdsc_init(), immediately around
platform_driver_register(). No initcall level, ordering, return code, supplier
link, probe decision, provider behavior, or recorder transport is changed.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
INIT = Path("init/main.c")
GDSC = Path("drivers/regulator/a52-legacy-gdsc-regulator.c")
MARKER = "A52_PHASE244_GDSC_SUBSYS_INITCALL_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, pattern: str, label: str) -> tuple[int, int]:
    m = re.search(pattern, text)
    if not m:
        raise RuntimeError(f"{label}: function not found")
    brace = text.find("{", m.start())
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace missing")
    depth = 0
    i = brace
    state = "code"
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == '"': state = "str"
            elif c == "'": state = "char"
            elif c == "/" and n == "/": state = "line"; i += 1
            elif c == "/" and n == "*": state = "block"; i += 1
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return m.start(), i + 1
        elif state == "str":
            if c == "\\": i += 1
            elif c == '"': state = "code"
        elif state == "char":
            if c == "\\": i += 1
            elif c == "'": state = "code"
        elif state == "line":
            if c == "\n": state = "code"
        elif state == "block":
            if c == "*" and n == "/": state = "code"; i += 1
        i += 1
    raise RuntimeError(f"{label}: closing brace missing")


REC_MARK_OLD = "\t * A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1\n"
REC_MARK_NEW = REC_MARK_OLD + f"\t * {MARKER}\n"
FILTER_OLD = 'if (strncmp(fmt, "CXF243", 6) &&\n'
FILTER_NEW = 'if (strncmp(fmt, "CXF244", 6) &&\n\t    strncmp(fmt, "CXF243", 6) &&\n'
CRIT_OLD = 'return !strncmp(message, "CXF243 ", 7) ||\n'
CRIT_NEW = 'return !strncmp(message, "CXF244 ", 7) ||\n\t       !strncmp(message, "CXF243 ", 7) ||\n'


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
    for token in (MARKER, 'strncmp(fmt, "CXF244", 6)',
                  'return !strncmp(message, "CXF244 ", 7) ||',
                  'strncmp(fmt, "CXF243", 6)',
                  '!strncmp(message, "CXF243 ", 7) ||'):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


INIT_MARK = "A52_PHASE244_SUBSYS_LEVEL_ENTRY_V1"


def patch_init(text: str, label: str) -> str:
    if INIT_MARK in text:
        validate_init(text, label)
        return text
    if '"subsys",' not in text or '"fs",' not in text:
        raise RuntimeError(f"{label}: initcall level table shape missing")
    include = "#include <linux/a52_ack_secure_flight_recorder.h>\n"
    if include not in text:
        anchor = "#include <linux/init.h>\n"
        text = one(text, anchor, anchor + include, f"{label}: recorder include")
    start, end = function_bounds(text, r"static\s+void\s+__init\s+do_initcall_level\s*\(int\s+level\)",
                                 f"{label}: do_initcall_level")
    fn = text[start:end]
    decl = "\tinitcall_entry_t *fn;\n"
    if fn.count(decl) != 1:
        raise RuntimeError(f"{label}: initcall fn declaration anchor count={fn.count(decl)}")
    fn = fn.replace(decl, decl + "\tint a52_r244_i;\n", 1)
    anchor = "\tstrcpy(initcall_command_line, saved_command_line);\n"
    if fn.count(anchor) != 1:
        raise RuntimeError(f"{label}: initcall command-line anchor count={fn.count(anchor)}")
    probe = (f"\t/* {INIT_MARK}: subsys is initcall level 4 */\n"
             "\tif (level == 4)\n"
             "\t\tfor (a52_r244_i = 0; a52_r244_i < 3; a52_r244_i++)\n"
             "\t\t\ta52_ackfr_record(\"CXF244 V q=%d l=%d\", a52_r244_i, level);\n\n")
    fn = fn.replace(anchor, probe + anchor, 1)
    text = text[:start] + fn + text[end:]
    validate_init(text, label)
    return text


def validate_init(text: str, label: str) -> None:
    for token in (INIT_MARK, '#include <linux/a52_ack_secure_flight_recorder.h>',
                  'int a52_r244_i;', 'if (level == 4)',
                  'CXF244 V q=%d l=%d', 'a52_r244_i < 3',
                  'initcall_levels[level]', '"subsys",'):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


GDSC_MARK = "A52_PHASE244_GDSC_INIT_REGISTER_V1"


def patch_gdsc(text: str, label: str) -> str:
    if GDSC_MARK in text:
        validate_gdsc(text, label)
        return text
    start, end = function_bounds(text, r"static\s+int\s+__init\s+a52_legacy_gdsc_init\s*\(void\)",
                                 f"{label}: a52_legacy_gdsc_init")
    fn = text[start:end]
    decl = "\tint rc;\n"
    if fn.count(decl) != 1:
        raise RuntimeError(f"{label}: rc declaration anchor count={fn.count(decl)}")
    fn = fn.replace(decl, decl + "\tint a52_r244_i;\n", 1)
    enter = '\ta52_ackfr_record("A52GDSC driver-register enter");\n'
    if fn.count(enter) != 1:
        raise RuntimeError(f"{label}: driver-register enter anchor count={fn.count(enter)}")
    before = (f"\t/* {GDSC_MARK} */\n"
              "\tfor (a52_r244_i = 0; a52_r244_i < 3; a52_r244_i++)\n"
              "\t\ta52_ackfr_record(\"CXF244 I q=%d s=E\", a52_r244_i);\n\n")
    fn = fn.replace(enter, before + enter, 1)
    call = "\trc = platform_driver_register(&a52_legacy_gdsc_driver);\n"
    if fn.count(call) != 1:
        raise RuntimeError(f"{label}: platform_driver_register anchor count={fn.count(call)}")
    around = ("\tfor (a52_r244_i = 0; a52_r244_i < 3; a52_r244_i++)\n"
              "\t\ta52_ackfr_record(\"CXF244 I q=%d s=B\", a52_r244_i);\n"
              + call +
              "\tfor (a52_r244_i = 0; a52_r244_i < 3; a52_r244_i++)\n"
              "\t\ta52_ackfr_record(\"CXF244 I q=%d s=X rc=%d\", a52_r244_i, rc);\n")
    fn = fn.replace(call, around, 1)
    text = text[:start] + fn + text[end:]
    validate_gdsc(text, label)
    return text


def validate_gdsc(text: str, label: str) -> None:
    for token in (GDSC_MARK, 'CXF244 I q=%d s=E', 'CXF244 I q=%d s=B',
                  'CXF244 I q=%d s=X rc=%d', 'a52_r244_i < 3',
                  'platform_driver_register(&a52_legacy_gdsc_driver)',
                  'A52GDSC driver-register enter', 'A52GDSC driver-register exit rc=%d'):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


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
        paths = [root / p for p in (RECORDER, INIT, GDSC)]
        if not all(p.is_file() for p in paths):
            continue
        rec = paths[0].read_text(encoding="utf-8")
        if "A52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1" not in rec:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError("expected one generated Phase243 root, found " +
                           (", ".join(map(str, hits)) or "none"))
    return hits[0]


def self_test() -> None:
    rec = ("/*\n" + REC_MARK_OLD + " */\nA52_PHASE243_CXGX_LIVE_SUPPLIER_IDENTITY_V1\n" +
           FILTER_OLD + CRIT_OLD)
    rec2 = patch_recorder(rec, "fixture/rec")
    assert patch_recorder(rec2, "fixture/rec2") == rec2

    init = ('#include <linux/init.h>\n'
            'static char *initcall_level_names[] __initdata = {\n'
            '\t"pure", "core", "postcore", "arch", "subsys", "fs", "device", "late",\n};\n'
            'static void __init do_initcall_level(int level)\n{\n'
            '\tinitcall_entry_t *fn;\n\n'
            '\tstrcpy(initcall_command_line, saved_command_line);\n'
            '\tfor (fn = initcall_levels[level]; fn < initcall_levels[level+1]; fn++)\n'
            '\t\tdo_one_initcall(initcall_from_entry(fn));\n}\n')
    init2 = patch_init(init, "fixture/init")
    assert patch_init(init2, "fixture/init2") == init2

    gd = ('static int __init a52_legacy_gdsc_init(void)\n{\n'
          '\tint rc;\n\n'
          '\ta52_ackfr_record("A52GDSC driver-register enter");\n'
          '\trc = platform_driver_register(&a52_legacy_gdsc_driver);\n'
          '\ta52_ackfr_record("A52GDSC driver-register exit rc=%d", rc);\n'
          '\treturn rc;\n}\nsubsys_initcall(a52_legacy_gdsc_init);\n')
    gd2 = patch_gdsc(gd, "fixture/gdsc")
    assert patch_gdsc(gd2, "fixture/gdsc2") == gd2

    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp)
        gr = repo / "gki/common"
        for rel, data in ((RECORDER, rec), (INIT, init), (GDSC, gd)):
            p = gr / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data, encoding="utf-8")
        if locate([], cwd=repo).resolve() != gr.resolve():
            raise AssertionError("generated tree locator failed")
    print("Phase 244 GDSC subsys-initcall overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test(); return 0
    root = locate(sys.argv[1:])
    for rel, fn in ((RECORDER, patch_recorder), (INIT, patch_init), (GDSC, patch_gdsc)):
        path = root / rel
        path.write_text(fn(path.read_text(encoding="utf-8"), str(path)), encoding="utf-8")
    print("Phase 244 subsys-initcall/GDSC registration diagnostics applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
