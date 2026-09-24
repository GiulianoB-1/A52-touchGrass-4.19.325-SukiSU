#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase374-gki-out"
FAIL="$PWD/phase374-gki-failure"
GDSC="$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
COMPAT="$ROOT/a52-port-compat.h"

STAGE=startup
stage() {
  STAGE="$1"
  echo "== Phase374: $STAGE =="
}

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase374-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p374-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/374_apply_mdss_gdsc_proxy_parity.py scripts/374_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$GDSC" "$HWC" "$CTRL" "$REC" "$COMPAT"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}

trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase346"

bash scripts/346_ci_build_gki.sh 2>&1 | tee phase374-phase346.log

for f in \
  phase346-gki-out/package/boot.img \
  phase346-gki-out/compile/Image \
  phase346-gki-out/compile/System.map \
  phase346-gki-out/config/final.config \
  "$GDSC" "$HWC" "$CTRL" "$REC" "$COMPAT"; do
  test -s "$f"
done

test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' "$HWC"
grep -Eq '[[:space:]]a52_p346_sideband_init$' phase346-gki-out/compile/System.map

# Lock the exact inherited compatibility state that motivated this A/B.
grep -Fxq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"
grep -Fxq '#define rpmh_flush(d) a52_rpmh_flush_compat((d))' "$COMPAT"
grep -Fq '"mdss_core_gdsc"' "$GDSC"
grep -Fq 'A52_GDSC_PROFILE_MDSS' "$GDSC"
! grep -Fq 'A52_PHASE374_MDSS_GDSC_PROXY_PARITY_V1' "$GDSC"

cp phase346-gki-out/config/final.config /tmp/p374-phase346.config
cp "$GDSC" /tmp/p374-gdsc-before.c
cp "$HWC" /tmp/p374-hwc-before.c
cp "$CTRL" /tmp/p374-ctrl-before.c
cp "$REC" /tmp/p374-rec-before.c
cp "$COMPAT" /tmp/p374-compat-before.h

stage "apply TouchGrass proxy parity"

python3 -m py_compile scripts/374_apply_mdss_gdsc_proxy_parity.py
python3 scripts/374_apply_mdss_gdsc_proxy_parity.py \
  --root "$ROOT" --touchgrass "$TG"
python3 scripts/374_apply_mdss_gdsc_proxy_parity.py \
  --root "$ROOT" --touchgrass "$TG" --check-only

! cmp -s /tmp/p374-gdsc-before.c "$GDSC"
cmp -s /tmp/p374-hwc-before.c "$HWC"
cmp -s /tmp/p374-ctrl-before.c "$CTRL"
cmp -s /tmp/p374-rec-before.c "$REC"
cmp -s /tmp/p374-compat-before.h "$COMPAT"

git diff --no-index --check /tmp/p374-gdsc-before.c "$GDSC" > /tmp/p374-gdsc-check 2>&1 || true
test ! -s /tmp/p374-gdsc-check
git diff --no-index /tmp/p374-gdsc-before.c "$GDSC" > /tmp/p374-gdsc.diff || true

stage "scope audit"

python3 - "$GDSC" <<'PY'
from pathlib import Path
import sys

after = Path(sys.argv[1]).read_text()
before = Path("/tmp/p374-gdsc-before.c").read_text()

required = (
    "A52_PHASE374_MDSS_GDSC_PROXY_PARITY_V1",
    'proxy = regulator_get(&pdev->dev, "proxy");',
    "rc = regulator_enable(proxy);",
    "rc = regulator_disable(proxy);",
    "late_initcall_sync(a52_p374_mdss_proxy_release);",
    "a52_p374_mdss_proxy_acquire(pdev, gdsc);",
    'P374 PX+ en=0 reg=%x',
    'P374 PX- rc=%d before=%x after=%x',
)
for token in required:
    if token not in after:
        raise SystemExit("Phase374 missing intended token: " + token)

expected_delta = {
    'regulator_get(&pdev->dev, "proxy")': 1,
    "regulator_enable(proxy)": 1,
    "regulator_disable(proxy)": 1,
    "late_initcall_sync(a52_p374_mdss_proxy_release)": 1,
}
for token, delta in expected_delta.items():
    got = after.count(token) - before.count(token)
    if got != delta:
        raise SystemExit(
            f"Phase374 intended behavior delta mismatch {token!r}: {got} != {delta}"
        )

# This phase must not directly manipulate the GDSCR or change unrelated power,
# clock, reset, bus, wait, or delay mechanics. The regulator provider remains
# responsible for those operations when the temporary vote is acquired/released.
for token in (
    "writel_relaxed(", "regmap_write(", "clk_prepare_enable(",
    "clk_disable_unprepare(", "reset_control_", "msm_bus_scale_client_update_request(",
    "udelay(", "usleep_range(", "msleep(",
):
    if after.count(token) != before.count(token):
        raise SystemExit("Phase374 unexpected primitive drift: " + token)

