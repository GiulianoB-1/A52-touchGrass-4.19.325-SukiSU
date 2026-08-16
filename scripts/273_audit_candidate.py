#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <phase273-out>")

out = Path(sys.argv[1])
required = [
    out / "compile/Image",
    out / "config/final.config",
    out / "package/Image.gz",
    out / "package/boot.img",
    out / "package/repack-report.json",
    out / "audit/phase272-final.config",
    out / "audit/phase272-parity-after.txt",
    out / "source/a52_ack_secure_flight_recorder.c",
    out / "source/sde_connector.c",
    out / "source/drm_mode.h",
    out / "BUILD-IDENTITY.json",
    out / "SHA256SUMS",
]
for path in required:
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"missing/empty: {path}")

identity = json.loads((out / "BUILD-IDENTITY.json").read_text())
assert identity["phase"] == 273
assert identity["name"] == "LATE-BOOT-FRONTIER-RECORDER-V2"
assert identity["hardware_validated"] is False
assert identity["base_phase272_hardware_head"] == "7616d96f95bceb1247b9deeec8a31cd42153cb11"
assert identity["wire_format"] == "unchanged R48 v3 / RS48 / CRC32C"

if (out / "config/final.config").read_bytes() != (out / "audit/phase272-final.config").read_bytes():
    raise SystemExit("kernel config differs from reconstructed Phase272 baseline")

parity = (out / "audit/phase272-parity-after.txt").read_text()
for token in ("gki_cmd_macro_present=1", "gki_all_mentions_cmd=1", "tg_all_mentions_cmd=1"):
    if token not in parity:
        raise SystemExit(f"Phase272 DRM parity token missing: {token}")

rec = (out / "source/a52_ack_secure_flight_recorder.c").read_text()
sde = (out / "source/sde_connector.c").read_text()
hdr = (out / "source/drm_mode.h").read_text()
for token in (
    "A52_PHASE273_LATE_BOOT_FRONTIER_RECORDER_V2",
    '#define A52_R179_VERSION 3U',
    '#define A52_R179_RS_ROOTS 48U',
    '#define A52_R179_PREFIX "R48"',
    'return !strncmp(message, "P273 ", 5)',
    'P273 U + k=%s p=%d t=%d pp=%d c=%.15s',
    'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d',
    'P273 D t=%lu n=%d k=%c fn=%.36s',
    'P273 R t=%lu kd=%d nm=%d e=%d s=%d',
    'A52_R273_FRONTIER_END_S 900U',
):
    if token not in rec:
        raise SystemExit(f"recorder audit token missing: {token}")
for token in (
    "A52_PHASE273_P271_SB_DEDUP_V2",
    "call <= 8 || sig != prev || !(call & 0x7f)",
    'P271 SB id=%u eid=%u stp=%u best=%u',
    "return c_conn->encoder;",
):
    if token not in sde:
        raise SystemExit(f"SDE audit token missing: {token}")
for token in (
    "A52_PHASE272_DRM_VENDOR_MODE_FLAG_PARITY_V1",
    "DRM_MODE_FLAG_CMD_MODE_PANEL",
    "DRM_MODE_FLAG_VID_MODE_PANEL",
    "DRM_MODE_FLAG_SUPPORTS_RGB",
    "DRM_MODE_FLAG_SUPPORTS_YUV",
):
    if token not in hdr:
        raise SystemExit(f"Phase272 header token missing: {token}")

listed = {}
for raw in (out / "SHA256SUMS").read_text().splitlines():
    digest, rel = raw.split(None, 1)
    rel = rel.strip()
    if rel.startswith("./"):
        rel = rel[2:]
    listed[rel] = digest
for rel, expected in listed.items():
    path = out / rel
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"sha256 mismatch: {rel}")

print("Phase273 candidate audit: PASS")
