#!/usr/bin/env python3
"""Phase 244 runtime identity for GDSC subsys-initcall diagnostics."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
OLD = "BOOT rs=ready phase=243 focus=cx-gdsc-own-suppliers roots=%u copies=3 crc=crc32c"
NEW = "BOOT rs=ready phase=244 focus=gdsc-subsys-initcall roots=%u copies=3 crc=crc32c"
MARKER = "A52_PHASE244_GDSC_SUBSYS_INITCALL_IDENTITY_V1"
IDENTITY_ANCHOR = "\t * A52_PHASE244_GDSC_SUBSYS_INITCALL_V1\n"


def patch(text: str, label: str) -> str:
    if NEW in text and MARKER in text:
        return text
    if text.count(OLD) != 1:
        raise RuntimeError(f"{label}: expected exactly one Phase243 boot identity, found {text.count(OLD)}")
    text = text.replace(OLD, NEW, 1)
    if text.count(IDENTITY_ANCHOR) != 1:
        raise RuntimeError(f"{label}: expected exactly one Phase244 diagnostic anchor, found {text.count(IDENTITY_ANCHOR)}")
    return text.replace(IDENTITY_ANCHOR, IDENTITY_ANCHOR + "\t * " + MARKER + "\n", 1)


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots=[]
    for value in args:
        if value.startswith("-"): continue
        p=Path(value); p=p if p.is_absolute() else cwd/p; roots.extend((p,p.parent))
    roots.extend((cwd/"workspace/gki-phase199-src", cwd/"gki/common"))
    out=[]; seen=set()
    for r in roots:
        k=r.resolve(strict=False)
        if k not in seen: seen.add(k); out.append(r)
    return out


def locate_generated(args: list[str], cwd: Path | None=None) -> Path:
    base=cwd or Path.cwd(); hits=[]; seen=set()
    for r in candidate_roots(args,base):
        p=r/RECORDER
        if not p.is_file(): continue
        t=p.read_text(encoding="utf-8")
        if "A52_PHASE244_GDSC_SUBSYS_INITCALL_V1" not in t: continue
        if OLD not in t and not (NEW in t and MARKER in t): continue
        k=r.resolve()
        if k not in seen: seen.add(k); hits.append(r)
    if len(hits)!=1:
        raise RuntimeError("expected one generated Phase244 recorder root, found "+(", ".join(map(str,hits)) or "none"))
    return hits[0]


def self_test() -> None:
    fixture="/*\n"+IDENTITY_ANCHOR+" */\n/* A52_PHASE244_GDSC_SUBSYS_INITCALL_V1 */\n"+OLD+"\n"
    out=patch(fixture,"fixture")
    if NEW not in out or MARKER not in out or OLD in out: raise AssertionError("identity replacement failed")
    if patch(out,"fixture/idempotent") != out: raise AssertionError("identity patch not idempotent")
    with tempfile.TemporaryDirectory() as temp:
        repo=Path(temp); gr=repo/"gki/common"; p=gr/RECORDER; p.parent.mkdir(parents=True); p.write_text(fixture,encoding="utf-8")
        if locate_generated([],cwd=repo).resolve()!=gr.resolve(): raise AssertionError("identity locator failed")
    print("Phase 244 runtime identity self-test: PASS",flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]: self_test(); return 0
    root=locate_generated(sys.argv[1:]); p=root/RECORDER
    p.write_text(patch(p.read_text(encoding="utf-8"),str(p)),encoding="utf-8")
    print("Phase 244 runtime identity applied",flush=True); return 0

if __name__=="__main__": raise SystemExit(main())