if after.count('if (gdsc->profile == A52_GDSC_PROFILE_MDSS)') != \
        before.count('if (gdsc->profile == A52_GDSC_PROFILE_MDSS)') + 1:
    raise SystemExit("Phase374 MDSS-only acquisition guard count mismatch")

print("Phase374 one-variable proxy parity scope audit: PASS")
PY

# Exact Phase346 DSI observation path must remain untouched.
for f in "$HWC" "$CTRL" "$REC"; do
  case "$f" in
    "$HWC") cmp -s "$f" phase346-gki-out/source/dsi_ctrl_hw_cmn.c ;;
    "$CTRL") cmp -s "$f" phase346-gki-out/source/dsi_ctrl.c ;;
    "$REC") cmp -s "$f" phase346-gki-out/source/a52_ack_secure_flight_recorder.c ;;
  esac
done

stage "config"

cp /tmp/p374-phase346.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" \
  ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- \
  LLVM=1 LLVM_IAS=1 \
  olddefconfig > phase374-olddefconfig.log 2>&1

cmp -s /tmp/p374-phase346.config "$BUILD/.config"

stage "compile"

set +e
make -C "$ROOT" O="$BUILD" \
  ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- \
  LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase374-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"
test -s "$SYSTEM_MAP"

for marker in \
  'P374 PX+ en=0 reg=%x' \
  'P374 PX- rc=%d before=%x after=%x' \
  'P276 345R %x %x %llx %x %x %x %x' \
  'P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u'; do
  grep -aFq "$marker" "$IMAGE"
done

grep -Eq '[[:space:]]a52_p346_sideband_init$' "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p374_mdss_proxy_release$' "$SYSTEM_MAP"

stage "package"

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}

cp "$IMAGE" "$OUT/compile/Image"
cp "$SYSTEM_MAP" "$OUT/compile/System.map"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase374-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/374_apply_mdss_gdsc_proxy_parity.py scripts/374_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p374-* "$OUT/audit/" 2>/dev/null || true
cp "$GDSC" "$HWC" "$CTRL" "$REC" "$COMPAT" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"

python3 scripts/38_repack_a52_p1_boot.py \
  --source phase346-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
from pathlib import Path
import hashlib
import json
import os

root = Path("phase374-gki-out")

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

identity = {
    "phase": "374",
    "name": "MDSS-GDSC-PROXY-PARITY-V1",
    "base_phase": "346",
    "base_commit": "445fe7398a96b36633762a3b9e21ae92d8ee71f0",
    "hardware_validated": False,
    "functional_change": (
        "A52 mdss_core_gdsc only: acquire the DT self proxy-supply enable vote "
        "after provider registration and release it at late_initcall_sync"
    ),
    "touchgrass_contract": (
        "qcom,proxy-consumer-enable -> regulator_get(dev, proxy) -> "
        "regulator_enable -> late_initcall_sync regulator_disable"
    ),
    "dsi_measurement_path_changed": False,
    "phase346_dma_raw_preserved": True,
    "forced_on_at_f0": False,
    "rpmh_solver_behavior_changed": False,
    "rpmh_flush_behavior_changed": False,
    "boot_img_size": (root / "package/boot.img").stat().st_size,
    "boot_img_sha256": sha256(root / "package/boot.img"),
    "image_sha256": sha256(root / "compile/Image"),
    "git_sha": os.getenv("GITHUB_SHA"),
}
(root / "BUILD-IDENTITY.json").write_text(
    json.dumps(identity, indent=2, sort_keys=True) + "\n"
)
PY

cat > "$OUT/PHASE374-TEST.txt" <<'EOF'
Phase374 MDSS GDSC proxy parity A/B
===================================

Why:
  TouchGrass regulator core honors qcom,proxy-consumer-enable.
  The A52 mdss_core_gdsc node points proxy-supply to itself.
  The temporary proxy vote protects the boot-time display domain until
  late_initcall_sync, then TouchGrass removes the vote.

What changes:
  Only the A52 legacy mdss_core_gdsc provider gains the same temporary vote.

What does NOT change:
  DSI controller source
  DSI DMA measurement source
  Phase346 raw sideband
  clocks / PHY / SMMU
  RPMh flush
  RPMh solver shim
  GDSCR enable/disable implementation
  late-runtime/F0 forced-on policy

Expected runtime proof:
  P374 PX+ en=0 reg=<...>
  P374 PX- rc=<...> before=<...> after=<...>

The F0 result remains determined by the inherited Phase346 raw DMA sideband.
EOF

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage "complete"
echo "Phase374 MDSS GDSC proxy parity build: PASS"
trap - EXIT
