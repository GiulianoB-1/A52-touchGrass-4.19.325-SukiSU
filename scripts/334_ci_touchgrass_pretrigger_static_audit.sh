#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
OUT="$PWD/phase334-static-audit"
FAIL="$PWD/phase334-static-audit-failure"
STAGE=startup

stage() { STAGE="$1"; echo "== Phase334 static-audit stage: $STAGE =="; }
fail_report() {
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase334-*.log "$FAIL/logs/" 2>/dev/null || true
  cp -a "$OUT" "$FAIL/audit/output" 2>/dev/null || true
  cp scripts/334_touchgrass_pretrigger_static_audit.py scripts/334_ci_touchgrass_pretrigger_static_audit.sh "$FAIL/audit/" 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase332 source"
bash scripts/332_ci_build_gki.sh 2>&1 | tee phase334-phase332-reconstruct.log
for f in \
  phase332-gki-out/package/boot.img \
  phase332-gki-out/compile/Image \
  phase332-gki-out/config/final.config \
  "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c" \
  "$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c" \
  "$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c" \
  "$ROOT/drivers/a52_display/msm/msm_gem.c" \
  "$ROOT/drivers/a52_display/msm/msm_gem_vma.c" \
  "$ROOT/drivers/a52_display/msm/msm_smmu.c" \
  "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c" \
  "$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c" \
  "$TG/techpack/display/msm/dsi/dsi_display.c" \
  "$TG/drivers/iommu/arm-smmu.c"; do
  test -s "$f"
done

stage "run existing broad display source parity audit"
rm -rf "$OUT"; mkdir -p "$OUT"/broad-display
python3 scripts/157_compare_a52_display_to_touchgrass.py \
  --ack "$ROOT" \
  --touchgrass "$TG" \
  --output "$OUT/broad-display" \
  2>&1 | tee phase334-broad-display.log

stage "run focused pre-trigger TouchGrass audit"
python3 -m py_compile scripts/334_touchgrass_pretrigger_static_audit.py
python3 scripts/334_touchgrass_pretrigger_static_audit.py \
  --gki "$ROOT" \
  --touchgrass "$TG" \
  --out "$OUT" \
  2>&1 | tee phase334-focused.log

stage "hard evidence gates"
test -s "$OUT/phase334-static-audit.json"
test -s "$OUT/PHASE334-REPORT.md"
test -s "$OUT/broad-display/a52-display-touchgrass-parity-report.json"

python3 - "$OUT/phase334-static-audit.json" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
d=json.loads(p.read_text())
if d.get("status") != "phase334-touchgrass-pretrigger-static-audit-v1":
    raise SystemExit("Phase334 report status mismatch")
if not d.get("findings"):
    raise SystemExit("Phase334 produced no prioritized findings")
# Fail closed if the pinned TouchGrass source no longer contains the expected
# QSMMUv500 sACR CACHE_LOCK release sequence. That sequence is the specific
# static contract we discovered and want to preserve as evidence.
if not d.get("touchgrass_qsmmuv500_arch_init_has_sacr_cache_unlock"):
    raise SystemExit("Pinned TouchGrass QSMMUv500 sACR CACHE_LOCK sequence missing")
print("Phase334 static report evidence gates: PASS")
PY

stage "identity and checksums"
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path("phase334-static-audit")
report=json.loads((r/"phase334-static-audit.json").read_text())
identity={
  "phase":"334",
  "name":"TOUCHGRASS-PRETRIGGER-STATIC-AUDIT-V1",
  "git_sha":os.getenv("GITHUB_SHA"),
  "hardware_test_required":False,
  "kernel_behavior_changed":False,
  "base_source":"exact Phase332 reconstruction",
  "touchgrass_commit":"6bf351bdf18bdb228db79e66f14a7a9c0178e5d7",
  "mission":"find remaining TouchGrass-vs-GKI contracts capable of establishing a different q0 state before SW_TRIGGER",
  "finding_keys":[x["key"] for x in report.get("findings",[])],
}
(r/"BUILD-IDENTITY.json").write_text(json.dumps(identity,indent=2,sort_keys=True)+"\n")
files=[p for p in r.rglob("*") if p.is_file() and p.name!="SHA256SUMS"]
with (r/"SHA256SUMS").open("w") as f:
  for p in sorted(files):
    f.write(hashlib.sha256(p.read_bytes()).hexdigest()+"  ./"+p.relative_to(r).as_posix()+"\n")
PY
(cd "$OUT" && sha256sum -c SHA256SUMS)

stage complete
echo "Phase334 TouchGrass pre-trigger static audit: PASS"
trap - EXIT
