#!/usr/bin/env python3
"""Phase427 source launcher.

The readable implementation is split into four adjacent .inc files only to keep
connector writes small and auditable.  Reconstruct it byte-for-byte, verify the
known local-preflight SHA256, then execute it as this script.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

EXPECTED_SHA256 = "20527493ccaa0616fe5911b5d6843ddde5bd6dbfcce993c94a9eb5bbf285c943"
PART_DIR = Path(__file__).with_name("427_parts")
PARTS = tuple(PART_DIR / f"part{i}.inc" for i in range(1, 5))

payload = b"".join(path.read_bytes() for path in PARTS)
actual = hashlib.sha256(payload).hexdigest()
if actual != EXPECTED_SHA256:
    raise SystemExit(
        f"Phase427 source fragment SHA256 mismatch: expected {EXPECTED_SHA256}, got {actual}"
    )

source = payload.decode("utf-8")
exec(compile(source, str(Path(__file__).with_suffix(".joined.py")), "exec"), globals())
