#!/usr/bin/env python3
import base64
import hashlib
import zlib
from pathlib import Path

PAYLOAD = Path(__file__).with_suffix(Path(__file__).suffix + ".z64")
ENC_SHA256 = "b39cba6d293e2c48e6049c04adbf8ffc4074e3fcdf06114138b52fe6ea876dd0"
RAW_SHA256 = "47de3b5302bec9f253bbd4c8fdd2e4462a9dcabc4d98dfee0cc23754f8ec377b"

encoded = PAYLOAD.read_text(encoding="ascii").strip()
if hashlib.sha256(encoded.encode("ascii")).hexdigest() != ENC_SHA256:
    raise SystemExit("Phase432-CTRL decoder payload encoded sha256 mismatch")
raw = zlib.decompress(base64.b64decode(encoded, validate=True))
if hashlib.sha256(raw).hexdigest() != RAW_SHA256:
    raise SystemExit("Phase432-CTRL decoder payload raw sha256 mismatch")
exec(compile(raw, "scripts/decode_phase432_ctrl_payload.py", "exec"),
     {"__name__": "__main__"})
