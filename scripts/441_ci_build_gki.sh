#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
ROOT="$ROOT_DIR/gki/common"
BUILD="$ROOT_DIR/workspace/gki-phase199-out"
TG="$ROOT_DIR/workspace/touchgrass-a52xq"
OUT="$ROOT_DIR/phase441-gki-out"
AUDIT="$ROOT_DIR/phase441-uapi-audit"
SOURCE_BOOT="$ROOT_DIR/phase429-gki-out/package/boot-phase429-apex-sf-focused-census-v1.img"
FINAL="$OUT/package/boot-phase441g-bootanimation-oracle-v1.img"

stage(){ echo "== Phase441G: $* =="; }

stage "validate prerequisites"
test -d "$ROOT"
test -d "$TG"
test -s "$SOURCE_BOOT"
test -s phase429-gki-out/config/final.config
python3 -m py_compile scripts/441_apply_bootanimation_oracle.py scripts/441_graphics_uapi_audit.py
python3 scripts/429_apply_apex_sf_focused_census.py --root "$ROOT" --check-only

stage "prepare build directory"
if test -s "$BUILD/.config" && test -s "$BUILD/arch/arm64/boot/Image"; then
  echo "Reusable object tree: HIT"
else
  echo "Reusable object tree: MISS"
  mkdir -p "$BUILD"
  cp phase429-gki-out/config/final.config "$BUILD/.config"
  make -C "$ROOT" O="$BUILD" ARCH=arm64     CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu-     LLVM=1 LLVM_IAS=1 olddefconfig
fi

stage "reconstruct Phase440 source baseline"
python3 scripts/430_apply_sf_dma_forensics.py --root "$ROOT"
python3 scripts/430_postapply_hardening.py --root "$ROOT"
python3 scripts/431_apply_safe_gap_forensics.py --root "$ROOT"
python3 scripts/432_apply_sf_hwc_bootanim_chain.py --root "$ROOT"
python3 scripts/432ctrl_apply_odsign_keymint_qsee_trace.py --root "$ROOT"
python3 scripts/432ctrl_apply_odsign_keymint_qsee_trace.py --root "$ROOT" --check-only
python3 scripts/433_apply_cpu_mem_perf.py --root "$ROOT" --touchgrass "$TG"
"$ROOT/scripts/config" --file "$BUILD/.config"   -e ARM_QCOM_CPUFREQ_HW -e CPU_FREQ_DEFAULT_GOV_PERFORMANCE   -d CPU_FREQ_DEFAULT_GOV_SCHEDUTIL -e CPU_FREQ_GOV_PERFORMANCE   -e QCOM_BIMC_BWMON -e DEVFREQ_GOV_QCOM_BW_HWMON   -e ARM_MEMLAT_MON -e DEVFREQ_GOV_MEMLAT   -e ARM_QCOM_DEVFREQ_FW -e QCOM_DEVFREQ_DEVBW -e DUTVFREQ_SIMPLE_DEV
make -C "$ROOT" O="$BUILD" ARCH=arm64   CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu-   LLVM=1 LLVM_IAS=1 olddefconfig
python3 scripts/434_apply_clean_f0.py --root "$ROOT"
python3 scripts/435_apply_terminal_race.py --root "$ROOT" --overlap-report /tmp/p435-overlap.txt
python3 scripts/436_apply_adreno_start_frontier.py --root "$ROOT" --overlap-report /tmp/p436-overlap.txt
python3 scripts/437_apply_kgsl_open_corridor.py --root "$ROOT"
python3 scripts/438_apply_devbw_semantic_parity.py --root "$ROOT"
python3 scripts/439_apply_dsi_autopsy.py --root "$ROOT" --variant A
python3 scripts/440_apply_early_f0_cleanup_forensics.py --root "$ROOT"
python3 scripts/437_apply_kgsl_open_corridor.py --root "$ROOT" --check-only
python3 scripts/438_apply_devbw_semantic_parity.py --root "$ROOT" --check-only
python3 scripts/439_apply_dsi_autopsy.py --root "$ROOT" --variant A --check-only
python3 scripts/440_apply_early_f0_cleanup_forensics.py --root "$ROOT" --check-only

