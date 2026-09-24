#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase376-gki-out"
FAIL="$PWD/phase376-gki-failure"
GDSC="$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c"
RSCV3="$ROOT/drivers/a52_display/msm/sde_rsc_hw_v3.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
COMPAT="$ROOT/a52-port-compat.h"

STAGE=startup
stage() {
  STAGE="$1"
  echo "== Phase376: $STAGE =="
}

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase376-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p376-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/376_apply_mdss_gdsc_hwctrl_parity.py scripts/376_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$GDSC" "$RSCV3" "$HWC" "$CTRL" "$REC" "$COMPAT"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}

trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase346"

bash scripts/346_ci_build_gki.sh 2>&1 | tee phase376-phase346.log

for f in \
  phase346-gki-out/package/boot.img \
  phase346-gki-out/compile/Image \
  phase346-gki-out/compile/System.map \
  phase346-gki-out/config/final.config \
  "$GDSC" "$RSCV3" "$HWC" "$CTRL" "$REC" "$COMPAT"; do
  test -s "$f"
done

test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' "$HWC"
grep -Eq '[[:space:]]a52_p346_sideband_init$' phase346-gki-out/compile/System.map

grep -Fq '"mdss_core_gdsc"' "$GDSC"
grep -Fq 'A52_GDSC_PROFILE_MDSS' "$GDSC"
grep -Fq 'val &= ~A52_GDSC_HW_CONTROL;' "$GDSC"
! grep -Fq 'A52_PHASE376_MDSS_GDSC_HWCTRL_PARITY_V1' "$GDSC"

# Prove that the exact Phase346 display source still performs the TouchGrass
# RSC-v3 ownership sequence that makes the provider mismatch relevant:
# FAST/HW-control first, then the software regulator vote is dropped.
python3 - "$RSCV3" "$TG/techpack/display/msm/sde_rsc_hw_v3.c" <<'PY'
from pathlib import Path
import sys

def mode2_entry(path: Path) -> str:
    text = path.read_text(errors="replace")
    start = text.find("static int sde_rsc_mode2_entry_v3")
    if start < 0:
        raise SystemExit(f"Phase376 missing mode2 entry in {path}")
    end = text.find("\nstatic ", start + 1)
    if end < 0:
        raise SystemExit(f"Phase376 cannot bound mode2 entry in {path}")
    return text[start:end]

for path in map(Path, sys.argv[1:]):
    fn = mode2_entry(path)
    fast = "regulator_set_mode(rsc->fs, REGULATOR_MODE_FAST)"
    disable = "regulator_disable(rsc->fs)"
    if fast not in fn or disable not in fn:
        raise SystemExit(f"Phase376 RSC ownership sequence missing in {path}")
    if fn.index(fast) >= fn.index(disable):
        raise SystemExit(f"Phase376 RSC ownership order drifted in {path}")
    print(f"Phase376 RSC FAST->disable contract: PASS {path}")
PY

cp phase346-gki-out/config/final.config /tmp/p376-phase346.config
cp "$GDSC" /tmp/p376-gdsc-before.c
cp "$RSCV3" /tmp/p376-rscv3-before.c
cp "$HWC" /tmp/p376-hwc-before.c
cp "$CTRL" /tmp/p376-ctrl-before.c
cp "$REC" /tmp/p376-rec-before.c
cp "$COMPAT" /tmp/p376-compat-before.h

stage "apply TouchGrass MDSS HW-control parity"

python3 -m py_compile scripts/376_apply_mdss_gdsc_hwctrl_parity.py
python3 scripts/376_apply_mdss_gdsc_hwctrl_parity.py \
  --root "$ROOT" --touchgrass "$TG"
python3 scripts/376_apply_mdss_gdsc_hwctrl_parity.py \
  --root "$ROOT" --touchgrass "$TG" --check-only

! cmp -s /tmp/p376-gdsc-before.c "$GDSC"
cmp -s /tmp/p376-rscv3-before.c "$RSCV3"
cmp -s /tmp/p376-hwc-before.c "$HWC"
cmp -s /tmp/p376-ctrl-before.c "$CTRL"
cmp -s /tmp/p376-rec-before.c "$REC"
cmp -s /tmp/p376-compat-before.h "$COMPAT"

git diff --no-index --check /tmp/p376-gdsc-before.c "$GDSC" > /tmp/p376-gdsc-check 2>&1 || true
test ! -s /tmp/p376-gdsc-check
git diff --no-index /tmp/p376-gdsc-before.c "$GDSC" > /tmp/p376-gdsc.diff || true

stage "scope audit"

python3 - "$GDSC" <<'PY'
from pathlib import Path
import sys

before = Path("/tmp/p376-gdsc-before.c").read_text()
after = Path(sys.argv[1]).read_text()

