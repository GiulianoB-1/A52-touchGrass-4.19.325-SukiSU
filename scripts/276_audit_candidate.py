#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <phase276-out>")
root = Path(sys.argv[1])

required = [
    root/"BUILD-IDENTITY.json",
    root/"SHA256SUMS",
    root/"compile/Image",
    root/"config/final.config",
    root/"audit/phase275-final.config",
    root/"audit/phase276-dsi-panel-tx-parity-before.txt",
    root/"package/Image.gz",
    root/"package/boot.img",
    root/"package/repack-report.json",
    root/"source/a52_ack_secure_flight_recorder.c",
    root/"source/dsi_panel.c",
    root/"source/ss_dsi_panel_common.c",
    root/"source/ss_wrapper_common.c",
    root/"source/drm_mode.h",
]
for p in required:
    if not p.is_file() or p.stat().st_size == 0:
        raise SystemExit(f"missing/empty required file: {p}")

identity = json.loads((root/"BUILD-IDENTITY.json").read_text())
assert identity["phase"] == 276
assert identity["hardware_validated"] is False
assert identity["base_phase275_hardware_head"] == "fefb6bd6042b27ac8ca3c0bb77c019dbc159686f"

parity = (root/"audit/phase276-dsi-panel-tx-parity-before.txt").read_text()
assert "function=dsi_panel_tx_cmd_set" in parity
assert "exact_match=1" in parity

rec = (root/"source/a52_ack_secure_flight_recorder.c").read_text()
dsi = (root/"source/dsi_panel.c").read_text()
for token in [
    "A52_PHASE276_DSI_PANEL_TRANSFER_FRONTIER_V1",
    'return !strncmp(message, "P276 ", 5)',
]:
    assert token in rec, token
for token in [
    "A52_PHASE276_DSI_PANEL_TX_FRONTIER_V1",
    'P276 T E ty=%d',
    'P276 T A n=%x s=%x e=%x p=%x',
    'P276 T L ty=%d p=0 h=%u',
    'P276 T L ty=%d p=1',
    'P276 T M0 n=%x s=%x mt=%x l=%x',
    'P276 T M1 f=%x c=%x z=%x w=%x',
    'P276 T O i=%d p=0 mt=%u tl=%u fl=%x',
    'P276 T O i=%d p=1 len=%zd',
    'P276 T Z ty=%d p=1 rc=%d',
    'ops->transfer(panel->host, &cmds->msg);',
]:
    assert token in dsi, token

if (root/"config/final.config").read_bytes() != (root/"audit/phase275-final.config").read_bytes():
    raise SystemExit("Phase276 mutated kernel config")

for line in (root/"SHA256SUMS").read_text().splitlines():
    if not line.strip():
        continue
    digest, rel = line.split(None, 1)
    p = root / rel.strip().removeprefix("./")
    if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
        raise SystemExit(f"checksum mismatch: {p}")

print(json.dumps({
    "status":"phase276-audit-pass",
    "dsi_panel_tx_touchgrass_parity":True,
    "config_unchanged_from_phase275":True,
    "hardware_validated":False,
}, sort_keys=True))
