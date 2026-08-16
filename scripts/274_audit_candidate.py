#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <phase274-out>")
root = Path(sys.argv[1])

required = [
    root / "BUILD-IDENTITY.json",
    root / "SHA256SUMS",
    root / "compile/Image",
    root / "config/final.config",
    root / "audit/phase273-final.config",
    root / "audit/phase274-gpara-parity-before.txt",
    root / "package/Image.gz",
    root / "package/boot.img",
    root / "package/repack-report.json",
    root / "source/a52_ack_secure_flight_recorder.c",
    root / "source/ss_dsi_panel_common.c",
    root / "source/drm_mode.h",
]
for p in required:
    if not p.is_file() or p.stat().st_size == 0:
        raise SystemExit(f"missing/empty required file: {p}")

identity = json.loads((root / "BUILD-IDENTITY.json").read_text())
assert identity["phase"] == 274
assert identity["hardware_validated"] is False
assert identity["base_phase273_hardware_head"] == "8a621ad9db835dc7b465a1582d9a518170d9b131"

parity = (root / "audit/phase274-gpara-parity-before.txt").read_text()
assert "match_after_removing_existing_scope=1" in parity

rec = (root / "source/a52_ack_secure_flight_recorder.c").read_text()
panel = (root / "source/ss_dsi_panel_common.c").read_text()
for token in [
    "A52_PHASE274_FRONTIER_TIMEBASE_FIX_V1",
    'return !strncmp(message, "P274 ", 5)',
    'P274 START tb=elapsed q=%u/%u s=%u',
    'jiffies - a52_r274_frontier_start_jiffies',
    'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d',
]:
    assert token in rec, token
for token in [
    "A52_PHASE274_PANEL_GPARA_FRONTIER_V1",
    'P274 G E n=%d ty=%d lk=%x g=%u p=%u',
    'P274 G K n=%d p=0 lk=%x',
    'P274 G P n=%d i=%d p=0 o=%d',
    'P274 G R n=%d i=%d p=0 l=%d',
    'P274 G Z n=%d cp=%d',
    'ss_send_cmd(vdd, TX_REG_READ_POS);',
    'ss_send_cmd(vdd, type);',
]:
    assert token in panel, token

if (root / "config/final.config").read_bytes() != (root / "audit/phase273-final.config").read_bytes():
    raise SystemExit("Phase274 mutated kernel config")

sums = {}
for line in (root / "SHA256SUMS").read_text().splitlines():
    if not line.strip():
        continue
    digest, rel = line.split(None, 1)
    sums[rel.strip().removeprefix("./")] = digest
for rel, digest in sums.items():
    p = root / rel
    if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
        raise SystemExit(f"checksum mismatch: {rel}")

print(json.dumps({
    "status": "phase274-audit-pass",
    "gpara_preinstrumentation_touchgrass_parity": True,
    "config_unchanged_from_phase273": True,
    "hardware_validated": False,
}, sort_keys=True))
