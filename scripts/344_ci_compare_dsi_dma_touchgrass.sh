#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
OUT="$PWD/phase344-dsi-dma-audit"
FAIL="$PWD/phase344-dsi-dma-audit-failure"
STAGE=startup

stage(){ STAGE="$1"; echo "== Phase344 DMA audit stage: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase344-*.log "$FAIL/logs/" 2>/dev/null || true
  cp -a "$OUT" "$FAIL/audit/output" 2>/dev/null || true
  cp scripts/344_compare_dsi_dma_touchgrass.py scripts/344_ci_compare_dsi_dma_touchgrass.sh "$FAIL/audit/" 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact current DSI source point"
# Phases 335-343 do not modify the DSI controller/hardware implementation.
# Phase332 is therefore the exact current DSI source point and avoids replaying
# later recorder-only diagnostics.
bash scripts/332_ci_build_gki.sh 2>&1 | tee phase344-phase332-reconstruct.log

for f in \
  "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c" \
  "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c" \
  "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw.h" \
  "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_reg.h" \
  "$TG/techpack/display/msm/dsi/dsi_ctrl.c" \
  "$TG/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c" \
  "$TG/techpack/display/msm/dsi/dsi_ctrl_hw.h" \
  "$TG/techpack/display/msm/dsi/dsi_ctrl_reg.h"; do
  test -s "$f"
done

stage "compare trigger and completion path"
rm -rf "$OUT"; mkdir -p "$OUT"
python3 -m py_compile scripts/344_compare_dsi_dma_touchgrass.py
python3 scripts/344_compare_dsi_dma_touchgrass.py \
  --gki "$ROOT" --touchgrass "$TG" --out "$OUT" \
  2>&1 | tee phase344-focused-dma.log

stage "evidence gates"
test -s "$OUT/phase344-dsi-dma-audit.json"
test -s "$OUT/PHASE344-DMA-AUDIT.md"
python3 - "$OUT/phase344-dsi-dma-audit.json" <<'PY'
import json,sys
from pathlib import Path
d=json.loads(Path(sys.argv[1]).read_text())
if d.get("status") != "phase344-dsi-dma-touchgrass-audit-v1":
    raise SystemExit("Phase344 audit status mismatch")
critical={
 "dsi_ctrl_hw_cmn_kickoff_command",
 "dsi_ctrl_hw_cmn_trigger_command_dma",
 "dsi_ctrl_hw_cmn_get_interrupt_status",
 "dsi_ctrl_hw_cmn_clear_interrupt_status",
 "dsi_ctrl_hw_cmn_enable_status_interrupts",
 "dsi_ctrl_dma_cmd_wait_for_done",
 "dsi_kickoff_msg_tx",
}
rows={x.get("function"):x for x in d.get("functions",[])}
missing=[f for f in critical if f not in rows]
if missing:
    raise SystemExit("Phase344 missing critical comparison rows: "+",".join(sorted(missing)))
print("Phase344 DMA audit evidence gates: PASS")
PY

stage "identity and checksums"
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path("phase344-dsi-dma-audit")
d=json.loads((r/"phase344-dsi-dma-audit.json").read_text())
identity={
 "phase":"344A",
 "name":"TOUCHGRASS-DSI-DMA-TRIGGER-COMPLETION-AUDIT-V1",
 "git_sha":os.getenv("GITHUB_SHA"),
 "kernel_behavior_changed":False,
 "hardware_test_required":False,
 "gki_dsi_source_point":"Phase332 (DSI unchanged through Phase343)",
 "touchgrass_commit":"6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
 "mission":"identify remaining source-level differences capable of explaining q0 parity, q1 bit12 divergence and missing DMA_DONE",
}
(r/"BUILD-IDENTITY.json").write_text(json.dumps(identity,indent=2,sort_keys=True)+"\n")
files=[p for p in r.rglob("*") if p.is_file() and p.name!="SHA256SUMS"]
with (r/"SHA256SUMS").open("w") as f:
  for p in sorted(files):
    f.write(hashlib.sha256(p.read_bytes()).hexdigest()+"  ./"+p.relative_to(r).as_posix()+"\n")
PY
(cd "$OUT" && sha256sum -c SHA256SUMS)

stage complete
echo "Phase344A DSI DMA TouchGrass audit: PASS"
trap - EXIT
