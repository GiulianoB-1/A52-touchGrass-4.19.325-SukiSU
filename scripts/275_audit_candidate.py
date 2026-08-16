#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <phase275-out>")
root = Path(sys.argv[1])
required = [
    root / "BUILD-IDENTITY.json",
    root / "SHA256SUMS",
    root / "compile/Image",
    root / "config/final.config",
    root / "audit/phase274-final.config",
    root / "audit/phase275-send-path-parity-before.txt",
    root / "package/Image.gz",
    root / "package/boot.img",
    root / "package/repack-report.json",
    root / "source/a52_ack_secure_flight_recorder.c",
    root / "source/ss_dsi_panel_common.c",
    root / "source/ss_wrapper_common.c",
    root / "source/sde_connector.c",
    root / "source/drm_mode.h",
]
for p in required:
    if not p.is_file() or p.stat().st_size == 0:
        raise SystemExit(f"missing/empty required file: {p}")

identity = json.loads((root / "BUILD-IDENTITY.json").read_text())
assert identity["phase"] == 275
assert identity["name"] == "LEVEL1-KEY-SEND-FRONTIER-V1"
assert identity["hardware_validated"] is False
assert identity["base_phase274_hardware_head"] == "ab0ab2127f067112ad9b9ffb69185fa9a3d55c64"
assert identity["target_command"] == "TX_LEVEL1_KEY_ENABLE"

parity = (root / "audit/phase275-send-path-parity-before.txt").read_text()
for token in (
    "function=ss_send_cmd",
    "function=ss_wrapper_dsi_panel_tx_cmd_set",
    "all_exact_match=1",
):
    if token not in parity:
        raise SystemExit(f"send-path parity token missing: {token}")

rec = (root / "source/a52_ack_secure_flight_recorder.c").read_text()
panel = (root / "source/ss_dsi_panel_common.c").read_text()
wrap = (root / "source/ss_wrapper_common.c").read_text()
for token in (
    "A52_PHASE275_LEVEL1_KEY_SEND_FRONTIER_V1",
    'return !strncmp(message, "P275 ", 5)',
    'P273 F t=%lu n=%x e=%x g=%x f=%d z=%d s=%d u=%d b=%d',
):
    assert token in rec, token
for token in (
    "A52_PHASE275_SS_SEND_CMD_FRONTIER_V1",
    'P275 S E ty=%d',
    'P275 S L ty=%d p=0',
    'P275 S L ty=%d p=1',
    'P275 S M ty=%d p=0',
    'P275 S M ty=%d p=1',
    'P275 S W ty=%d p=0',
    'P275 S W ty=%d p=1',
    'P275 S Z ty=%d rc=%d',
):
    assert token in panel, token
for token in (
    "A52_PHASE275_DSI_TX_WRAPPER_FRONTIER_V1",
    'P275 W E ty=%d',
    'P275 W C ty=%d p=0 on=1',
    'P275 W C ty=%d p=1 on=1 rc=%d',
    'P275 W T ty=%d p=0',
    'P275 W T ty=%d p=1 rc=%d',
    'P275 W C ty=%d p=0 on=0',
    'P275 W C ty=%d p=1 on=0 rc=%d',
    'P275 W Z ty=%d rc=%d',
):
    assert token in wrap, token

if (root / "config/final.config").read_bytes() != (root / "audit/phase274-final.config").read_bytes():
    raise SystemExit("Phase275 mutated kernel config")

for line in (root / "SHA256SUMS").read_text().splitlines():
    if not line.strip():
        continue
    digest, rel = line.split(None, 1)
    rel = rel.strip().removeprefix("./")
    actual = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    if actual != digest:
        raise SystemExit(f"checksum mismatch: {rel}")

print(json.dumps({
    "status": "phase275-audit-pass",
    "preinstrumentation_send_path_touchgrass_exact": True,
    "target": "TX_LEVEL1_KEY_ENABLE",
    "config_unchanged_from_phase274": True,
    "hardware_validated": False,
}, sort_keys=True))
