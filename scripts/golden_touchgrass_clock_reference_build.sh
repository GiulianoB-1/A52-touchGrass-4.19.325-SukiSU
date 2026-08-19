#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/golden-clock-ref-out"
FAIL="$ROOT/golden-clock-ref-failure"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
rm -rf "$OUT" "$FAIL"
mkdir -p "$OUT/audit" "$FAIL"

on_err() {
  rc=$?
  {
    echo "rc=$rc"
    date -u
    git status --short || true
    if [ -d "$KERNEL" ]; then
      git -C "$KERNEL" status --short || true
      git -C "$KERNEL" diff --stat || true
      git -C "$KERNEL" diff --check || true
    fi
  } > "$FAIL/diagnostics.txt" 2>&1
  exit "$rc"
}
trap on_err ERR

chmod +x scripts/*.sh scripts/*.py

# Reproduce the exact reviewed 4.19.200 sequence used by workflow run 29199421254.
./scripts/01_prepare_source.sh
./scripts/03_apply_linux_4.19.153.sh
./scripts/04_apply_linux_4.19.154.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.154 4.19.159
./scripts/checkpoint_resolve_linux_4.19.159.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.159 4.19.164
./scripts/checkpoint_resolve_linux_4.19.164.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.164 4.19.180
./scripts/checkpoint_resolve_linux_4.19.180.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.180 4.19.200
./scripts/checkpoint_resolve_linux_4.19.200.sh

# Freeze the pre-instrumentation display files for audit.
cp "$KERNEL/techpack/display/msm/dsi/dsi_clk_manager.c" "$OUT/audit/dsi_clk_manager-before.c"
cp "$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c" "$OUT/audit/dsi_ctrl-before.c"

python3 scripts/golden_touchgrass_clock_reference.py --root "$KERNEL"
python3 scripts/golden_touchgrass_clock_reference.py --root "$KERNEL" --check-only

git -C "$KERNEL" diff --check

grep -Fq 'A52_GOLDEN_TOUCHGRASS_CLOCK_REFERENCE_V1' "$KERNEL/techpack/display/msm/dsi/dsi_clk_manager.c"
grep -Fq 'TGREF SKIP' "$KERNEL/techpack/display/msm/dsi/dsi_clk_manager.c"
grep -Fq 'TGREF CMD PRE' "$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c"
grep -Fq 'TGREF CMD POST' "$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c"

./scripts/07_patch_resukisu_exec_hook.sh

set -o pipefail
./scripts/08_build_resukisu_safe_checkpoint.sh 4.19.200 \
  2>&1 | tee "$OUT/golden-clock-reference-build.log"

IMAGE="$(find artifacts -maxdepth 1 -type f -name 'Image-touchgrass-4.19.200-resukisu-v4.1.0-safe' -print -quit)"
CONFIG="$(find artifacts -maxdepth 1 -type f -name 'config-touchgrass-4.19.200-resukisu-v4.1.0-safe' -print -quit)"
test -n "$IMAGE" -a -f "$IMAGE"
test -n "$CONFIG" -a -f "$CONFIG"

cp "$IMAGE" "$OUT/Image"
cp "$CONFIG" "$OUT/config"
cp "$KERNEL/techpack/display/msm/dsi/dsi_clk_manager.c" "$OUT/audit/dsi_clk_manager-after.c"
cp "$KERNEL/techpack/display/msm/dsi/dsi_ctrl.c" "$OUT/audit/dsi_ctrl-after.c"
cp scripts/golden_touchgrass_clock_reference.py "$OUT/audit/"

strings "$OUT/Image" > "$OUT/Image.strings.txt"
for marker in \
  'TGREF CACHE' 'TGREF SETP' 'TGREF SETB' 'TGREF APPLY0' \
  'TGREF SKIP' 'TGREF APPLY1' 'TGREF SPLASH' \
  'TGREF CMD PRE' 'TGREF CMD POST'; do
  grep -Fq "$marker" "$OUT/Image.strings.txt"
done

grep -Fq 'Linux version 4.19.200-touchGrassKernel+' "$OUT/Image.strings.txt"

sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
cat > "$OUT/BUILD-IDENTITY.txt" <<EOF
experiment=GOLDEN-TOUCHGRASS-CLOCK-REFERENCE
behavior_change=none-read-only-pr_info-only
base_project_commit=d6f75d100f0307866938c386347732b8a776c97e
touchgrass_base=6bf351bdf18bdb228db79e66f14a7a9c0178e5d7
kernel_version=4.19.200-touchGrassKernel+
reference_workflow_run=29199421254
reference_runtime_build_time=2026-07-12T16:13:54Z
markers=TGREF_CACHE,TGREF_SETP,TGREF_SETB,TGREF_APPLY0,TGREF_SKIP,TGREF_APPLY1,TGREF_SPLASH,TGREF_CMD_PRE,TGREF_CMD_POST
flashable=no-image-only-until-template-repack
EOF

echo "Golden TouchGrass clock reference build passed"
