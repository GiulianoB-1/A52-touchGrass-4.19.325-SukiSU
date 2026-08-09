#!/usr/bin/env python3
"""Audit final generated Phase 242 recorder source before compilation."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
BOOT = "BOOT rs=ready phase=242 focus=cx-sticky-state roots=%u copies=3 crc=crc32c"


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


def locate_generated(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd if cwd is not None else Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        path = root / RECORDER
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "A52_PHASE242_CX_STICKY_STATE_IDENTITY_V1" not in text:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        rendered = ", ".join(str(x) for x in hits) or "none"
        raise RuntimeError(f"expected one generated Phase242 source root, found {rendered}")
    return hits[0]


def audit_text(text: str, label: str) -> None:
    required = (
        "A52_PHASE239_GPU_CX_VDD_PARENT_IDENTITY_V1",
        "A52_PHASE240_CX_FROZEN_LATCH_V1",
        "A52_PHASE241_CX_BROAD_CORRIDOR_LATCH_V1",
        "A52_PHASE242_CX_STICKY_STATE_V1",
        "A52_PHASE242_CX_STICKY_STATE_IDENTITY_V1",
        "A52_PHASE242_PHASE241_REPLAY_DISABLED_V1",
        BOOT,
        'strncmp(fmt, "CXF242", 6)',
        'return !strncmp(message, "CXF242 ", 7) ||',
        'a52_r242_sticky_latch(event.message);',
        'a52_r242_snapshot(tick);',
        'CXF242 A t=%u c=%d g=%d dr=%d/%d dw=%d/%d dm=%d/%d',
        'CXF242 B t=%u sp=%d/%d pr=%d/%d gd=%d/%d',
        'CXF242 U t=%u %.68s',
        'CXF240 drvwalk-in ',
        'CXF240 drv-match ',
        'CXF240 drv-probe ',
        'CXF240 sup-out ',
        'A52GDSC CX_VDD_PARENT_GET_V1 ',
        '__maybe_unused a52_r241_corridor_replay',
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")

    fn = text.find("static void a52_r179_heartbeat_fn")
    end = text.find("static int __init a52_r179_early_heartbeat", fn)
    if fn < 0 or end < 0:
        raise RuntimeError(f"{label}: heartbeat bounds missing")
    body = text[fn:end]
    snap = body.find("a52_r242_snapshot(tick);")
    hb = body.find('a52_ackfr_record("HB tick=%u')
    if snap < 0 or hb < 0 or snap > hb:
        raise RuntimeError(f"{label}: sticky snapshot must execute before HB")
    if "a52_r241_corridor_replay(tick);" in body:
        raise RuntimeError(f"{label}: Phase241 bulk replay remains callable from heartbeat")
    if 'a52_ackfr_record("CXF241 live t=%u", tick);' in body:
        raise RuntimeError(f"{label}: stale Phase241 live/replay heartbeat block remains")

    record = text.find("void a52_ackfr_record(const char *fmt, ...)")
    latch = text.find("a52_r242_sticky_latch(event.message);", record)
    critical = text.find("critical = a52_r179_is_critical_message(event.message);", record)
    capacity_return = text.find("if (!buffered && !critical)", record)
    if min(record, latch, critical, capacity_return) < 0 or not (record < latch < critical < capacity_return):
        raise RuntimeError(f"{label}: sticky latch is not before post-capacity rejection")


def self_test() -> None:
    source = Path("/mnt/data/phase241-run7/stage/after/a52_ack_secure_flight_recorder.c")
    overlay_path = Path("/mnt/data/phase242/242_phase241_cx_sticky_state_overlay.py")
    identity_path = Path("/mnt/data/phase242/242_phase241_identity_overlay.py")
    if source.is_file() and overlay_path.is_file() and identity_path.is_file():
        import importlib.util
        def load(path: Path, name: str):
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        ov = load(overlay_path, "p242_overlay")
        ident = load(identity_path, "p242_identity")
        text = ov.patch_recorder(source.read_text(encoding="utf-8"), "fixture")
        text = ident.patch(text, "fixture/identity")
        audit_text(text, "fixture/final")
    print("Phase 242 generated-source audit self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate_generated(sys.argv[1:])
    path = root / RECORDER
    audit_text(path.read_text(encoding="utf-8"), str(path))
    print("Phase 242 generated-source audit: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
