#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase335-gki-out"
FAIL="$PWD/phase335-gki-failure"

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
stage() { STAGE="$1"; echo "== Phase335 stage: $STAGE =="; }

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase335-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p335-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/335_apply_qsmmuv500_sacr_cache_lock_parity.py scripts/335_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$SMMU" "$SMMU_H" "$SMMU_QCOM" "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$DISP"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase332 evidence baseline"
bash scripts/332_ci_build_gki.sh 2>&1 | tee phase335-phase332.log

for f in \
  phase332-gki-out/package/boot.img \
  phase332-gki-out/compile/Image \
  phase332-gki-out/config/final.config \
  "$SMMU" "$SMMU_H" "$SMMU_QCOM" "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$DISP"; do
  test -s "$f"
done

test "$(stat -c '%s' phase332-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE332_PERSISTENT_GDM_TIMEOUT_FRONTIER_V1' "$CTRL"
grep -Fq 'P276 332G q=2 dump=0' "$CTRL"
grep -Fq 'qcom,qsmmu-v500' "$SMMU"
grep -Fq 'qcom,skip-init' "$SMMU"
grep -Fq 'if (!smmu->skip_init && smmu->impl && smmu->impl->reset)' "$SMMU"

cp phase332-gki-out/config/final.config /tmp/p335-phase332.config
cp "$SMMU" /tmp/p335-smmu-before
for spec in \
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
  cp "$f" "/tmp/p335-$n-before"
done

stage "apply only TouchGrass QSMMUv500 sACR CACHE_LOCK parity"
python3 -m py_compile scripts/335_apply_qsmmuv500_sacr_cache_lock_parity.py
python3 scripts/335_apply_qsmmuv500_sacr_cache_lock_parity.py --root "$ROOT"
python3 scripts/335_apply_qsmmuv500_sacr_cache_lock_parity.py \
  --root "$ROOT" --check-only --before /tmp/p335-smmu-before

! cmp -s /tmp/p335-smmu-before "$SMMU"
cmp -s /tmp/p335-smmuh-before "$SMMU_H"
cmp -s /tmp/p335-smmuq-before "$SMMU_QCOM"
cmp -s /tmp/p335-ctrl-before "$CTRL"
cmp -s /tmp/p335-hwc-before "$HWC"
cmp -s /tmp/p335-phy-before "$PHY"
cmp -s /tmp/p335-phyv3-before "$PHYV3"
cmp -s /tmp/p335-rec-before "$REC"
cmp -s /tmp/p335-disp-before "$DISP"

diff -u /tmp/p335-smmu-before "$SMMU" > /tmp/p335-smmu.diff || true
git -C "$ROOT" diff --check -- drivers/iommu/arm/arm-smmu/arm-smmu.c

stage "strict one-variable MMU scope audit"
python3 - "$SMMU" <<'PY'
from pathlib import Path
import sys

before = Path("/tmp/p335-smmu-before").read_text()
after = Path(sys.argv[1]).read_text()

mark = "A52_PHASE335_QSMMUV500_SACR_CACHE_LOCK_PARITY_V1"
if mark not in after:
    raise SystemExit("Phase335 marker missing")

start = after.index("/* " + mark)
end = after.index(
    "\tif (!smmu->skip_init && smmu->impl && smmu->impl->reset)",
    start,
)
block = after[start:end]

required = (
    'of_device_is_compatible(smmu->dev->of_node, "qcom,qsmmu-v500")',
    "smmu->skip_init &&",
    "readl_relaxed(smmu->base + 0x10)",
    "a52_p335_before & ~(1U << 26)",
    "writel_relaxed(a52_p335_wanted, smmu->base + 0x10)",
    "A52 P335 QSMMUV500 sACR before=%08x after=%08x changed=%u lock=%u",
)
for token in required:
    if token not in block:
        raise SystemExit("Phase335 block missing: " + token)

if block.count("writel_relaxed(") != 1:
    raise SystemExit("Phase335 must add exactly one possible MMIO write")
