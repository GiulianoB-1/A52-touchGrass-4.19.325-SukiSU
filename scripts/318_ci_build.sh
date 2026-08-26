#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase318-gki-out"
FAIL="$PWD/phase318-gki-failure"
QCOM="$ROOT/drivers/clk/qcom"
RCGH="$QCOM/clk-rcg.h"
RCG2="$QCOM/clk-rcg2.c"
DISP="$QCOM/dispcc-lagoon.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
PHY="$ROOT/drivers/a52_display/msm/dsi/dsi_phy.c"
PHYV3="$ROOT/drivers/a52_display/msm/dsi/dsi_phy_hw_v3_0.c"
STAGE=startup

stage(){ STAGE="$1"; echo "== Phase318 stage: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile,nested}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase318-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p318-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/318_apply_esc0_rcg_safe_relock.py "$FAIL/audit/" 2>/dev/null || true
  for f in "$RCGH" "$RCG2" "$DISP" "$CTRL" "$HWC" "$PHY" "$PHYV3"; do [ -f "$f" ] && cp "$f" "$FAIL/source/" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  for d in phase*-gki-failure; do [ -d "$d" ] || continue; [ "$d" = phase318-gki-failure ] && continue; cp -a "$d" "$FAIL/nested/" 2>/dev/null || true; done
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact successful Phase316 baseline"
bash scripts/316_ci_build.sh 2>&1 | tee phase318-phase316.log
for f in phase316-gki-out/package/boot.img phase316-gki-out/compile/Image phase316-gki-out/config/final.config \
         "$RCGH" "$RCG2" "$DISP" "$CTRL" "$HWC" "$PHY" "$PHYV3"; do test -s "$f"; done
grep -Fq 'A52_PHASE316_GKI_F0_LAUNCH_FAULT_WINDOW_RECORDER_V1' "$HWC"
! grep -Fq 'A52_PHASE318_ESC0_RCG_SAFE_RELOCK_AB_V1' "$RCG2"
cp "$RCGH" /tmp/p318-rcgh-before.h
cp "$RCG2" /tmp/p318-rcg2-before.c
cp "$DISP" /tmp/p318-disp-before.c
cp "$CTRL" /tmp/p318-ctrl-before.c
cp "$HWC" /tmp/p318-hwc-before.c
cp "$PHY" /tmp/p318-phy-before.c
cp "$PHYV3" /tmp/p318-phyv3-before.c

stage "apply ESC0-only Golden RCG relock"
python3 -m py_compile scripts/318_apply_esc0_rcg_safe_relock.py
python3 scripts/318_apply_esc0_rcg_safe_relock.py --root "$ROOT"
python3 scripts/318_apply_esc0_rcg_safe_relock.py --root "$ROOT" --check-only
git -C "$ROOT" diff --check -- drivers/clk/qcom/clk-rcg.h drivers/clk/qcom/clk-rcg2.c drivers/clk/qcom/dispcc-lagoon.c
cp "$RCGH" /tmp/p318-rcgh-after.h
cp "$RCG2" /tmp/p318-rcg2-after.c
cp "$DISP" /tmp/p318-disp-after.c
diff -u /tmp/p318-rcgh-before.h /tmp/p318-rcgh-after.h > /tmp/p318-rcgh.diff || true
diff -u /tmp/p318-rcg2-before.c /tmp/p318-rcg2-after.c > /tmp/p318-rcg2.diff || true
diff -u /tmp/p318-disp-before.c /tmp/p318-disp-after.c > /tmp/p318-disp.diff || true
cmp -s /tmp/p318-ctrl-before.c "$CTRL"
cmp -s /tmp/p318-hwc-before.c "$HWC"
cmp -s /tmp/p318-phy-before.c "$PHY"
cmp -s /tmp/p318-phyv3-before.c "$PHYV3"

stage "scope and Golden-semantics audit"
python3 - <<'PY'
from pathlib import Path
bh=Path('/tmp/p318-rcgh-before.h').read_text(); ah=Path('/tmp/p318-rcgh-after.h').read_text()
br=Path('/tmp/p318-rcg2-before.c').read_text(); ar=Path('/tmp/p318-rcg2-after.c').read_text()
bd=Path('/tmp/p318-disp-before.c').read_text(); ad=Path('/tmp/p318-disp-after.c').read_text()
mark='A52_PHASE318_ESC0_RCG_SAFE_RELOCK_AB_V1'
if ah.count(mark)!=1 or ar.count(mark)!=1 or ad.count(mark)!=1: raise SystemExit('Phase318 marker count mismatch')
if ad.count('.enable_safe_config = true,')!=1: raise SystemExit('Phase318 expected exactly one safe-config opt-in')
esc=ad[ad.index('static struct clk_rcg2 disp_cc_mdss_esc0_clk_src = {'):]
esc=esc[:esc.index('};')]
if '.enable_safe_config = true,' not in esc: raise SystemExit('Phase318 ESC0 not opted in')
for name in ('disp_cc_mdss_byte0_clk_src','disp_cc_mdss_pclk0_clk_src'):
 b=ad[ad.index('static struct clk_rcg2 '+name+' = {'):]; b=b[:b.index('};')]
 if '.enable_safe_config' in b: raise SystemExit('Phase318 unexpectedly opts in '+name)
# Only the generic RCG callback gains hardware operations. The flag defaults
# false for every pre-existing RCG, so only ESC0 reaches these writes.
if ar.count('regmap_update_bits(')-br.count('regmap_update_bits(')!=2: raise SystemExit('Phase318 expected two new force-enable regmap_update_bits call sites')
if ar.count('udelay(')-br.count('udelay(')!=1: raise SystemExit('Phase318 expected one new force-enable poll delay call site')
for token in ('clk_set_rate(','clk_set_parent(','clk_prepare_enable(','clk_disable_unprepare(',
              'regulator_enable(','regulator_disable(','reset_control_assert(','reset_control_deassert('):
 if ar.count(token)!=br.count(token) or ad.count(token)!=bd.count(token): raise SystemExit('Phase318 forbidden unrelated functional delta: '+token)
# No VDD or broad downstream clock-framework port is allowed in this A/B.
for token in ('vdd-level-lagoon.h','DEFINE_VDD_REGULATORS','.vdd_class =','.num_rate_max =','.rate_max ='):
 if ad.count(token)!=bd.count(token): raise SystemExit('Phase318 must not alter VDD machinery: '+token)
for token in ('a52_p318_clk_rcg2_enable','a52_p318_rcg_set_force_enable','clk_rcg2_configure(rcg, f)',
              '.enable = a52_p318_clk_rcg2_enable,','A52P318 %s: relock inherited RCG rate=%lu','A52P318 %s: relock complete rc=%d'):
 if token not in ar: raise SystemExit('Phase318 missing relock token: '+token)
print('Phase318 ESC0-only Golden relock scope audit: PASS')
PY

stage "config invariant"
cp phase316-gki-out/config/final.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase318-olddefconfig.log 2>&1
cmp -s phase316-gki-out/config/final.config "$BUILD/.config"

stage "compile Phase318 Image"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase318-compile.log
rc=${PIPESTATUS[0]}; set -e
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' phase318-compile.log | tail -n 300 || true
  exit "$rc"
fi
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for marker in 'A52P318 %s: relock inherited RCG rate=%lu' 'A52P318 %s: relock complete rc=%d' \
              'P276 316S q=2 im=%x irq=%u dn=%u wq=%u ec=%u ret=%d' \
              'P276 310D q=%u rc=%d p=%x b=%x i=%x e=%x'; do grep -aFq "$marker" "$IMAGE"; done

stage "assemble evidence and repack"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase318-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/318_apply_esc0_rcg_safe_relock.py "$OUT/audit/"
cp /tmp/p318-* "$OUT/audit/" 2>/dev/null || true
cp "$RCGH" "$RCG2" "$DISP" "$CTRL" "$HWC" "$PHY" "$PHYV3" "$OUT/source/"
cp phase316-gki-out/BUILD-IDENTITY.json "$OUT/audit/PHASE316-BASE-BUILD-IDENTITY.json"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase316-gki-out/package/boot.img \
  --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

stage "identity and checksums"
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
r=Path('phase318-gki-out')
def sha(p): h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
idn={
 'phase':'318','name':'GKI-ESC0-RCG-SAFE-RELOCK-AB-V1','git_sha':os.getenv('GITHUB_SHA'),
 'hardware_validated':False,'base':'exact successful Phase316 Golden-parity recorder baseline',
 'hypothesis':'continuous splash skips ESC set_rate; Golden clk_rcg2 enable force-reapplies inherited ESC0 RCG configuration and pulses CMD_UPDATE; stripped GKI port did not',
 'behavior_change':'ESC0 source only: on CCF enable, force-enable RCG, reapply the inherited 19.2MHz frequency-table configuration, pulse CMD_UPDATE, clear force-enable',
 'unchanged':['byte0 RCG behavior','pclk0 RCG behavior','DSI/PHY register setup','VDD machinery','Phase316 recorder stack'],
 'golden_sources':['TouchGrass dispcc-lagoon.c ESC0 enable_safe_config=true','TouchGrass clk-rcg2.c clk_rcg2_ops.enable','TouchGrass dsi_clk_manager.c continuous-splash ESC set_rate skip plus clk_prepare_enable'],
 'image_sha256':sha(r/'compile/Image'),'boot_img_sha256':sha(r/'package/boot.img'),
 'boot_img_size':(r/'package/boot.img').stat().st_size}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(idn,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase318 ESC0-only RCG safe relock A/B: PASS'
