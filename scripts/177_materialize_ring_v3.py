#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
from pathlib import Path

PATCHER_SHA256 = "8cb439549ba18862544a10444cc196b5bd5b44852e664c8f9a75420b868d64b3"
DECODER_SHA256 = "a3f5b88230d566b2279a44cbea5b27ad5a5e59fd1fbd7cc2b6002707593b3333"


def materialize(root: Path, chunks: list[str], destination: str, expected: str) -> None:
    encoded = "".join(
        (root / "scripts/177_ring_payload" / name).read_text(encoding="ascii").strip()
        for name in chunks
    )
    payload = gzip.decompress(base64.b64decode(encoded, validate=True))
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise SystemExit(f"checksum mismatch for {destination}: {actual}")
    target = root / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    target.chmod(0o755)
    print(f"materialized {destination} sha256={actual}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize A52 recorder ring-v3 sources")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    materialize(
        root,
        ["patcher-00.txt", "patcher-01.txt"],
        "scripts/176_apply_a52_recorder_fec.py",
        PATCHER_SHA256,
    )
    materialize(
        root,
        ["decoder-00.txt", "decoder-01.txt", "decoder-02.txt"],
        "tools/decode-a52-recorder-v3.py",
        DECODER_SHA256,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
