#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, gzip, hashlib, io, tarfile
from pathlib import Path
ARCHIVE_SHA256 = '62fb8b8bfb908079002a76e2e73585a447651487fde10a59504d7551dc6a383a'
CHUNKS = ['scripts/176_payload_chunks/00.txt', 'scripts/176_payload_chunks/01.txt', 'scripts/176_payload_chunks/02.txt', 'scripts/176_payload_chunks/03.txt']
FILES = {'scripts/176_apply_a52_recorder_fec.py': '3fa52c216f36d8e739aa899fac8fb818cee2c9a340696f49e970111f83123172', 'tools/decode-a52-recorder-v3.py': 'f81613cae944992ba0f38014f171bc067012b078c5860038202aa03ae8bbbfa6'}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    encoded = ''.join((root / name).read_text().strip() for name in CHUNKS)
    # The repository stores the payload in independently editable text chunks.
    # Restore canonical Base64 padding after concatenation rather than requiring
    # the final chunk to retain trailing '=' characters through every editor.
    encoded += '=' * (-len(encoded) % 4)
    archive = base64.b64decode(encoded, validate=True)
    actual_archive_sha = hashlib.sha256(archive).hexdigest()
    if actual_archive_sha != ARCHIVE_SHA256:
        print(
            'payload archive digest differs from the generation-time container: '
            f'expected={ARCHIVE_SHA256} actual={actual_archive_sha}; '
            'continuing with mandatory per-file SHA-256 verification'
        )
    payload = gzip.decompress(archive)
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as tf:
        for member in tf.getmembers():
            if member.name not in FILES or not member.isfile():
                raise SystemExit(f'unexpected payload member: {member.name}')
            handle = tf.extractfile(member)
            if handle is None:
                raise SystemExit(f'cannot read payload member: {member.name}')
            data = handle.read()
            actual = hashlib.sha256(data).hexdigest()
            if actual != FILES[member.name]:
                raise SystemExit(f'payload checksum mismatch for {member.name}')
            path = root / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755)
            seen.add(member.name)
            print(f'extracted {member.name} sha256={actual}')
    missing = sorted(set(FILES) - seen)
    if missing:
        raise SystemExit(f'missing payload members: {missing}')
    if args.verify:
        for name, expected in FILES.items():
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            if actual != expected:
                raise SystemExit(f'verification failed for {name}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
