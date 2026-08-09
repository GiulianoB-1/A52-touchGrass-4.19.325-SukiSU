#!/usr/bin/env python3
"""Phase 241: repair C90 declaration order and superseded Phase 240 helper use.

The first real Phase 241 kernel build proved the diagnostic transformations were
reachable, then failed under the kernel's -Werror policy because the new
create-in/dreg-in statements were inserted before pre-existing declarations.
Phase 241 also intentionally supersedes the Phase 240 heartbeat replay call,
leaving its static replay helper unused.

This post-overlay repair is compile-shape only:
- move CXF241 dreg-in after driver_register()'s declaration block;
- move CXF241 create-in after of_platform_device_create_pdata()'s declaration;
- mark the retained, superseded Phase 240 replay helper __maybe_unused.

No return value, match/probe result, device link, deferred-probe decision,
provider state, driver ordering, or recorder transport is changed.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
OF_PLATFORM = Path("drivers/of/platform.c")
DRIVER = Path("drivers/base/driver.c")

MARKER = "A52_PHASE241_COMPILE_SHAPE_REPAIR_V1"
RECORDER_MARKER = "A52_PHASE241_R240_REPLAY_MAYBE_UNUSED_V1"
OF_MARKER = "A52_PHASE241_OF_DECLARATION_ORDER_V1"
DRIVER_MARKER = "A52_PHASE241_DRIVER_DECLARATION_ORDER_V1"

DRIVER_LOG = '''\tif (a52_r241_driver_focus(drv))\n\t\ta52_ackfr_record("CXF241 dreg-in r=%.32s bus=%.16s",\n\t\t\tdrv->name, drv->bus && drv->bus->name ? drv->bus->name : "-");\n'''
OF_LOG = '''\tif (a52_r241_of_gpu_target(np))\n\t\ta52_ackfr_record("CXF241 create-in node=%.64s", np->full_name);\n'''


def mask_c(text: str) -> str:
    out = list(text)
    state = "code"
    i = 0
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*":
                out[i] = out[i + 1] = " "; state = "block"; i += 2; continue
            if c == "/" and n == "/":
                out[i] = out[i + 1] = " "; state = "line"; i += 2; continue
            if c == '"': out[i] = " "; state = "string"
            elif c == "'": out[i] = " "; state = "char"
        elif state == "block":
            if c == "*" and n == "/":
                out[i] = out[i + 1] = " "; state = "code"; i += 2; continue
            if c != "\n": out[i] = " "
        elif state == "line":
            if c == "\n": state = "code"
            else: out[i] = " "
        else:
            quote = '"' if state == "string" else "'"
            if c == "\\":
                out[i] = " "
                if i + 1 < len(text): out[i + 1] = " "
                i += 2; continue
            if c == quote: out[i] = " "; state = "code"
            elif c != "\n": out[i] = " "
        i += 1
    return "".join(out)


def find_function(text: str, pattern: str, label: str) -> tuple[int, int, int]:
    match = re.search(pattern, text, re.M)
    if not match:
        raise RuntimeError(f"{label}: function signature not found")
    brace = text.find("{", match.start(), match.end() + 8)
    if brace < 0:
        raise RuntimeError(f"{label}: opening brace missing")
    masked = mask_c(text)
    depth = 0
    for pos in range(brace, len(masked)):
        if masked[pos] == "{":
            depth += 1
        elif masked[pos] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), brace, pos + 1
    raise RuntimeError(f"{label}: unterminated function")


def relocate_log_after_anchor(
    text: str,
    pattern: str,
    log_block: str,
    declaration_anchors: tuple[str, ...],
    marker: str,
    label: str,
) -> str:
    start, _brace, end = find_function(text, pattern, label)
    fn = text[start:end]
    if fn.count(log_block) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one Phase 241 log block, found {fn.count(log_block)}"
        )

    if marker not in fn:
        fn_without = fn.replace(log_block, "", 1)
        anchor = next((candidate for candidate in declaration_anchors if candidate in fn_without), None)
        if anchor is None:
            raise RuntimeError(
                f"{label}: expected declaration anchor missing: {declaration_anchors!r}"
            )
        if fn_without.count(anchor) != 1:
            raise RuntimeError(
                f"{label}: declaration anchor count is {fn_without.count(anchor)}, expected 1"
            )
        insertion = anchor + f"\n\t/* {marker} */\n" + log_block
        fn = fn_without.replace(anchor, insertion, 1)
        text = text[:start] + fn + text[end:]

    validate_order(text, pattern, log_block, declaration_anchors, marker, label)
    return text


def validate_order(
    text: str,
    pattern: str,
    log_block: str,
    declaration_anchors: tuple[str, ...],
    marker: str,
    label: str,
) -> None:
    start, _brace, end = find_function(text, pattern, label)
    fn = text[start:end]
    if marker not in fn:
        raise RuntimeError(f"{label}: compile-shape marker missing")
    if fn.count(log_block) != 1:
        raise RuntimeError(f"{label}: Phase 241 log block count is {fn.count(log_block)}, expected 1")
    anchor = next((candidate for candidate in declaration_anchors if candidate in fn), None)
    if anchor is None:
        raise RuntimeError(f"{label}: declaration anchor missing after repair")
    if fn.find(log_block) <= fn.find(anchor):
        raise RuntimeError(f"{label}: Phase 241 log still precedes declaration block")


def patch_driver(text: str, label: str) -> str:
    pattern = r"int\s+driver_register\s*\(\s*struct\s+device_driver\s*\*\s*drv\s*\)\s*\{"
    return relocate_log_after_anchor(
        text,
        pattern,
        DRIVER_LOG,
        ("\tint ret;\n\tstruct device_driver *other;\n", "\tint ret;\n"),
        DRIVER_MARKER,
        label,
    )


def patch_of(text: str, label: str) -> str:
    pattern = (
        r"(?:static\s+)?struct\s+platform_device\s*\*\s*"
        r"of_platform_device_create_pdata\s*\([^)]*\)\s*\{"
    )
    return relocate_log_after_anchor(
        text,
        pattern,
        OF_LOG,
        ("\tstruct platform_device *dev;\n",),
        OF_MARKER,
        label,
    )


def patch_recorder(text: str, label: str) -> str:
    old = "static void a52_r240_cxf_replay(unsigned int tick)\n{"
    new = (
        f"/* {RECORDER_MARKER}: Phase 241 supersedes the heartbeat replay call. */\n"
        "static void __maybe_unused a52_r240_cxf_replay(unsigned int tick)\n{"
    )
    if RECORDER_MARKER not in text:
        if text.count(old) != 1:
            raise RuntimeError(
                f"{label}: expected one Phase 240 replay helper definition, found {text.count(old)}"
            )
        text = text.replace(old, new, 1)
    if text.count("static void __maybe_unused a52_r240_cxf_replay(unsigned int tick)") != 1:
        raise RuntimeError(f"{label}: Phase 240 replay helper was not marked __maybe_unused")
    if "a52_r240_cxf_replay(tick);" in text:
        raise RuntimeError(f"{label}: obsolete Phase 240 replay call unexpectedly remains")
    return text


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        roots.extend((path, path.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    matches: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        paths = (root / RECORDER, root / OF_PLATFORM, root / DRIVER)
        if not all(path.is_file() for path in paths):
            continue
        recorder = paths[0].read_text(encoding="utf-8")
        if "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1" not in recorder:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            matches.append(root)
    if len(matches) != 1:
        rendered = ", ".join(str(root) for root in matches) or "none"
        raise RuntimeError(
            f"expected exactly one generated Phase 241 source root, found {len(matches)}: {rendered}"
        )
    return matches[0]


def self_test() -> None:
    driver = '''int driver_register(struct device_driver *drv)\n{\n''' + DRIVER_LOG + '''\n\tint ret;\n\tstruct device_driver *other;\n\n\tret = bus_add_driver(drv);\n\treturn ret;\n}\n'''
    of = '''static struct platform_device *of_platform_device_create_pdata(\n\tstruct device_node *np, const char *bus_id, void *data, struct device *parent)\n{\n''' + OF_LOG + '''\n\tstruct platform_device *dev;\n\n\tdev = of_device_alloc(np, bus_id, parent);\n\treturn dev;\n}\n'''
    recorder = '''/* A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1 */\nstatic void a52_r240_cxf_replay(unsigned int tick)\n{\n\t(void)tick;\n}\n'''

    driver_fixed = patch_driver(driver, "fixture/driver.c")
    of_fixed = patch_of(of, "fixture/of/platform.c")
    recorder_fixed = patch_recorder(recorder, "fixture/recorder.c")
    if patch_driver(driver_fixed, "fixture/driver-idempotent.c") != driver_fixed:
        raise AssertionError("driver compile-shape repair is not idempotent")
    if patch_of(of_fixed, "fixture/of-idempotent.c") != of_fixed:
        raise AssertionError("OF compile-shape repair is not idempotent")
    if patch_recorder(recorder_fixed, "fixture/recorder-idempotent.c") != recorder_fixed:
        raise AssertionError("recorder compile-shape repair is not idempotent")

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "gki/common"
        for rel, value in ((RECORDER, recorder), (OF_PLATFORM, of), (DRIVER, driver)):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        if locate_generated([], cwd=root.parent.parent).resolve() != root.resolve():
            raise AssertionError("compile-shape locator failed")

    print(
        "Phase 241 compile-shape repair self-test: PASS (C90 declaration order; Phase240 helper maybe-unused)",
        flush=True,
    )


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    recorder = root / RECORDER
    of_platform = root / OF_PLATFORM
    driver = root / DRIVER
    recorder.write_text(
        patch_recorder(recorder.read_text(encoding="utf-8"), str(recorder)),
        encoding="utf-8",
    )
    of_platform.write_text(
        patch_of(of_platform.read_text(encoding="utf-8"), str(of_platform)),
        encoding="utf-8",
    )
    driver.write_text(
        patch_driver(driver.read_text(encoding="utf-8"), str(driver)),
        encoding="utf-8",
    )
    print(
        "Phase 241 compile-shape repair applied: declarations precede diagnostics; superseded Phase240 replay helper retained maybe-unused",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Phase 241 compile-shape repair failed: {exc}", file=sys.stderr)
        raise
