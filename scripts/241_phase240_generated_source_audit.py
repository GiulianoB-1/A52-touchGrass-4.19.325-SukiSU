#!/usr/bin/env python3
"""Phase 241 final generated-source reachability and coverage audit.

This complements the Image-string package gate: the surviving HB record,
Phase241 live proof, and replay call must be in the same generated C function.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
OF_PLATFORM = Path("drivers/of/platform.c")
DRIVER = Path("drivers/base/driver.c")
IDENTITY = "BOOT rs=ready phase=241 focus=cx-broad-corridor-latch roots=%u copies=3 crc=crc32c"
IDENTITY_MARKER = "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_IDENTITY_V1"
LATCH_MARKER = "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1"
OF_MARKER = "A52_PHASE241_OF_GPU_CREATE_TRACE_V1"
DRIVER_MARKER = "A52_PHASE241_GPU_DRIVER_REGISTER_TRACE_V1"


def mask_c(text: str) -> str:
    out = list(text); state = "code"; i = 0
    while i < len(text):
        c = text[i]; n = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if c == "/" and n == "*": out[i] = out[i + 1] = " "; state = "block"; i += 2; continue
            if c == "/" and n == "/": out[i] = out[i + 1] = " "; state = "line"; i += 2; continue
            if c == '"': out[i] = " "; state = "string"
            elif c == "'": out[i] = " "; state = "char"
        elif state == "block":
            if c == "*" and n == "/": out[i] = out[i + 1] = " "; state = "code"; i += 2; continue
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


def enclosing_top_level_block(text: str, position: int, label: str) -> tuple[int, int]:
    masked = mask_c(text); depth = 0; opening = -1
    for i, c in enumerate(masked[:position]):
        if c == "{":
            if depth == 0: opening = i
            depth += 1
        elif c == "}": depth -= 1
    if depth <= 0 or opening < 0: raise RuntimeError(f"{label}: HB marker is not inside a top-level function")
    d = 0
    for i in range(opening, len(masked)):
        if masked[i] == "{": d += 1
        elif masked[i] == "}":
            d -= 1
            if d == 0: return opening, i
    raise RuntimeError(f"{label}: unterminated live heartbeat function")


def require(text: str, tokens: tuple[str, ...], label: str) -> None:
    missing = [token for token in tokens if token not in text]
    if missing: raise RuntimeError(f"{label}: missing generated-source token(s): {missing}")


def audit_recorder(text: str, label: str) -> None:
    require(text, (
        IDENTITY, IDENTITY_MARKER, LATCH_MARKER,
        "A52_R241_POP_CAPACITY 24U", "A52_R241_DRV_CAPACITY 32U",
        "A52_R241_PRB_CAPACITY 48U", "A52_R241_SUP_CAPACITY 48U",
        'strncmp(fmt, "CXF241", 6)', "a52_r241_corridor_latch(event.message);",
        'a52_ackfr_record("CXF241 replay-begin', 'a52_r241_replay_bucket("pop"',
        'a52_r241_replay_bucket("drv"', 'a52_r241_replay_bucket("prb"',
        'a52_r241_replay_bucket("sup"', 'a52_ackfr_record("CXF241 stats',
        'a52_ackfr_record("CXF241 replay-end'), label)
    hb = '"HB tick=%u'
    if text.count(hb) != 1: raise RuntimeError(f"{label}: expected exactly one HB format, found {text.count(hb)}")
    marker = text.index(hb); opening, closing = enclosing_top_level_block(text, marker, label)
    body = text[opening + 1:closing]
    ordered = ('a52_ackfr_record("HB tick=%u', 'a52_ackfr_record("CXF241 live t=%u", tick);', 'a52_r241_corridor_replay(tick);')
    for token in ordered:
        if body.count(token) != 1: raise RuntimeError(f"{label}: live HB body expected one {token!r}, found {body.count(token)}")
    if not (body.find(ordered[0]) < body.find(ordered[1]) < body.find(ordered[2])):
        raise RuntimeError(f"{label}: live proof/replay is not downstream of surviving HB")
    if "a52_r240_cxf_replay(tick);" in body:
        raise RuntimeError(f"{label}: obsolete Phase240 replay remains in live heartbeat")


def audit_of(text: str, label: str) -> None:
    require(text, (OF_MARKER, 'CXF241 create-in', 'CXF241 create-out', "a52_r241_of_create_return(np,",
                   'strstr(name, "3d9106c")', 'strstr(name, "3d9100c")',
                   'strstr(name, "3d90000")', 'strstr(name, "3d00000")'), label)


def audit_driver(text: str, label: str) -> None:
    require(text, (DRIVER_MARKER, 'CXF241 dreg-in', 'CXF241 dreg-out', "a52_r241_driver_register_return(drv,",
                   'strstr(name, "a52-legacy-gdsc")', 'strstr(name, "kgsl")', 'strstr(name, "gpu")'), label)


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"): continue
        p = Path(value)
        if not p.is_absolute(): p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []; seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen: seen.add(key); out.append(root)
    return out


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd(); unique: list[Path] = []; seen: set[Path] = set()
    for root in candidate_roots(args, base):
        paths = (root / RECORDER, root / OF_PLATFORM, root / DRIVER)
        if not all(path.is_file() for path in paths): continue
        text = paths[0].read_text(encoding="utf-8")
        if LATCH_MARKER not in text or IDENTITY_MARKER not in text: continue
        key = root.resolve()
        if key not in seen: seen.add(key); unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(root) for root in unique) or "none"
        raise RuntimeError(f"expected exactly one final generated Phase241 source root, found {len(unique)}: {rendered}")
    return unique[0]


def audit_root(root: Path) -> None:
    audit_recorder((root / RECORDER).read_text(encoding="utf-8"), str(root / RECORDER))
    audit_of((root / OF_PLATFORM).read_text(encoding="utf-8"), str(root / OF_PLATFORM))
    audit_driver((root / DRIVER).read_text(encoding="utf-8"), str(root / DRIVER))


def self_test() -> None:
    rec = '''/* A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1 */\n/* A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_IDENTITY_V1 */\nA52_R241_POP_CAPACITY 24U\nA52_R241_DRV_CAPACITY 32U\nA52_R241_PRB_CAPACITY 48U\nA52_R241_SUP_CAPACITY 48U\nstrncmp(fmt, "CXF241", 6)\na52_r241_corridor_latch(event.message);\na52_ackfr_record("CXF241 replay-begin");\na52_r241_replay_bucket("pop");\na52_r241_replay_bucket("drv");\na52_r241_replay_bucket("prb");\na52_r241_replay_bucket("sup");\na52_ackfr_record("CXF241 stats");\na52_ackfr_record("CXF241 replay-end");\nBOOT rs=ready phase=241 focus=cx-broad-corridor-latch roots=%u copies=3 crc=crc32c\nstatic void heartbeat(void)\n{\n unsigned int tick = 155;\n a52_ackfr_record("HB tick=%u online=%u", tick, 8);\n a52_ackfr_record("CXF241 live t=%u", tick);\n a52_r241_corridor_replay(tick);\n}\n'''
    of = '''/* A52_PHASE241_OF_GPU_CREATE_TRACE_V1 */\nCXF241 create-in\nCXF241 create-out\na52_r241_of_create_return(np, p, 1);\nstrstr(name, "3d9106c"); strstr(name, "3d9100c"); strstr(name, "3d90000"); strstr(name, "3d00000");\n'''
    dr = '''/* A52_PHASE241_GPU_DRIVER_REGISTER_TRACE_V1 */\nCXF241 dreg-in\nCXF241 dreg-out\na52_r241_driver_register_return(drv, 0, 1);\nstrstr(name, "a52-legacy-gdsc"); strstr(name, "kgsl"); strstr(name, "gpu");\n'''
    audit_recorder(rec, "fixture/recorder"); audit_of(of, "fixture/of"); audit_driver(dr, "fixture/driver")
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp); root = repo / "gki/common"
        for rel, txt in ((RECORDER, rec), (OF_PLATFORM, of), (DRIVER, dr)):
            p = root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(txt, encoding="utf-8")
        if locate_generated([], cwd=repo).resolve() != root.resolve(): raise AssertionError("audit locator failed")
    print("Phase 241 final generated-source audit self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]: self_test(); return 0
    root = locate_generated(sys.argv[1:]); audit_root(root)
    print("Phase 241 generated-source audit: PASS (HB/live/replay same function; broad CX corridor present)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
