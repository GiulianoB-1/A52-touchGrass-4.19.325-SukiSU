#!/usr/bin/env python3
import base64
import hashlib
import sys
import zlib
from pathlib import Path

PAYLOAD = Path(__file__).with_suffix(Path(__file__).suffix + ".z64")
ENC_SHA256 = "3aecbc61e038abdc7d32f671fd18dc23aa8dd346c8c8dddad985ebfd7bb716c9"
RAW_SHA256 = "961f27d24a08aeeaae8ce71d92e3a190031584ed6d61be97d8fbd22d0441844b"
MARK = "A52_PHASE432_CTRL_ODSIGN_KEYMINT_QSEE_V1"

def normalize_android510_anchors() -> None:
    argv = sys.argv[1:]
    if "--check-only" in argv or "--root" not in argv:
        return
    i = argv.index("--root")
    if i + 1 >= len(argv):
        return
    root = Path(argv[i + 1])

    key = root / "security/keys/key.c"
    if key.is_file():
        s = key.read_text(errors="replace")
        if MARK not in s:
            lines = s.splitlines(keepends=True)
            hits = [n for n, line in enumerate(lines) if line.strip() == "found_matching_key:"]
            if len(hits) != 1:
                raise SystemExit(f"Phase432-CTRL key label normalization expected 1 anchor, found {len(hits)}")
            nl = "\n" if lines[hits[0]].endswith("\n") else ""
            lines[hits[0]] = "found_matching_key:" + nl
            key.write_text("".join(lines))

    fs = root / "fs/verity/signature.c"
    if fs.is_file():
        s = fs.read_text(errors="replace")
        if MARK not in s:
            lines = s.splitlines(keepends=True)
            hits = []
            for n, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(".proc_handler") and "proc_dointvec_minmax" in stripped:
                    hits.append(n)
            if len(hits) != 1:
                raise SystemExit(f"Phase432-CTRL fs-verity normalization expected 1 anchor, found {len(hits)}")
            line = lines[hits[0]]
            indent = line[:len(line) - len(line.lstrip())]
            nl = "\n" if line.endswith("\n") else ""
            lines[hits[0]] = indent + ".proc_handler = proc_dointvec_minmax," + nl
            fs.write_text("".join(lines))

encoded = PAYLOAD.read_text(encoding="ascii").strip()
if hashlib.sha256(encoded.encode("ascii")).hexdigest() != ENC_SHA256:
    raise SystemExit("Phase432-CTRL patch payload encoded sha256 mismatch")
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != RAW_SHA256:
    raise SystemExit("Phase432-CTRL patch payload raw sha256 mismatch")

normalize_android510_anchors()
exec(compile(raw, "scripts/432ctrl_apply_odsign_keymint_qsee_trace_payload.py", "exec"),
     {"__name__": "__main__"})
