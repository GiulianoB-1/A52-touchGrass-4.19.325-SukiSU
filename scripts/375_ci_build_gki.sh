#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
TG="$PWD/workspace/touchgrass-a52xq"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase375-gki-out"
FAIL="$PWD/phase375-gki-failure"
INTERNAL="$ROOT/drivers/soc/qcom/rpmh-internal.h"
RSC="$ROOT/drivers/soc/qcom/rpmh-rsc.c"
RPMH="$ROOT/drivers/soc/qcom/rpmh.c"
COMPAT="$ROOT/a52-port-compat.h"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"

STAGE=startup
stage() {
  STAGE="$1"
  echo "== Phase375: $STAGE =="
}

fail_report() {
  set +e
  rm -rf "$FAIL"
  mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase375-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p375-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/375_apply_rpmh_solver_parity.py scripts/375_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$INTERNAL" "$RSC" "$RPMH" "$COMPAT" "$HWC" "$CTRL" "$REC"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}

trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact Phase346"
bash scripts/346_ci_build_gki.sh 2>&1 | tee phase375-phase346.log

for f in   phase346-gki-out/package/boot.img   phase346-gki-out/compile/Image   phase346-gki-out/compile/System.map   phase346-gki-out/config/final.config   "$INTERNAL" "$RSC" "$RPMH" "$COMPAT" "$HWC" "$CTRL" "$REC"; do
  test -s "$f"
done

test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' "$HWC"
grep -Fq 'A52_PHASE305_DISPLAY_RPMH_FLUSH_COMPAT_V1' "$RSC"
grep -Fxq '#define rpmh_mode_solver_set(d,e) do{}while(0)' "$COMPAT"
grep -Fxq '#define rpmh_flush(d) a52_rpmh_flush_compat((d))' "$COMPAT"
! grep -Fq 'A52_PHASE375_RPMH_SOLVER_PARITY_V1' "$RSC"

cp phase346-gki-out/config/final.config /tmp/p375-phase346.config
cp "$INTERNAL" /tmp/p375-internal-before.h
cp "$RSC" /tmp/p375-rsc-before.c
cp "$RPMH" /tmp/p375-rpmh-before.c
cp "$COMPAT" /tmp/p375-compat-before.h
cp "$HWC" /tmp/p375-hwc-before.c
cp "$CTRL" /tmp/p375-ctrl-before.c
cp "$REC" /tmp/p375-rec-before.c

stage "apply RPMh solver parity"
python3 -m py_compile scripts/375_apply_rpmh_solver_parity.py
python3 scripts/375_apply_rpmh_solver_parity.py   --root "$ROOT" --touchgrass "$TG"
python3 scripts/375_apply_rpmh_solver_parity.py   --root "$ROOT" --touchgrass "$TG" --check-only

! cmp -s /tmp/p375-internal-before.h "$INTERNAL"
! cmp -s /tmp/p375-rsc-before.c "$RSC"
! cmp -s /tmp/p375-rpmh-before.c "$RPMH"
! cmp -s /tmp/p375-compat-before.h "$COMPAT"
cmp -s /tmp/p375-hwc-before.c "$HWC"
cmp -s /tmp/p375-ctrl-before.c "$CTRL"
cmp -s /tmp/p375-rec-before.c "$REC"

for pair in   "/tmp/p375-internal-before.h:$INTERNAL"   "/tmp/p375-rsc-before.c:$RSC"   "/tmp/p375-rpmh-before.c:$RPMH"   "/tmp/p375-compat-before.h:$COMPAT"; do
  before="${pair%%:*}"
  after="${pair#*:}"
  git diff --no-index --check "$before" "$after" > "/tmp/p375-$(basename "$after").check" 2>&1 || true
  test ! -s "/tmp/p375-$(basename "$after").check"
  git diff --no-index "$before" "$after" > "/tmp/p375-$(basename "$after").diff" || true
done

stage "scope audit"
python3 - "$INTERNAL" "$RSC" "$RPMH" "$COMPAT" <<'PY'
from pathlib import Path
import sys

internal, rsc, rpmh, compat = [Path(x).read_text() for x in sys.argv[1:]]
bi = Path("/tmp/p375-internal-before.h").read_text()
br = Path("/tmp/p375-rsc-before.c").read_text()
bp = Path("/tmp/p375-rpmh-before.c").read_text()
bc = Path("/tmp/p375-compat-before.h").read_text()

required = (
    "A52_PHASE375_RPMH_SOLVER_PARITY_V1",
    "bool in_solver_mode;",
    "a52_rpmh_rsc_mode_solver_set",
    "a52_rpmh_mode_solver_set_compat",
    "if (unlikely(drv->in_solver_mode))",
    "P375 SG reject active",
    "P375 SG set e=%u spins=%u",
)
joined = internal + rsc + rpmh + compat
for token in required:
    if token not in joined:
        raise SystemExit("Phase375 missing intended token: " + token)

if "#define rpmh_mode_solver_set(d,e) do{}while(0)" in compat:
    raise SystemExit("Phase375 solver no-op survived")
if "#define rpmh_flush(d) a52_rpmh_flush_compat((d))" not in compat:
    raise SystemExit("Phase375 changed the Phase305 flush repair")

expected = {
    "bool in_solver_mode;": 1,
    "P375 SG reject active": 1,
    "P375 SG set e=%u spins=%u": 1,
    "a52_rpmh_mode_solver_set_compat((d), (e))": 1,
}
for token, delta in expected.items():
    before = (bi + br + bp + bc).count(token)
    after = joined.count(token)
    if after - before != delta:
        raise SystemExit(
            f"Phase375 delta mismatch {token!r}: {after-before} != {delta}"
        )

# Solver parity only. Do not alter display MMIO, clocks, PHY, SMMU, regulators,
# MSM-bus algorithms, delays, or the already repaired rpmh_flush wrapper.
for token in (
    "DSI_W32(", "DSI_R32(", "clk_set_rate(", "clk_prepare_enable(",
    "regulator_enable(", "regulator_disable(", "msm_bus_scale_client_update_request(",
    "reset_control_", "udelay(", "usleep_range(", "msleep(",
):
    before = (bi + br + bp + bc).count(token)
    after = joined.count(token)
    if after != before:
        raise SystemExit("Phase375 unexpected primitive drift: " + token)

print("Phase375 one-variable solver parity scope audit: PASS")
PY

stage "config"
cp /tmp/p375-phase346.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD"   ARCH=arm64   CROSS_COMPILE=aarch64-linux-gnu-   CLANG_TRIPLE=aarch64-linux-gnu-   LLVM=1 LLVM_IAS=1   olddefconfig > phase375-olddefconfig.log 2>&1
cmp -s /tmp/p375-phase346.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD"   ARCH=arm64   CROSS_COMPILE=aarch64-linux-gnu-   CLANG_TRIPLE=aarch64-linux-gnu-   LLVM=1 LLVM_IAS=1   -j"$(nproc)" Image 2>&1 | tee phase375-compile.log
rc=${PIPESTATUS[0]}
set -e
test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"
test -s "$SYSTEM_MAP"

for marker in   'P375 SG reject active'   'P375 SG set e=%u spins=%u'   'P276 345R %x %x %llx %x %x %x %x'   'P276 305F x r=%d l=%u b=%u'; do
  grep -aFq "$marker" "$IMAGE"
done

grep -Eq '[[:space:]]a52_rpmh_mode_solver_set_compat$' "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_rpmh_rsc_mode_solver_set$' "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p346_sideband_init$' "$SYSTEM_MAP"

stage "package"
rm -rf "$OUT"
mkdir -p "$OUT"/{compile,config,package,audit,source}

cp "$IMAGE" "$OUT/compile/Image"
cp "$SYSTEM_MAP" "$OUT/compile/System.map"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase375-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/375_apply_rpmh_solver_parity.py scripts/375_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p375-* "$OUT/audit/" 2>/dev/null || true
cp "$INTERNAL" "$RSC" "$RPMH" "$COMPAT" "$HWC" "$CTRL" "$REC" "$OUT/source/"

gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py   --source phase346-gki-out/package/boot.img   --kernel "$OUT/package/Image.gz"   --output "$OUT/package/boot.img"   --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

python3 - <<'PY'
from pathlib import Path
import hashlib
import json
import os

root = Path("phase375-gki-out")

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

identity = {
    "phase": "375",
    "name": "RPMH-SOLVER-PARITY-V1",
    "base_phase": "346",
    "base_commit": "445fe7398a96b36633762a3e748cab9128eacec6a".replace(
        "e748cab9128eacec6a", "e21ae92d8ee71f0"
    ),
    "hardware_validated": False,
    "functional_change": (
        "restore the live SDE-RSC rpmh_mode_solver_set contract on the display "
        "RPMh controller and reject active RPMh transfers while solver mode is active"
    ),
    "design": (
        "single rsc_drv solver state with 5.10 lock-order-safe setter; "
        "no duplicate rpmh_ctrlr solver lock"
    ),
    "dsi_measurement_path_changed": False,
    "phase346_dma_raw_preserved": True,
    "mdss_gdsc_proxy_changed": False,
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

cat > "$OUT/PHASE375-TEST.txt" <<'EOF'
Phase375 RPMh solver parity A/B
===============================

Why:
  TouchGrass SDE-RSC toggles rpmh_mode_solver_set() after hardware RSC state
  transitions. Phase13 erased every call with a no-op macro. Phase305 repaired
  rpmh_flush() only and intentionally left solver-mode behavior untouched.

What changes:
  - one solver-state bit is restored in the native 5.10 rsc_drv
  - SDE-RSC rpmh_mode_solver_set() calls now reach a real compatibility bridge
  - enabling solver waits for the display controller's active/borrowed TCS to idle
  - active RPMh sends are rejected while solver mode is active
  - a pre-cache check avoids recording normal active votes while already in solver

What does NOT change:
  MDSS GDSC proxy-consumer behavior
  DSI controller / DMA source
  clocks / PHY / SMMU
  MSM-bus algorithms
  Phase305 rpmh_flush bridge
  Phase346 raw sideband

Expected runtime proof:
  P375 SG set e=1 spins=<...>
  P375 SG set e=0 spins=<...>
  P375 SG reject active        (only if a client tries an active vote in solver)

Test independently from Phase374. If either image changes the F0/DMA_DONE
fingerprint, we have isolated two different pre-trigger dependency contracts.
EOF

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)

stage "complete"
echo "Phase375 RPMh solver parity build: PASS"
trap - EXIT