start_s = "static int a52_legacy_gdsc_disable_mdss(struct regulator_dev *rdev)"
end_s = "static unsigned int a52_legacy_gdsc_get_mode"

def fn(text):
    start = text.index(start_s)
    end = text.index(end_s, start)
    return text[start:end]

b = fn(before)
a = fn(after)

if "A52_PHASE376_MDSS_GDSC_HWCTRL_PARITY_V1" not in a:
    raise SystemExit("Phase376 marker missing")
if "val &= ~A52_GDSC_HW_CONTROL;" not in b:
    raise SystemExit("Phase376 baseline did not clear HW_CONTROL")
if "val &= ~A52_GDSC_HW_CONTROL;" in a:
    raise SystemExit("Phase376 HW_CONTROL clear survived")
for token in (
    "val |= A52_GDSC_SW_COLLAPSE;",
    "ret = a52_legacy_gdsc_poll(gdsc, false, &val);",
    "A52GDSC disable profile=mdss",
):
    if token not in a:
        raise SystemExit("Phase376 inherited MDSS behavior missing: " + token)

expected = {
    "val &= ~A52_GDSC_HW_CONTROL;": -1,
    "writel_relaxed(val, gdsc->gdscr);": -1,
    "mb();": -1,
    "udelay(1);": -1,
}
for token, delta in expected.items():
    got = after.count(token) - before.count(token)
    if got != delta:
        raise SystemExit(
            f"Phase376 behavior delta mismatch {token!r}: {got} != {delta}"
        )

print("Phase376 one-variable MDSS HW-control scope audit: PASS")
PY

stage "config"

cp /tmp/p376-phase346.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" \
  ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- \
  LLVM=1 LLVM_IAS=1 \
  olddefconfig > phase376-olddefconfig.log 2>&1

cmp -s /tmp/p376-phase346.config "$BUILD/.config"

stage "compile"

set +e
make -C "$ROOT" O="$BUILD" \
  ARCH=arm64 \
  CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- \
  LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase376-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"
test -s "$SYSTEM_MAP"

grep -aFq 'A52GDSC disable profile=mdss name=%s rc=%d before=0x%x after=0x%x' "$IMAGE"
grep -aFq 'P276 345R %x %x %llx %x %x %x %x' "$IMAGE"
grep -Eq '[[:space:]]a52_p346_sideband_init$' "$SYSTEM_MAP"

stage "package"

rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}

cp "$IMAGE" "$OUT/compile/Image"
cp "$SYSTEM_MAP" "$OUT/compile/System.map"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase376-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/376_apply_mdss_gdsc_hwctrl_parity.py scripts/376_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p376-* "$OUT/audit/" 2>/dev/null || true
cp "$GDSC" "$RSCV3" "$HWC" "$CTRL" "$REC" "$COMPAT" "$OUT/source/"

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

root = Path("phase376-gki-out")

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

identity = {
    "phase": "376",
    "name": "MDSS-GDSC-HWCTRL-PARITY-V1",
    "base_phase": "346",
    "hardware_validated": False,
    "functional_change": (
        "mdss_core_gdsc disable preserves HW_CONTROL exactly as the "
        "TouchGrass gdsc-regulator ownership sequence requires"
    ),
    "rsc_v3_fast_then_disable_verified": True,
    "dsi_measurement_path_changed": False,
    "phase346_dma_raw_preserved": True,
    "proxy_consumer_behavior_changed": False,
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

cat > "$OUT/PHASE376-TEST.txt" <<'EOF'
Phase376 MDSS GDSC HW-control parity A/B
=========================================

Causal contract verified by CI:
  SDE RSC v3 sets mdss_core_gdsc to REGULATOR_MODE_FAST.
  It then drops the software regulator enable vote.
  TouchGrass gdsc_disable() preserves HW_CONTROL during that disable.
  Phase346 instead cleared HW_CONTROL before setting SW_COLLAPSE.

What Phase376 changes:
  Only a52_legacy_gdsc_disable_mdss().
  It no longer clears A52_GDSC_HW_CONTROL before SW_COLLAPSE.

What Phase376 does not change:
  SDE RSC source
  DSI controller/DMA source
  clocks / PHY / SMMU
  proxy-consumer behavior
  RPMh solver behavior
  RPMh flush behavior
  Phase346 raw DMA sideband

Runtime evidence already exists in the inherited provider:
  A52GDSC mode profile=mdss ... mode=1 ...
  A52GDSC disable profile=mdss ... before=... after=...

With Phase376, HW_CONTROL bit 1 should remain set across the disable handoff.
The inherited Phase346 F0 sideband remains the final DMA_DONE discriminator.
EOF

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage "complete"
echo "Phase376 MDSS GDSC HW-control parity build: PASS"
trap - EXIT
