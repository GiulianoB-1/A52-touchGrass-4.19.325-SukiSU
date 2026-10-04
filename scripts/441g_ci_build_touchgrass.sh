#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
OUT="$ROOT/phase441-tg-golden-out"
FAIL="$ROOT/phase441-tg-golden-failure"
STAGE=startup

stage(){ STAGE="$1"; echo "== Phase441-TG stage: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase441-tg-*.log "$FAIL/logs/" 2>/dev/null || true
  cp scripts/441_apply_bootanimation_oracle.py scripts/441_apply_bootanimation_oracle.py.z64 "$FAIL/audit/" 2>/dev/null || true
  [ -f "$KERNEL/arch/arm64/kernel/syscall.c" ] && cp "$KERNEL/arch/arm64/kernel/syscall.c" "$FAIL/source/" || true
  [ -f "$KERNEL/kernel/signal.c" ] && cp "$KERNEL/kernel/signal.c" "$FAIL/source/" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact clean TouchGrass 4.19.200 Golden base"
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
./scripts/07_patch_resukisu_exec_hook.sh

stage "apply mirrored Phase441 bootanimation oracle"
python3 -m py_compile scripts/441_apply_bootanimation_oracle.py
python3 scripts/441_apply_bootanimation_oracle.py --root "$KERNEL" --mode golden
python3 scripts/441_apply_bootanimation_oracle.py --root "$KERNEL" --mode golden --check-only
git -C "$KERNEL" diff --check -- arch/arm64/kernel/syscall.c kernel/signal.c

stage "strict passive-scope audit"
grep -Fq 'A52_PHASE441_BOOTANIMATION_GOLDEN_ORACLE_V1:golden' "$KERNEL/arch/arm64/kernel/syscall.c"
grep -Fq 'proc_create("a52_phase441"' "$KERNEL/arch/arm64/kernel/syscall.c"
grep -Fq 'a52_p441_fatal_signal(signr' "$KERNEL/kernel/signal.c"
! grep -R -Fq 'a52_p440_schedule(display)' "$KERNEL" || true

stage "build TouchGrass Golden"
set -o pipefail
bash -lc 'source scripts/common.sh; build_kernel "touchgrass-4.19.200-resukisu-v4.1.0-safe"' \
  2>&1 | tee phase441-tg-build.log

IMAGE="$ROOT/artifacts/Image-touchgrass-4.19.200-resukisu-v4.1.0-safe"
CONFIG="$ROOT/artifacts/config-touchgrass-4.19.200-resukisu-v4.1.0-safe"
test -s "$IMAGE" -a -s "$CONFIG"
grep -aFq 'A52_PHASE441_BOOTANIMATION_GOLDEN_ORACLE_V1:golden' "$IMAGE"
grep -aFq 'a52_phase441' "$IMAGE"
grep -aFq 'Linux version 4.19.200-touchGrassKernel+' "$IMAGE"

stage "assemble Phase441 Golden evidence"
rm -rf "$OUT"; mkdir -p "$OUT"/{audit,source,package}
cp "$IMAGE" "$OUT/Image"
cp "$CONFIG" "$OUT/config"
cp phase441-tg-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/441_apply_bootanimation_oracle.py scripts/441_apply_bootanimation_oracle.py.z64 "$OUT/audit/"
cp "$KERNEL/arch/arm64/kernel/syscall.c" "$OUT/source/syscall.c"
cp "$KERNEL/kernel/signal.c" "$OUT/source/signal.c"
cat > "$OUT/BUILD-IDENTITY.txt" <<'EOF'
experiment=PHASE441-TG-GOLDEN-BOOTANIMATION-ORACLE-V1
base=clean-TouchGrass-4.19.200-Golden-reconstruction
touchgrass_base=6bf351bdf18bdb228db79e66f14a7a9c0178e5d7
kernel_version=4.19.200-touchGrassKernel+
behavior_change=none
trace_scope=bootanimation-whole-TGID
trace_anchor=first-syscall-after-bootanimation-exec
trace_window=event-driven
proc=/proc/a52_phase441
ioctl_generic_cap=150
ioctl_detail_cap=64
ioctl_detail_bytes=32-pre-plus-32-post
binder_ioctl_stream=excluded
fatal=signal-si_code-si_addr-PC-LR-SP-plus-VMA-file-offset
EOF
sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
echo "Phase441 TouchGrass Golden bootanimation oracle: PASS"
