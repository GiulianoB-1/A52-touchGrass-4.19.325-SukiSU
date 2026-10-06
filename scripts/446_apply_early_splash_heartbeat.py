#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from pathlib import Path

EXPECTED_SHA256 = "4065da6f430bfb8a4c364360e9f67ca0847cc95c530b01d09a09e95b3e82f8c6"
PART_DIR = Path(__file__).with_name("446_parts")
PARTS = tuple(PART_DIR / f"part{i}.inc" for i in range(5))

payload = b"".join(p.read_bytes() for p in PARTS)
actual = hashlib.sha256(payload).hexdigest()
if actual != EXPECTED_SHA256:
    raise SystemExit(f"Phase446 source fragment SHA256 mismatch: expected {EXPECTED_SHA256}, got {actual}")

source = payload.decode("utf-8")
exec(compile(source, str(Path(__file__).with_suffix(".joined.py")), "exec"), globals())
