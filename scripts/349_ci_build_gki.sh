#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase349-gki-out"
FAIL="$PWD/phase349-gki-failure"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
DRV="$ROOT/drivers/a52_display/msm/msm_drv.c"
ATOMIC="$ROOT/drivers/a52_display/msm/msm_atomic.c"
KMS="$ROOT/drivers/a52_display/msm/sde/sde_kms.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase349: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase349-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p349-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/349_apply_drm_frontier.py scripts/349_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$HWC" "$CTRL" "$DRV" "$ATOMIC" "$KMS"; do
    [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true
  done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE349_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase349: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase349-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$HWC" "$CTRL" "$DRV" "$ATOMIC" "$KMS"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296

cp phase346-gki-out/config/final.config /tmp/p349-base.config
for item in hwc:"$HWC" ctrl:"$CTRL" drv:"$DRV" atomic:"$ATOMIC" kms:"$KMS"; do
  name="${item%%:*}"; src="${item#*:}"; cp "$src" "/tmp/p349-${name}-before"
done

stage "apply DRM frontier"
python3 -m py_compile scripts/349_apply_drm_frontier.py
python3 scripts/349_apply_drm_frontier.py --root "$ROOT"
python3 scripts/349_apply_drm_frontier.py --root "$ROOT" --check-only

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
root=Path("gki/common")
files=[
 root/"drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c",
 root/"drivers/a52_display/msm/dsi/dsi_ctrl.c",
 root/"drivers/a52_display/msm/msm_drv.c",
 root/"drivers/a52_display/msm/msm_atomic.c",
 root/"drivers/a52_display/msm/sde/sde_kms.c",
]
joined="\n".join(p.read_text() for p in files)
for token in (
 "A52_PHASE349_DRM_FRONTIER_V1",
 "A52_P349_MAGIC              0x393433544e524644ULL",
 "A52_P349_COMMIT             0x349c0de5U",
 "a52_p349_mark(1U",
 "a52_p349_mark(3U",
 "a52_p349_mark(5U",
 "a52_p349_mark(6U",
 "a52_p349_mark(7U",
 "a52_p349_mark(8U",
 "a52_p349_mark(9U",
):
 if token not in joined:
  raise SystemExit("Phase349 scope token missing: "+token)
print("Phase349 scope audit: PASS")
PY

stage "config"
cp /tmp/p349-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase349-olddefconfig.log 2>&1
cmp -s /tmp/p349-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase349-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p349_mark$' "$SYSTEM_MAP"
grep -aFq 'P276 296A e' "$IMAGE"
grep -aFq 'P276 296C e n=%d' "$IMAGE"

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase349-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/349_apply_drm_frontier.py scripts/349_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p349-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$DRV" "$ATOMIC" "$KMS" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE349-SCHEMA.txt" <<'EOF'
Phase349 DRM frontier
=====================
Base: Phase346.

Raw physical base: 0xB1BFA000
copy A: +0x0000
copy B: +0x1000

Phase349 uses +0x0800 in each copy.
10 events, two 64-byte replicas/event per copy = four physical copies/event.

slot:
 u64 magic
 u64 ns
 u32 event
 u32 seq
 u32 pid
 u32 tgid
 u32 cpu
 u32 arg0
 u32 arg1
 u32 arg2
 u32 arg3
 u32 state
 u32 commit
 u32 version

magic=0x393433544e524644
commit=0x349c0de5
version=1

events:
 0 INIT
 1 DRM_OPEN_ENTER
 2 DRM_OPEN_EXIT       arg0=rc
 3 ATOMIC_CHECK_ENTER  arg0=num_connector
 4 ATOMIC_CHECK_EXIT   arg0=rc
 5 ATOMIC_COMMIT_ENTER arg0=nonblock
 6 COMMIT_TAIL_ENTER
 7 SDE_PREPARE_ENTER
 8 SDE_COMMIT_ENTER
 9 DSI_MSG_ENTER       arg0=type arg1=len arg2=msg_flags arg3=ctrl_flags

Only the first occurrence of each event is persisted.
EOF

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path("phase349-gki-out")
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 "phase":"349","name":"DRM-FRONTIER-V1","base_phase":"346",
 "hardware_validated":False,
 "sideband_phys":"0xB1BFA000","base_offset":"0x800",
 "event_count":10,"replicas_per_mirror":2,"slot_bytes":64,
 "boot_img_size":(r/"package/boot.img").stat().st_size,
 "boot_img_sha256":sha(r/"package/boot.img"),
 "image_sha256":sha(r/"compile/Image"),
 "git_sha":os.getenv("GITHUB_SHA"),
}
(r/"BUILD-IDENTITY.json").write_text(json.dumps(ident,indent=2,sort_keys=True)+"\n")
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage "complete"
echo "Phase349 DRM frontier build: PASS"
trap - EXIT
