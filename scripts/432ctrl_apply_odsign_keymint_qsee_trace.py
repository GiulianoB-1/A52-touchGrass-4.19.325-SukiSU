#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

PAYLOAD = Path(__file__).with_suffix(Path(__file__).suffix + ".z64")
ENC_SHA256 = "4ceef0163111029feefa6b6003e6ee089fb23e99e1a25b3dcc4aff26bede1378"
RAW_SHA256 = "2600cd891e5ab1f5f0748d21f5d132b1e3cb8dfb52d0702a6e3451abf774e7bd"

encoded = PAYLOAD.read_text(encoding="ascii").strip()
if hashlib.sha256(encoded.encode("ascii")).hexdigest() != ENC_SHA256:
    raise SystemExit("Phase432-CTRL patch payload encoded sha256 mismatch")
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != RAW_SHA256:
    raise SystemExit("Phase432-CTRL patch payload raw sha256 mismatch")
exec(compile(raw, "scripts/432ctrl_apply_odsign_keymint_qsee_trace_payload.py", "exec"),
     {"__name__": "__main__"})
