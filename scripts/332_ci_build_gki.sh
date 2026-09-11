#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase332-gki-out"
FAIL="$PWD/phase332-gki-failure"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
STAGE=startup

stage() { STAGE="$1"; echo "== Phase332 stage: $STAGE =="; }
fail_report() {
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase332-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p332-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/332_apply_persistent_timeout_frontier_recorder.py scripts/332_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$DISP"; do [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase331"
bash scripts/331_ci_build_gki.sh 2>&1 | tee phase332-phase331.log
for f in phase331-gki-out/package/boot.img phase331-gki-out/compile/Image phase331-gki-out/config/final.config "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$DISP"; do test -s "$f"; done
test "$(stat -c '%s' phase331-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE331_POST_RETENTION_FRONTIER_RECORDER_V1' "$CTRL"
grep -Fq 'P276 331F q=2 dump=0' "$CTRL"
grep -Fq "fmt[7] == '1'" "$REC"

cp phase331-gki-out/config/final.config /tmp/p332-phase331.config
for spec in ctrl:$CTRL hwc:$HWC phy:$PHY phyv3:$PHYV3 rec:$REC disp:$DISP; do
  n=${spec%%:*}; f=${spec#*:}; cp "$f" "/tmp/p332-${n}-before"
done

stage "apply persistent exact-F0 timeout frontier"
python3 -m py_compile scripts/332_apply_persistent_timeout_frontier_recorder.py
python3 scripts/332_apply_persistent_timeout_frontier_recorder.py --root "$ROOT"
python3 scripts/332_apply_persistent_timeout_frontier_recorder.py --root "$ROOT" --check-only

cmp -s /tmp/p332-hwc-before "$HWC"
cmp -s /tmp/p332-phy-before "$PHY"
cmp -s /tmp/p332-phyv3-before "$PHYV3"
cmp -s /tmp/p332-disp-before "$DISP"
! cmp -s /tmp/p332-ctrl-before "$CTRL"
! cmp -s /tmp/p332-rec-before "$REC"
diff -u /tmp/p332-ctrl-before "$CTRL" > /tmp/p332-ctrl.diff || true
diff -u /tmp/p332-rec-before "$REC" > /tmp/p332-rec.diff || true
git -C "$ROOT" diff --check -- drivers/a52_display/msm/dsi/dsi_ctrl.c drivers/a52_secure/a52_ack_secure_flight_recorder.c

stage "strict Phase332 diagnostic-only scope audit"
python3 - "$CTRL" "$REC" <<'PY'
from pathlib import Path
import sys
bc=Path('/tmp/p332-ctrl-before').read_text(); ac=Path(sys.argv[1]).read_text()
br=Path('/tmp/p332-rec-before').read_text(); ar=Path(sys.argv[2]).read_text()
protected=(
 'DSI_W32(', 'writel_relaxed(', 'writel(', 'regmap_write(', 'regmap_update_bits(',
 'wmb(', 'mb(', 'rmb(', 'readl_poll_timeout', 'wait_for_completion_timeout(',
 'udelay(', 'ndelay(', 'usleep_range(', 'msleep(', 'clk_set_rate(', 'clk_set_parent(',
 'clk_prepare_enable(', 'clk_disable_unprepare(', 'regulator_enable(', 'regulator_disable(',
 'reset_control_assert(', 'reset_control_deassert(', 'SDE_DBG_DUMP(',
 'dsi_ctrl_disable_status_interrupt(', 'clear_interrupt_status(', 'gpio_get_value(', 'ss_get_vdd(',
)
for t in protected:
    if bc.count(t) != ac.count(t):
        raise SystemExit(f'Phase332 functional-scope violation {t}: {bc.count(t)} -> {ac.count(t)}')
if ac.count('a52_ackfr_retain_timeout_snapshot();') != bc.count('a52_ackfr_retain_timeout_snapshot();'):
    raise SystemExit('Phase332 changed retention call count')
if ar.count('atomic_set(&a52_r280_retained, 1);') != br.count('atomic_set(&a52_r280_retained, 1);'):
    raise SystemExit('Phase332 changed retention setter')
if ar.count('a52_r280_retained') != br.count('a52_r280_retained'):
    raise SystemExit('Phase332 changed retention-latch reference count')
if ac.count('a52_p293_gdm_armed(dsi_ctrl)') <= bc.count('a52_p293_gdm_armed(dsi_ctrl)'):
    raise SystemExit('Phase332 did not move timeout-frontier gating to persistent GDM arm')
for t in (
 'P276 332A q=2 g=1 d=%u st=%x m=%x','P276 332B q=2 retained=1',
 'P276 332C q=2 b=1 st=%x','P276 332D q=2 b=1 st=%x','P276 332C q=2 b=0 st=%x',
 'P276 332E q=2 ss=1 v=%u','P276 332F q=2 gpio=1','P276 332G q=2 dump=0',
 'P276 332H q=2 dump=1','P276 332I q=2 b=0','P276 332J q=2 dis=0','P276 332K q=2 dis=1'):
    if t not in ac: raise SystemExit('Phase332 marker missing: '+t)
if "fmt[7] == '1' || fmt[7] == '2'" not in ar:
    raise SystemExit('Phase332 recorder post-retention namespace missing')
print('Phase332 persistent-GDM diagnostic scope audit: PASS')
PY

stage "config invariant"
cp /tmp/p332-phase331.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase332-olddefconfig.log 2>&1
cmp -s /tmp/p332-phase331.config "$BUILD/.config"

stage "compile incremental Phase332 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase332-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for m in \
 'P276 332A q=2 g=1 d=%u st=%x m=%x' \
 'P276 332B q=2 retained=1' \
 'P276 332C q=2 b=1 st=%x' \
 'P276 332D q=2 b=1 st=%x' \
 'P276 332C q=2 b=0 st=%x' \
 'P276 332E q=2 ss=1 v=%u' \
 'P276 332F q=2 gpio=1' \
 'P276 332G q=2 dump=0' \
 'P276 332H q=2 dump=1' \
 'P276 332I q=2 b=0' \
 'P276 332J q=2 dis=0' \
 'P276 332K q=2 dis=1' \
 'P276 330D q=%u c=%x z=%x' \
 'P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x' \
 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' \
 'P276 280Z q=2'; do grep -aFq "$m" "$IMAGE"; done

stage "package evidence and boot image"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase332-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/332_apply_persistent_timeout_frontier_recorder.py scripts/332_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p332-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HWC" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase331-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase331-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE331-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase332-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
idn={
 'phase':'332','flavor':'gki','name':'PERSISTENT-GDM-TIMEOUT-FRONTIER-V1',
 'git_sha':os.getenv('GITHUB_SHA'),'hardware_validated':False,'base_phase':'331',
 'phase331_hardware_finding':'The retained R48 ring continued with ordinary P269/P270/BOOTPOST records through seq 6264 and contained no P276 331* or P276 280Z. Therefore the Phase280/331 timeout retention gate did not fire in that boot.',
 'root_cause_of_phase331_blind_spot':'a52_p276r_deep_active() is task-PID scoped only while TX_LEVEL1_KEY_ENABLE is inside ops->transfer(); the timeout completion may execute outside that task context. The exact-F0 a52_p293_gdm_state is a persistent atomic latch and survives task/workqueue boundaries.',
 'experiment':'gate timeout retention and frontier breadcrumbs on a52_p293_gdm_armed(dsi_ctrl); record the transient deep-active bit in 332A; preserve all DSI/MMIO/clock/PHY/reset/regulator/timing behavior.',
 'display_control_flow_changed':False,'dsi_mmio_changed':False,'clock_phy_reset_regulator_changed':False,
 'retention_behavior':'first exact-F0 armed timeout freezes ordinary records; P276 331* and P276 332* remain admitted after the freeze.',
 'image_sha256':sha(r/'compile/Image'),'boot_img_sha256':sha(r/'package/boot.img'),'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage complete
echo 'Phase332 persistent exact-F0 timeout frontier recorder: PASS'
trap - EXIT
