#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase331-gki-out"
FAIL="$PWD/phase331-gki-failure"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
DISP="$ROOT/drivers/clk/qcom/dispcc-lagoon.c"
STAGE=startup

stage() { STAGE="$1"; echo "== Phase331 stage: $STAGE =="; }
fail_report() {
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase331-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p331-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/331_apply_post_retention_frontier_recorder.py scripts/331_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$DISP"; do [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "install Phase337 retention-safe Phase206 reconstruction helper"
test -s scripts/337_reconstruct_phase206_artifact_free.sh
grep -Fq 'A52_PHASE337_ARTIFACT_FREE_PHASE206_REPLAY_V1' \
  scripts/337_reconstruct_phase206_artifact_free.sh
bash -n scripts/337_reconstruct_phase206_artifact_free.sh
cp scripts/337_reconstruct_phase206_artifact_free.sh \
  scripts/319_reconstruct_phase206_from_verified_artifact.sh
chmod +x scripts/319_reconstruct_phase206_from_verified_artifact.sh
cmp -s scripts/337_reconstruct_phase206_artifact_free.sh \
  scripts/319_reconstruct_phase206_from_verified_artifact.sh

stage "adapt Phase328 helper to pinned GKI"
python3 - <<'PY'
from pathlib import Path
p = Path('scripts/328_apply_full_touchgrass_display_clock_port.py')
s = p.read_text()
start = '    a="static u8 clk_rcg2_get_parent(struct clk_hw *hw)\\n"\n    s=one(s,a,r\'\'\'static int clk_rcg2_set_force_enable(struct clk_hw *hw)'
end = '\'\'\'+a,"force")\n'
i = s.find(start)
if i < 0:
    raise SystemExit('Phase331 Phase328 helper-adaptation start anchor missing')
j = s.find(end, i)
if j < 0:
    raise SystemExit('Phase331 Phase328 helper-adaptation end anchor missing')
j += len(end)
replacement = '''    # Pinned GKI already provides clk_rcg2_set_force_enable() and
    # clk_rcg2_clear_force_enable() for shared RCG ops. Reuse those helpers.
'''
s = s[:i] + replacement + s[j:]
life = "    life=r'''static int clk_rcg2_enable(struct clk_hw *hw)\n"
protos = "    life=r'''static int clk_rcg2_set_force_enable(struct clk_hw *hw);\nstatic int clk_rcg2_clear_force_enable(struct clk_hw *hw);\n\nstatic int clk_rcg2_enable(struct clk_hw *hw)\n"
if s.count(life) != 1:
    raise SystemExit(f'Phase331 Phase328 lifecycle anchor count {s.count(life)}')
s = s.replace(life, protos, 1)
p.write_text(s)
PY
python3 -m py_compile scripts/328_apply_full_touchgrass_display_clock_port.py

test "$(grep -c '^static int clk_rcg2_set_force_enable' scripts/328_apply_full_touchgrass_display_clock_port.py || true)" -eq 0
grep -Fq "static int clk_rcg2_set_force_enable(struct clk_hw *hw);" scripts/328_apply_full_touchgrass_display_clock_port.py
grep -Fq "static int clk_rcg2_clear_force_enable(struct clk_hw *hw);" scripts/328_apply_full_touchgrass_display_clock_port.py

stage "reconstruct exact Phase330"
bash scripts/330_ci_build_gki.sh 2>&1 | tee phase331-phase330.log
for f in phase330-gki-out/package/boot.img phase330-gki-out/compile/Image phase330-gki-out/config/final.config "$CTRL" "$HWC" "$PHY" "$PHYV3" "$REC" "$DISP"; do test -s "$f"; done
test "$(stat -c '%s' phase330-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'P276 330D q=%u c=%x z=%x' "$HWC"
grep -Fq 'P276 280Z q=2' "$CTRL"
grep -Fq 'a52_ackfr_retain_timeout_snapshot();' "$CTRL"
grep -Fq 'a52_r280_retained' "$REC"

cp phase330-gki-out/config/final.config /tmp/p331-phase330.config
for spec in ctrl:$CTRL hwc:$HWC phy:$PHY phyv3:$PHYV3 rec:$REC disp:$DISP; do
  n=${spec%%:*}; f=${spec#*:}; cp "$f" "/tmp/p331-${n}-before"
done

stage "apply selective post-retention frontier recorder"
python3 -m py_compile scripts/331_apply_post_retention_frontier_recorder.py
python3 scripts/331_apply_post_retention_frontier_recorder.py --root "$ROOT"
python3 scripts/331_apply_post_retention_frontier_recorder.py --root "$ROOT" --check-only

cmp -s /tmp/p331-hwc-before "$HWC"
cmp -s /tmp/p331-phy-before "$PHY"
cmp -s /tmp/p331-phyv3-before "$PHYV3"
cmp -s /tmp/p331-disp-before "$DISP"
! cmp -s /tmp/p331-ctrl-before "$CTRL"
! cmp -s /tmp/p331-rec-before "$REC"
diff -u /tmp/p331-ctrl-before "$CTRL" > /tmp/p331-ctrl.diff || true
diff -u /tmp/p331-rec-before "$REC" > /tmp/p331-rec.diff || true
git -C "$ROOT" diff --check -- drivers/a52_display/msm/dsi/dsi_ctrl.c drivers/a52_secure/a52_ack_secure_flight_recorder.c

stage "strict diagnostic-only scope audit"
python3 - "$CTRL" "$REC" <<'PY'
from pathlib import Path
import sys
bc=Path('/tmp/p331-ctrl-before').read_text(); ac=Path(sys.argv[1]).read_text()
br=Path('/tmp/p331-rec-before').read_text(); ar=Path(sys.argv[2]).read_text()
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
        raise SystemExit(f'Phase331 control-flow/MMIO scope violation {t}: {bc.count(t)} -> {ac.count(t)}')
if ac.count('a52_ackfr_retain_timeout_snapshot();') != bc.count('a52_ackfr_retain_timeout_snapshot();'):
    raise SystemExit('Phase331 changed retention-call count')
if ar.count('atomic_set(&a52_r280_retained, 1);') != br.count('atomic_set(&a52_r280_retained, 1);'):
    raise SystemExit('Phase331 changed Phase280 retention setter')
if ar.count('a52_r280_retained') != br.count('a52_r280_retained'):
    raise SystemExit('Phase331 changed retention-latch reference count unexpectedly')
for t in (
 'P276 331A q=2 st=%x m=%x','P276 331B q=2 b=1 st=%x','P276 331C q=2 b=1 st=%x',
 'P276 331B q=2 b=0 st=%x','P276 331D q=2 ss=1 v=%u','P276 331E q=2 gpio=1',
 'P276 331F q=2 dump=0','P276 331G q=2 dump=1','P276 331H q=2 b=0',
 'P276 331I q=2 dis=0','P276 331J q=2 dis=1'):
    if t not in ac: raise SystemExit('Phase331 marker missing: '+t)
if "fmt[0] == 'P'" not in ar or "fmt[7] == '1'" not in ar:
    raise SystemExit('Phase331 selective post-retention admission missing')
print('Phase331 diagnostic-only scope audit: PASS')
PY

stage "config invariant"
cp /tmp/p331-phase330.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase331-olddefconfig.log 2>&1
cmp -s /tmp/p331-phase330.config "$BUILD/.config"

stage "compile incremental Phase331 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase331-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for m in \
 'P276 331A q=2 st=%x m=%x' \
 'P276 331B q=2 b=1 st=%x' \
 'P276 331C q=2 b=1 st=%x' \
 'P276 331B q=2 b=0 st=%x' \
 'P276 331D q=2 ss=1 v=%u' \
 'P276 331E q=2 gpio=1' \
 'P276 331F q=2 dump=0' \
 'P276 331G q=2 dump=1' \
 'P276 331H q=2 b=0' \
 'P276 331I q=2 dis=0' \
 'P276 331J q=2 dis=1' \
 'P276 330D q=%u c=%x z=%x' \
 'P276 329C q=%u a=%x b=%x c=%x d=%x e=%x f=%x' \
 'P276 319B q=%u c=%x 171=%x 181=%x 191=%x 1a1=%x 1e1=%x 211=%x z=%x r=%x' \
 'P276 280Z q=2'; do grep -aFq "$m" "$IMAGE"; done

stage "package evidence and boot image"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase331-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/331_apply_post_retention_frontier_recorder.py scripts/331_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p331-* "$OUT/audit/" 2>/dev/null || true
cp "$CTRL" "$HWC" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase330-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cp phase330-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE330-BASE-BUILD-IDENTITY.json"

stage "identity and checksums"
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase331-gki-out')
def sha(p):
    h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
idn={
 'phase':'331','flavor':'gki','name':'POST-RETENTION-TIMEOUT-FRONTIER-RECORDER-V1',
 'git_sha':os.getenv('GITHUB_SHA'),'hardware_validated':False,'base_phase':'330',
 'finding_from_phase330':'Phase280 emits P276 280Z immediately before its retention latch; that latch intentionally suppresses all ordinary later recorder entries, so 280Z cannot be interpreted as the hang boundary.',
 'experiment':'preserve the Phase280 latch in place but admit only P276 331* records after retention; bracket timeout branch, interrupt-clear path, Samsung helper/GPIO path, SDE_DBG_DUMP(panic), and status-interrupt disable.',
 'display_control_flow_changed':False,'dsi_mmio_changed':False,'clock_phy_reset_regulator_changed':False,
 'retention_behavior':'all non-Phase331 records remain frozen after Phase280 retention; only the small Phase331 breadcrumb namespace may append.',
 'image_sha256':sha(r/'compile/Image'),'boot_img_sha256':sha(r/'package/boot.img'),'boot_img_size':(r/'package/boot.img').stat().st_size,
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage complete
echo 'Phase331 post-retention timeout frontier recorder: PASS'
trap - EXIT
