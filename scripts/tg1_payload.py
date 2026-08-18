#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import zlib
from pathlib import Path

# A52_TOUCHGRASS_CRITICAL_FLIGHT_RECORDER_V1 transport only.
# The payload is split so GitHub content transport cannot silently corrupt one
# giant line. Both compressed and decompressed bytes are authenticated before
# any generated source file is written.
CHUNKS = 7
COMPRESSED_SHA256 = "4c99d269cee8e35ddeb6bc97b0c92c8547017eabd7b978102d76c2e25fc67504"
JSON_SHA256 = "4b9163e3aec3d01462253a0e97f24010c02883c385140bbd3029b39367444883"


def main() -> int:
    parts = []
    for i in range(CHUNKS):
        p = Path("scripts") / f"tg1_payload_{i:02d}.txt"
        parts.append(p.read_text(encoding="ascii").strip())
    encoded = "".join(parts)
    compressed = base64.b64decode(encoded, validate=True)
    got = hashlib.sha256(compressed).hexdigest()
    if got != COMPRESSED_SHA256:
        raise SystemExit(f"TouchGrass payload compressed SHA mismatch: {got}")
    raw = zlib.decompress(compressed)
    got = hashlib.sha256(raw).hexdigest()
    if got != JSON_SHA256:
        raise SystemExit(f"TouchGrass payload JSON SHA mismatch: {got}")
    files = json.loads(raw.decode("utf-8"))
    expected = {
        "scripts/tg1_apply_critical_flight_recorder.py",
        "scripts/tg1_check_critical_flight_recorder.py",
        "scripts/tg1_decode_critical_bank.py",
        "scripts/tg1_ci_build.sh",
        "scripts/tg1_design.md",
    }
    if set(files) != expected:
        raise SystemExit("TouchGrass payload file manifest mismatch")
    for name, content in files.items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if path.suffix in {".py", ".sh"}:
            path.chmod(0o755)
        print(f"materialized {name}")
    print("TouchGrass critical recorder payload integrity: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
