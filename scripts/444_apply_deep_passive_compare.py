#!/usr/bin/env python3
from __future__ import annotations

import base64
import zlib
from pathlib import Path

payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
payload = base64.b64decode(payload_path.read_text().strip())
source = zlib.decompress(payload).decode("utf-8")

# Fail before source reconstruction/compilation if the compressed experiment
# payload lost one of the locked Phase444 contracts.
_required = (
    "A52_PHASE444_DEEP_PASSIVE_COMPARE_V1",
    "PRE_DEEP",
    "PRE_TRIGGER",
    "HOT_0_75US",
    "DSI_KONA_BUS",
    "MMC_MAJOR179",
    "a52_p444_sde_capture",
    "a52_p444_store_section",
    "P444 TERM ret=%d/%d irq=%u/%u",
)
_missing = [token for token in _required if token not in source]
if _missing:
    raise SystemExit("Phase444 payload contract missing: " + ", ".join(_missing))

_low = source.lower()
_has_crc = ("crc=%" in _low) or ("crc32" in _low)
_dup_sets = (
    ("st=%x/%x", "ck=%x/%x", "in=%x/%x", "ln=%x/%x", "db=%x/%x"),
    ("st=%08x/%08x", "ck=%08x/%08x", "in=%08x/%08x",
     "ln=%08x/%08x", "db=%08x/%08x"),
)
_has_dups = any(all(token in source for token in group) for group in _dup_sets)
if not (_has_crc or _has_dups):
    raise SystemExit(
        "Phase444 payload lacks TG clear-bit-corruption protection "
        "(need CRC32 or duplicated key fields)"
    )

exec(compile(source, str(Path(__file__).with_suffix(".expanded.py")), "exec"), globals())
