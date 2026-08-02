#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import tarfile
from pathlib import Path

ARCHIVE_SHA256 = "88c07c1b8a1ad173782bea3abb9e89c450dcbf01135c724b2e82221f677bcb47"
CHUNKS = [
    "scripts/182_payload_chunks/00.txt",
    "scripts/182_payload_chunks/01.txt",
]
FILES = {
    "scripts/182_upgrade_a52_recorder_rs48.py": "0951c4b3b61260572afdb4e2f47442693556246e66d8aa00a818ec0aa6c767b9",
    "tools/decode-a52-recorder-v4.py": "7be5b884773bc6d718235a667d66b57d1ddeec8e22bbc5e0939c004481ea48fa",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()

    encoded = "".join((root / name).read_text().strip() for name in CHUNKS)
    encoded += "=" * (-len(encoded) % 4)
    archive = base64.b64decode(encoded, validate=True)
    actual_archive_sha = hashlib.sha256(archive).hexdigest()
    if actual_archive_sha != ARCHIVE_SHA256:
        raise SystemExit(
            f"RS48 payload archive checksum mismatch: {actual_archive_sha}"
        )

    payload = gzip.decompress(archive)
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tar:
        for member in tar.getmembers():
            if member.name not in FILES or not member.isfile():
                raise SystemExit(f"unexpected RS48 payload member: {member.name}")
            handle = tar.extractfile(member)
            if handle is None:
                raise SystemExit(f"cannot read RS48 payload member: {member.name}")
            data = handle.read()
            actual = hashlib.sha256(data).hexdigest()
            if actual != FILES[member.name]:
                raise SystemExit(f"RS48 payload checksum mismatch for {member.name}")
            path = root / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755)
            seen.add(member.name)
            print(f"extracted {member.name} sha256={actual}")

    missing = sorted(set(FILES) - seen)
    if missing:
        raise SystemExit(f"missing RS48 payload files: {missing}")

    if args.verify:
        for name, expected in FILES.items():
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            if actual != expected:
                raise SystemExit(f"RS48 verification failed for {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
