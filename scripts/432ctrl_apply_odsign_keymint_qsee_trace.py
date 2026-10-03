#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

PAYLOAD = Path(__file__).with_suffix(Path(__file__).suffix + ".z64")
ENC_SHA256 = "3aecbc61e038abdc7d32f671fd18dc23aa8dd346c8c8dddad985ebfd7bb716c9"
RAW_SHA256 = "961f27d24a08aeeaae8ce71d92e3a190031584ed6d61be97d8fbd22d0441844b"

encoded = PAYLOAD.read_text(encoding="ascii").strip()
if hashlib.sha256(encoded.encode("ascii")).hexdigest() != ENC_SHA256:
    raise SystemExit("Phase432-CTRL patch payload encoded sha256 mismatch")
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != RAW_SHA256:
    raise SystemExit("Phase432-CTRL patch payload raw sha256 mismatch")
exec(compile(raw, "scripts/432ctrl_apply_odsign_keymint_qsee_trace_payload.py", "exec"),
     {"__name__": "__main__"})
