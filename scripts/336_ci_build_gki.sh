#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase336-gki-out"
FAIL="$PWD/phase336-gki-failure"

SMMU="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.c"
SMMU_H="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu.h"
SMMU_QCOM="$ROOT/drivers/iommu/arm/arm-smmu/arm-smmu-qcom.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"

STAGE=startup
stage() { STAGE="$1"; echo "== Phase336 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase336-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p336-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/336_apply_qsmmuv500_hf0_tbu_state_recorder.py scripts/336_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$SMMU" "$SMMU_H" "$SMMU_QCOM" "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$DISP"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase335 baseline"
bash scripts/335_ci_build_gki.sh 2>&1 | tee phase336-phase335.log

for f in \
  phase335-gki-out/package/boot.img \
  phase335-gki-out/compile/Image \
  phase335-gki-out/config/final.config \
  "$SMMU" "$SMMU_H" "$SMMU_QCOM" "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$DISP"; do
  test -s "$f"
done

test "$(stat -c '%s' phase335-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE335_QSMMUV500_SACR_CACHE_LOCK_PARITY_V1' "$SMMU"
grep -Fq 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1' "$CTRL"
grep -Fq 'P276 316K q=%u ck=%x b7=%u b10=%u b12=%u b16=%u b23=%u' "$HWC"

cp phase335-gki-out/config/final.config /tmp/p336-phase335.config
for spec in \
  smmu:"$SMMU" \
  smmuh:"$SMMU_H" \
  smmuq:"$SMMU_QCOM" \
  ctrl:"$CTRL" \
  hwc:"$HWC" \
  phy:"$PHY" \
  phyv3:"$PHYV3" \
  rec:"$REC" \
  disp:"$DISP"; do
  n=${spec%%:*}
  f=${spec#*:}
  cp "$f" "/tmp/p336-$n-before"
done

stage "apply read-only apps-SMMU/HF0 TBU state recorder"
python3 -m py_compile scripts/336_apply_qsmmuv500_hf0_tbu_state_recorder.py
python3 scripts/336_apply_qsmmuv500_hf0_tbu_state_recorder.py --root "$ROOT"
python3 scripts/336_apply_qsmmuv500_hf0_tbu_state_recorder.py \
  --root "$ROOT" --check-only \
  --before-smmu /tmp/p336-smmu-before \
  --before-hwc /tmp/p336-hwc-before

! cmp -s /tmp/p336-smmu-before "$SMMU"
! cmp -s /tmp/p336-hwc-before "$HWC"
cmp -s /tmp/p336-smmuh-before "$SMMU_H"
cmp -s /tmp/p336-smmuq-before "$SMMU_QCOM"
cmp -s /tmp/p336-ctrl-before "$CTRL"
cmp -s /tmp/p336-phy-before "$PHY"
cmp -s /tmp/p336-phyv3-before "$PHYV3"
cmp -s /tmp/p336-rec-before "$REC"
cmp -s /tmp/p336-disp-before "$DISP"

diff -u /tmp/p336-smmu-before "$SMMU" > /tmp/p336-smmu.diff || true
diff -u /tmp/p336-hwc-before "$HWC" > /tmp/p336-hwc.diff || true
git -C "$ROOT" diff --check -- \
  drivers/iommu/arm/arm-smmu/arm-smmu.c \
  drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c

stage "strict Phase336 diagnostic-only scope audit"
python3 - "$SMMU" "$HWC" <<'PY'
from pathlib import Path
import sys

bs = Path("/tmp/p336-smmu-before").read_text()
as_ = Path(sys.argv[1]).read_text()
bh = Path("/tmp/p336-hwc-before").read_text()
ah = Path(sys.argv[2]).read_text()

for token in (
    "A52_PHASE336_QSMMUV500_HF0_TBU_STATE_RECORDER_V1",
    "a52_p336_res->start == (resource_size_t)0x15000000",
    "(resource_size_t)0x15182210",
    "P276 336A q=%u v=%u sb=%x sa=%x sc=%x tv=%u ts=%x",
):
    if token not in as_ + ah:
        raise SystemExit("Phase336 marker missing: " + token)

protected = (
    "writel_relaxed(", "writel(", "writeq_relaxed(", "regmap_write(",
    "regmap_update_bits(", "DSI_W32(", "clk_set_rate(", "clk_set_parent(",
    "clk_prepare_enable(", "clk_disable_unprepare(", "regulator_enable(",
    "regulator_disable(", "reset_control_assert(", "reset_control_deassert(",
    "arm_smmu_write_sme(", "arm_smmu_write_context_bank(",
    "arm_smmu_tlb_sync_global(", "impl->reset(smmu)",
)
for token in protected:
    before = bs.count(token) + bh.count(token)
    after = as_.count(token) + ah.count(token)
    if before != after:
        raise SystemExit(
            f"Phase336 changed protected behavior {token}: {before} -> {after}"
        )

if as_.count("devm_ioremap(") != bs.count("devm_ioremap(") + 1:
    raise SystemExit("Phase336 expected exactly one HF0 observer mapping")
if as_.count("readl_relaxed(") != bs.count("readl_relaxed(") + 2:
    raise SystemExit("Phase336 expected exactly two live observer MMIO reads")
if ah.count("a52_ackfr_record(") != bh.count("a52_ackfr_record(") + 1:
    raise SystemExit("Phase336 expected exactly one new q-point record")

print("Phase336 strict diagnostic-only scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p336-phase335.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase336-olddefconfig.log 2>&1
cmp -s /tmp/p336-phase335.config "$BUILD/.config"

stage "compile incremental Phase336 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase336-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

grep -aFq 'P276 336A q=%u v=%u sb=%x sa=%x sc=%x tv=%u ts=%x' "$IMAGE"
grep -aFq 'A52 P335 QSMMUV500 sACR before=%08x after=%08x changed=%u lock=%u' "$IMAGE"
grep -aFq 'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' "$IMAGE"
grep -aFq 'P276 332G q=2 dump=0' "$IMAGE"
grep -aFq 'P276 280Z q=2' "$IMAGE"

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase336-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/336_apply_qsmmuv500_hf0_tbu_state_recorder.py scripts/336_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p336-* "$OUT/audit/" 2>/dev/null || true
cp "$SMMU" "$SMMU_H" "$SMMU_QCOM" "$CTRL" "$HWC" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase335-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase335-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE335-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase336-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "336",
    "flavor": "gki",
    "name": "QSMMUV500-HF0-TBU-STATE-RECORDER-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "335",
    "phase335_hardware_finding": (
        "Exact F0 5A5A still reaches q1 st=3 ck=0x8037c3 and q2 remains "
        "st=3 with irq=0, DMA_DONE=0, ret=0. Phase335 did not fix the failure."
    ),
    "blind_spot_closed": (
        "Phase335 sACR before/after was emitted only during early SMMU init and "
        "was overwritten before the exact-target timeout. Phase336 retains it "
        "and re-emits it at q0/q1/q2."
    ),
    "display_sid": "0x800",
    "apps_smmu_phys": "0x15000000",
    "hf0_tbu_phys": "0x1518d000",
    "hf0_tbu_status_phys": "0x15182210",
    "change": (
        "Read-only diagnostic: retain apps-SMMU Phase335 sACR before/after, "
        "map the DT-proven HF0 TBU status register once, and record live sACR "
        "plus HF0 status in the existing exact-F0 q0/q1/q2 snapshot."
    ),
    "register_writes_added": 0,
    "dsi_behavior_changed": False,
    "clock_phy_reset_regulator_changed": False,
    "smmu_stream_context_tlb_changed": False,
    "image_sha256": sha(r / "compile/Image"),
    "boot_img_sha256": sha(r / "package/boot.img"),
    "boot_img_size": (r / "package/boot.img").stat().st_size,
}
(r / "BUILD-IDENTITY.json").write_text(
    json.dumps(identity, indent=2, sort_keys=True) + "\n"
)
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage complete
echo 'Phase336 QSMMUv500 HF0 TBU state recorder build: PASS'
trap - EXIT