stage "static graphics UAPI/ABI audit"
rm -rf "$AUDIT"
python3 scripts/441_graphics_uapi_audit.py --gki "$ROOT" --touchgrass "$TG" --output "$AUDIT"
cat "$AUDIT/graphics-uapi-audit.json" | jq '{files,kgsl_layout_identical}'

stage "apply Phase441G"
python3 scripts/441_apply_bootanimation_oracle.py --root "$ROOT" --mode gki
python3 scripts/441_apply_bootanimation_oracle.py --root "$ROOT" --mode gki --check-only

# Phase441 parks the Phase440 synthetic early-F0 call.  Mark the now-dead
# helper explicitly unused so the 5.10 -Werror build does not reject it.
python3 - <<'PY'
from pathlib import Path
p = Path("gki/common/drivers/a52_display/msm/dsi/dsi_display.c")
s = p.read_text()
old = "static void a52_p440_schedule(struct dsi_display *display)"
new = "static __maybe_unused void a52_p440_schedule(struct dsi_display *display)"
n = s.count(old)
if n != 1:
    raise SystemExit(f"Phase441G expected one parked Phase440 helper, found {n}")
p.write_text(s.replace(old, new, 1))
PY
grep -Fq 'static __maybe_unused void a52_p440_schedule' "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
grep -Fq 'A52_PHASE441_BOOTANIMATION_GOLDEN_ORACLE_V1:gki' "$ROOT/arch/arm64/kernel/syscall.c"
grep -Fq 'Phase441: synthetic early F0 parked' "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"
! grep -Fq $'	a52_p440_schedule(display);' "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c"

stage "incremental compile"
make -C "$ROOT" O="$BUILD" ARCH=arm64   CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu-   LLVM=1 LLVM_IAS=1 -j"${JOBS:-$(nproc)}" Image
IMAGE="$BUILD/arch/arm64/boot/Image"
test -s "$IMAGE"
grep -aFq 'A52_PHASE441_BOOTANIMATION_GOLDEN_ORACLE_V1:gki' "$IMAGE"
grep -aFq 'a52_phase441' "$IMAGE"
grep -aFq 'P441 %.84s' "$IMAGE"
python3 scripts/441_apply_bootanimation_oracle.py --root "$ROOT" --mode gki --check-only
ccache -s || true

stage "package"
rm -rf "$OUT"
mkdir -p "$OUT"/{package,source,audit,config,compile}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/System.map" "$OUT/compile/System.map"
cp "$BUILD/.config" "$OUT/config/final.config"
cp -a "$AUDIT" "$OUT/audit/graphics-uapi"
cp scripts/441_apply_bootanimation_oracle.py scripts/441_apply_bootanimation_oracle.py.z64    scripts/441_graphics_uapi_audit.py scripts/441_graphics_uapi_audit.py.z64 "$OUT/audit/"
cp "$ROOT/arch/arm64/kernel/syscall.c" "$OUT/source/syscall.c"
cp "$ROOT/kernel/signal.c" "$OUT/source/signal.c"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/source/"
cp "$ROOT/drivers/a52_display/msm/dsi/dsi_display.c" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py   --source "$SOURCE_BOOT" --kernel "$OUT/package/Image.gz"   --output "$FINAL" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$FINAL")" -eq 100663296
cat > "$OUT/PHASE441-NOTES.txt" <<'NOTES'
Phase441G bootanimation oracle
==============================
Passive whole-TGID bootanimation syscall/crash recorder.
Captures failed pathname syscalls, successful /dev /sys /proc opens,
live-fd KGSL/dma_heap/ION/DRI ioctls with first 64 pre/post argument snapshots,
graphics mmap, fatal si_addr + PC/LR/SP + VMA file offset.
Phase440 synthetic early F0 is parked; natural Android F0/P439 autopsy is untouched.
Power forensics runs through 130 s, includes iommu names, and retains every genpd transition.
NOTES
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

echo "Phase441G GKI bootanimation oracle: PASS"