if block.count("readl_relaxed(") != 2:
    raise SystemExit("Phase335 must add exactly two sACR reads")

for token in (
    "arm_smmu_write_sme(",
    "arm_smmu_write_context_bank(",
    "arm_smmu_tlb_",
    "impl->reset",
    "clk_",
    "regulator_",
    "reset_control_",
    "udelay(",
    "usleep_range(",
    "msleep(",
):
    if token in block:
        raise SystemExit("Phase335 forbidden behavior in parity block: " + token)

for token in (
    "arm_smmu_write_sme(",
    "arm_smmu_write_context_bank(",
    "arm_smmu_tlb_sync_global(",
    "impl->reset(smmu)",
):
    if before.count(token) != after.count(token):
        raise SystemExit(
            f"Phase335 altered inherited SMMU behavior {token}: "
            f"{before.count(token)} -> {after.count(token)}"
        )

if after.count("writel_relaxed(") != before.count("writel_relaxed(") + 1:
    raise SystemExit("Phase335 added more than the one intended MMIO write")
if after.count("readl_relaxed(") != before.count("readl_relaxed(") + 2:
    raise SystemExit("Phase335 added unexpected MMIO reads")

print("Phase335 strict one-variable SMMU scope audit: PASS")
PY

stage "config invariant"
cp /tmp/p335-phase332.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig \
  > phase335-olddefconfig.log 2>&1
cmp -s /tmp/p335-phase332.config "$BUILD/.config"

stage "compile incremental Phase335 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image \
  2>&1 | tee phase335-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"

grep -aFq 'A52 P335 QSMMUV500 sACR before=%08x after=%08x changed=%u lock=%u' "$IMAGE"
grep -aFq 'P276 332G q=2 dump=0' "$IMAGE"
grep -aFq 'P276 330D q=%u c=%x z=%x' "$IMAGE"
grep -aFq 'P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x' "$IMAGE"
grep -aFq 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' "$IMAGE"
grep -aFq 'P276 280Z q=2' "$IMAGE"

stage "package evidence and boot image"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase335-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/335_apply_qsmmuv500_sacr_cache_lock_parity.py scripts/335_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p335-* "$OUT/audit/" 2>/dev/null || true
cp "$SMMU" "$SMMU_H" "$SMMU_QCOM" "$CTRL" "$HWC" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase332-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase332-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE332-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

r = Path("phase335-gki-out")

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

identity = {
    "phase": "335",
    "flavor": "gki",
    "name": "QSMMUV500-SACR-CACHE-LOCK-PARITY-V1",
    "git_sha": os.getenv("GITHUB_SHA"),
    "hardware_validated": False,
    "base_phase": "332",
    "hypothesis": (
        "The exact F0 command has Golden-equivalent DSI pre-trigger state but "
        "diverges immediately after SW_TRIGGER. TouchGrass QSMMUv500 init clears "
        "MMU-500 sACR.CACHE_LOCK while the GKI qcom,skip-init path suppresses "
        "the Qualcomm implementation reset and leaves this semantic unreproduced."
    ),
    "change": (
        "For qcom,qsmmu-v500 devices with qcom,skip-init only, read sACR at "
        "GR0+0x10, clear only bit26 CACHE_LOCK if set, and read it back."
    ),
    "possible_mmio_writes_added": 1,
    "register_offset": "0x10",
    "register_bit": 26,
    "stream_mapping_changes": False,
    "context_bank_changes": False,
    "tlb_changes": False,
    "actlr_changes": False,
    "implementation_reset_changes": False,
    "display_dsi_phy_clock_changes": False,
    "runtime_log": (
        "A52 P335 QSMMUV500 sACR before=<hex> after=<hex> "
        "changed=<0|1> lock=<0|1>"
    ),
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
echo 'Phase335 QSMMUv500 sACR CACHE_LOCK parity build: PASS'
trap - EXIT
