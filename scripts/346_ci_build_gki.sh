#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase346-gki-out"
FAIL="$PWD/phase346-gki-failure"
HWC="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
CTRL="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
STAGE=startup
stage(){ STAGE="$1"; echo "== Phase346: $STAGE =="; }
fail_report(){ set +e; rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}; printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"; cp phase346-*.log "$FAIL/logs/" 2>/dev/null || true; cp /tmp/p346-* "$FAIL/audit/" 2>/dev/null || true; cp scripts/346_apply_dma_raw.py scripts/346_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true; for f in "$HWC" "$CTRL" "$REC"; do [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true; done; [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true; }
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase345 baseline"
if [ "${PHASE346_PREWARMED_PHASE345:-0}" = "1" ]; then
  echo "Phase346: using validated cached Phase345 baseline"
else
  bash scripts/345_ci_build_gki.sh 2>&1 | tee phase346-phase345.log
fi

for f in phase345-gki-out/package/boot.img phase345-gki-out/compile/Image phase345-gki-out/config/final.config "$HWC" "$CTRL" "$REC"; do test -s "$f"; done
test "$(stat -c '%s' phase345-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE345_DMA_US_FRONTIER_V1' "$HWC"
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'static void __used a52_r342_start(void)' "$REC"
! grep -Fq 
cp phase345-gki-out/config/final.config /tmp/p346-base.config
cp "$HWC" /tmp/p346-hwc-before
cp "$CTRL" /tmp/p346-ctrl-before
cp "$REC" /tmp/p346-rec-before

stage "apply DMA raw sideband"
python3 -m py_compile scripts/346_apply_dma_raw.py
python3 scripts/346_apply_dma_raw.py --root "$ROOT"
python3 scripts/346_apply_dma_raw.py --root "$ROOT" --check-only
cmp -s /tmp/p346-rec-before "$REC"
! cmp -s /tmp/p346-hwc-before "$HWC"
! cmp -s /tmp/p346-ctrl-before "$CTRL"
git diff --no-index --check /tmp/p346-hwc-before "$HWC" > /tmp/p346-hwc-check 2>&1 || true
git diff --no-index --check /tmp/p346-ctrl-before "$CTRL" > /tmp/p346-ctrl-check 2>&1 || true
test ! -s /tmp/p346-hwc-check
test ! -s /tmp/p346-ctrl-check

stage "scope audit"
python3 - "$HWC" "$CTRL" "$REC" <<'PY'
from pathlib import Path
import sys
h=Path(sys.argv[1]).read_text(); c=Path(sys.argv[2]).read_text(); r=Path(sys.argv[3]).read_text()
bh=Path('/tmp/p346-hwc-before').read_text(); bc=Path('/tmp/p346-ctrl-before').read_text()
for x in (
 'A52_PHASE346_DMA_RAW_SIDEBAND_V1',
 'A52_P346_SIDEBAND_PHYS  0xB1BFA000ULL',
 'A52_P346_SIDEBAND_BYTES 0x2000U',
 'A52_P346_SLOT_BYTES     96U',
 'a52_p346_persist_samples();',
 'a52_p346_sideband_init();',
 'memremap(A52_P346_SIDEBAND_PHYS',
):
 if x not in h+c: raise SystemExit('Phase346 missing '+x)
for x in ('DSI_W32(','DSI_R32(','wait_for_completion_timeout(','clk_set_rate(','regulator_enable(','reset_control_','udelay(','usleep_range(','msleep('):
 if h.count(x)!=bh.count(x) or c.count(x)!=bc.count(x):
  raise SystemExit('Phase346 changed measured primitive '+x)
if 'static void __used a52_r342_start(void)' not in r:
 raise SystemExit('Phase346 requires disabled Phase342 writer')
if '\ta52_r342_start();' in r:
 raise SystemExit('Phase346 sideband collision: Phase342 writer active')
if h.count('memremap(') != bh.count('memremap(')+1:
 raise SystemExit('Phase346 expected one sideband map')
if h.count('__flush_dcache_area(') != bh.count('__flush_dcache_area(')+3:
 raise SystemExit('Phase346 expected two mirrored slot flushes plus one init clear flush')
print('Phase346 scope audit: PASS')
PY

stage "config"
cp /tmp/p346-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase346-olddefconfig.log 2>&1
cmp -s /tmp/p346-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase346-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
SYSTEM_MAP="$BUILD/System.map"; test -s "$SYSTEM_MAP"
# Runtime strings must survive in Image; source-only Phase346 labels need not.
for m in 'P276 345R %x %x %llx %x %x %x %x' 'P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u'; do
  grep -aFq "$m" "$IMAGE"
done
# Verify the actual Phase346 compiled symbols instead of a source comment token.
grep -Eq '[[:space:]]a52_p346_sideband_init
stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase346-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/346_apply_dma_raw.py scripts/346_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p346-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase345-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cat > "$OUT/PHASE346-SCHEMA.txt" <<'EOF'
Phase346 DMA raw sideband
=========================
Physical region: 0xB1BFA000..0xB1BFC000 (former Phase342 sideband; Phase342 runtime disabled)
Two mirrored copies: +0x0000 and +0x1000
Slot size: 96 bytes, little-endian
Slot 0: init marker (point=0xffffffff, status=1)
Slots 1..: Phase345 p0..p6 samples
Fields:
 u64 magic, u64 ns,
 u32 index, point, status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status,
 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl, ack_err, timeout,
 phy_err, axi2ahb, dbg171, commit, version
magic=0x3634335741524d44 commit=0x346c0de5 version=1
Samples are copied to this region only after p6 and DEBUG_BUS_CTL restore.
EOF
python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase346-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={'phase':'346','name':'DMA-RAW-V1','base_phase':'345','hardware_validated':False,
'sideband_phys':'0xB1BFA000','sideband_bytes':8192,'mirrored':True,'slot_bytes':96,
'phase342_runtime_writer_disabled':True,'measured_dsi_burst_changed':False,
'boot_img_size':(r/'package/boot.img').stat().st_size,'boot_img_sha256':sha(r/'package/boot.img'),
'image_sha256':sha(r/'compile/Image'),'git_sha':os.getenv('GITHUB_SHA')}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase346 DMA raw build: PASS'
trap - EXIT
\ta52_r342_start();' "$REC"

cp phase345-gki-out/config/final.config /tmp/p346-base.config
cp "$HWC" /tmp/p346-hwc-before
cp "$CTRL" /tmp/p346-ctrl-before
cp "$REC" /tmp/p346-rec-before

stage "apply DMA raw sideband"
python3 -m py_compile scripts/346_apply_dma_raw.py
python3 scripts/346_apply_dma_raw.py --root "$ROOT"
python3 scripts/346_apply_dma_raw.py --root "$ROOT" --check-only
cmp -s /tmp/p346-rec-before "$REC"
! cmp -s /tmp/p346-hwc-before "$HWC"
! cmp -s /tmp/p346-ctrl-before "$CTRL"
git diff --no-index --check /tmp/p346-hwc-before "$HWC" > /tmp/p346-hwc-check 2>&1 || true
git diff --no-index --check /tmp/p346-ctrl-before "$CTRL" > /tmp/p346-ctrl-check 2>&1 || true
test ! -s /tmp/p346-hwc-check
test ! -s /tmp/p346-ctrl-check

stage "scope audit"
python3 - "$HWC" "$CTRL" "$REC" <<'PY'
from pathlib import Path
import sys
h=Path(sys.argv[1]).read_text(); c=Path(sys.argv[2]).read_text(); r=Path(sys.argv[3]).read_text()
bh=Path('/tmp/p346-hwc-before').read_text(); bc=Path('/tmp/p346-ctrl-before').read_text()
for x in (
 'A52_PHASE346_DMA_RAW_SIDEBAND_V1',
 'A52_P346_SIDEBAND_PHYS  0xB1BFA000ULL',
 'A52_P346_SIDEBAND_BYTES 0x2000U',
 'A52_P346_SLOT_BYTES     96U',
 'a52_p346_persist_samples();',
 'a52_p346_sideband_init();',
 'memremap(A52_P346_SIDEBAND_PHYS',
):
 if x not in h+c: raise SystemExit('Phase346 missing '+x)
for x in ('DSI_W32(','DSI_R32(','wait_for_completion_timeout(','clk_set_rate(','regulator_enable(','reset_control_','udelay(','usleep_range(','msleep('):
 if h.count(x)!=bh.count(x) or c.count(x)!=bc.count(x):
  raise SystemExit('Phase346 changed measured primitive '+x)
if 'static void __used a52_r342_start(void)' not in r:
 raise SystemExit('Phase346 requires disabled Phase342 writer')
if '\ta52_r342_start();' in r:
 raise SystemExit('Phase346 sideband collision: Phase342 writer active')
if h.count('memremap(') != bh.count('memremap(')+1:
 raise SystemExit('Phase346 expected one sideband map')
if h.count('__flush_dcache_area(') != bh.count('__flush_dcache_area(')+3:
 raise SystemExit('Phase346 expected two mirrored slot flushes plus one init clear flush')
print('Phase346 scope audit: PASS')
PY

stage "config"
cp /tmp/p346-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase346-olddefconfig.log 2>&1
cmp -s /tmp/p346-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase346-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for m in 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' 'P276 345R %x %x %llx %x %x %x %x' 'P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u'; do grep -aFq "$m" "$IMAGE"; done

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase346-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/346_apply_dma_raw.py scripts/346_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p346-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase345-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cat > "$OUT/PHASE346-SCHEMA.txt" <<'EOF'
Phase346 DMA raw sideband
=========================
Physical region: 0xB1BFA000..0xB1BFC000 (former Phase342 sideband; Phase342 runtime disabled)
Two mirrored copies: +0x0000 and +0x1000
Slot size: 96 bytes, little-endian
Slot 0: init marker (point=0xffffffff, status=1)
Slots 1..: Phase345 p0..p6 samples
Fields:
 u64 magic, u64 ns,
 u32 index, point, status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status,
 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl, ack_err, timeout,
 phy_err, axi2ahb, dbg171, commit, version
magic=0x3634335741524d44 commit=0x346c0de5 version=1
Samples are copied to this region only after p6 and DEBUG_BUS_CTL restore.
EOF
python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase346-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={'phase':'346','name':'DMA-RAW-V1','base_phase':'345','hardware_validated':False,
'sideband_phys':'0xB1BFA000','sideband_bytes':8192,'mirrored':True,'slot_bytes':96,
'phase342_runtime_writer_disabled':True,'measured_dsi_burst_changed':False,
'boot_img_size':(r/'package/boot.img').stat().st_size,'boot_img_sha256':sha(r/'package/boot.img'),
'image_sha256':sha(r/'compile/Image'),'git_sha':os.getenv('GITHUB_SHA')}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase346 DMA raw build: PASS'
trap - EXIT
 "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p346_persist_samples
stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase346-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/346_apply_dma_raw.py scripts/346_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p346-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase345-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cat > "$OUT/PHASE346-SCHEMA.txt" <<'EOF'
Phase346 DMA raw sideband
=========================
Physical region: 0xB1BFA000..0xB1BFC000 (former Phase342 sideband; Phase342 runtime disabled)
Two mirrored copies: +0x0000 and +0x1000
Slot size: 96 bytes, little-endian
Slot 0: init marker (point=0xffffffff, status=1)
Slots 1..: Phase345 p0..p6 samples
Fields:
 u64 magic, u64 ns,
 u32 index, point, status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status,
 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl, ack_err, timeout,
 phy_err, axi2ahb, dbg171, commit, version
magic=0x3634335741524d44 commit=0x346c0de5 version=1
Samples are copied to this region only after p6 and DEBUG_BUS_CTL restore.
EOF
python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase346-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={'phase':'346','name':'DMA-RAW-V1','base_phase':'345','hardware_validated':False,
'sideband_phys':'0xB1BFA000','sideband_bytes':8192,'mirrored':True,'slot_bytes':96,
'phase342_runtime_writer_disabled':True,'measured_dsi_burst_changed':False,
'boot_img_size':(r/'package/boot.img').stat().st_size,'boot_img_sha256':sha(r/'package/boot.img'),
'image_sha256':sha(r/'compile/Image'),'git_sha':os.getenv('GITHUB_SHA')}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase346 DMA raw build: PASS'
trap - EXIT
\ta52_r342_start();' "$REC"

cp phase345-gki-out/config/final.config /tmp/p346-base.config
cp "$HWC" /tmp/p346-hwc-before
cp "$CTRL" /tmp/p346-ctrl-before
cp "$REC" /tmp/p346-rec-before

stage "apply DMA raw sideband"
python3 -m py_compile scripts/346_apply_dma_raw.py
python3 scripts/346_apply_dma_raw.py --root "$ROOT"
python3 scripts/346_apply_dma_raw.py --root "$ROOT" --check-only
cmp -s /tmp/p346-rec-before "$REC"
! cmp -s /tmp/p346-hwc-before "$HWC"
! cmp -s /tmp/p346-ctrl-before "$CTRL"
git diff --no-index --check /tmp/p346-hwc-before "$HWC" > /tmp/p346-hwc-check 2>&1 || true
git diff --no-index --check /tmp/p346-ctrl-before "$CTRL" > /tmp/p346-ctrl-check 2>&1 || true
test ! -s /tmp/p346-hwc-check
test ! -s /tmp/p346-ctrl-check

stage "scope audit"
python3 - "$HWC" "$CTRL" "$REC" <<'PY'
from pathlib import Path
import sys
h=Path(sys.argv[1]).read_text(); c=Path(sys.argv[2]).read_text(); r=Path(sys.argv[3]).read_text()
bh=Path('/tmp/p346-hwc-before').read_text(); bc=Path('/tmp/p346-ctrl-before').read_text()
for x in (
 'A52_PHASE346_DMA_RAW_SIDEBAND_V1',
 'A52_P346_SIDEBAND_PHYS  0xB1BFA000ULL',
 'A52_P346_SIDEBAND_BYTES 0x2000U',
 'A52_P346_SLOT_BYTES     96U',
 'a52_p346_persist_samples();',
 'a52_p346_sideband_init();',
 'memremap(A52_P346_SIDEBAND_PHYS',
):
 if x not in h+c: raise SystemExit('Phase346 missing '+x)
for x in ('DSI_W32(','DSI_R32(','wait_for_completion_timeout(','clk_set_rate(','regulator_enable(','reset_control_','udelay(','usleep_range(','msleep('):
 if h.count(x)!=bh.count(x) or c.count(x)!=bc.count(x):
  raise SystemExit('Phase346 changed measured primitive '+x)
if 'static void __used a52_r342_start(void)' not in r:
 raise SystemExit('Phase346 requires disabled Phase342 writer')
if '\ta52_r342_start();' in r:
 raise SystemExit('Phase346 sideband collision: Phase342 writer active')
if h.count('memremap(') != bh.count('memremap(')+1:
 raise SystemExit('Phase346 expected one sideband map')
if h.count('__flush_dcache_area(') != bh.count('__flush_dcache_area(')+3:
 raise SystemExit('Phase346 expected two mirrored slot flushes plus one init clear flush')
print('Phase346 scope audit: PASS')
PY

stage "config"
cp /tmp/p346-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase346-olddefconfig.log 2>&1
cmp -s /tmp/p346-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase346-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for m in 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' 'P276 345R %x %x %llx %x %x %x %x' 'P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u'; do grep -aFq "$m" "$IMAGE"; done

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase346-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/346_apply_dma_raw.py scripts/346_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p346-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase345-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cat > "$OUT/PHASE346-SCHEMA.txt" <<'EOF'
Phase346 DMA raw sideband
=========================
Physical region: 0xB1BFA000..0xB1BFC000 (former Phase342 sideband; Phase342 runtime disabled)
Two mirrored copies: +0x0000 and +0x1000
Slot size: 96 bytes, little-endian
Slot 0: init marker (point=0xffffffff, status=1)
Slots 1..: Phase345 p0..p6 samples
Fields:
 u64 magic, u64 ns,
 u32 index, point, status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status,
 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl, ack_err, timeout,
 phy_err, axi2ahb, dbg171, commit, version
magic=0x3634335741524d44 commit=0x346c0de5 version=1
Samples are copied to this region only after p6 and DEBUG_BUS_CTL restore.
EOF
python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase346-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={'phase':'346','name':'DMA-RAW-V1','base_phase':'345','hardware_validated':False,
'sideband_phys':'0xB1BFA000','sideband_bytes':8192,'mirrored':True,'slot_bytes':96,
'phase342_runtime_writer_disabled':True,'measured_dsi_burst_changed':False,
'boot_img_size':(r/'package/boot.img').stat().st_size,'boot_img_sha256':sha(r/'package/boot.img'),
'image_sha256':sha(r/'compile/Image'),'git_sha':os.getenv('GITHUB_SHA')}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase346 DMA raw build: PASS'
trap - EXIT
 "$SYSTEM_MAP"

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase346-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/346_apply_dma_raw.py scripts/346_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p346-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase345-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cat > "$OUT/PHASE346-SCHEMA.txt" <<'EOF'
Phase346 DMA raw sideband
=========================
Physical region: 0xB1BFA000..0xB1BFC000 (former Phase342 sideband; Phase342 runtime disabled)
Two mirrored copies: +0x0000 and +0x1000
Slot size: 96 bytes, little-endian
Slot 0: init marker (point=0xffffffff, status=1)
Slots 1..: Phase345 p0..p6 samples
Fields:
 u64 magic, u64 ns,
 u32 index, point, status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status,
 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl, ack_err, timeout,
 phy_err, axi2ahb, dbg171, commit, version
magic=0x3634335741524d44 commit=0x346c0de5 version=1
Samples are copied to this region only after p6 and DEBUG_BUS_CTL restore.
EOF
python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase346-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={'phase':'346','name':'DMA-RAW-V1','base_phase':'345','hardware_validated':False,
'sideband_phys':'0xB1BFA000','sideband_bytes':8192,'mirrored':True,'slot_bytes':96,
'phase342_runtime_writer_disabled':True,'measured_dsi_burst_changed':False,
'boot_img_size':(r/'package/boot.img').stat().st_size,'boot_img_sha256':sha(r/'package/boot.img'),
'image_sha256':sha(r/'compile/Image'),'git_sha':os.getenv('GITHUB_SHA')}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase346 DMA raw build: PASS'
trap - EXIT
\ta52_r342_start();' "$REC"

cp phase345-gki-out/config/final.config /tmp/p346-base.config
cp "$HWC" /tmp/p346-hwc-before
cp "$CTRL" /tmp/p346-ctrl-before
cp "$REC" /tmp/p346-rec-before

stage "apply DMA raw sideband"
python3 -m py_compile scripts/346_apply_dma_raw.py
python3 scripts/346_apply_dma_raw.py --root "$ROOT"
python3 scripts/346_apply_dma_raw.py --root "$ROOT" --check-only
cmp -s /tmp/p346-rec-before "$REC"
! cmp -s /tmp/p346-hwc-before "$HWC"
! cmp -s /tmp/p346-ctrl-before "$CTRL"
git diff --no-index --check /tmp/p346-hwc-before "$HWC" > /tmp/p346-hwc-check 2>&1 || true
git diff --no-index --check /tmp/p346-ctrl-before "$CTRL" > /tmp/p346-ctrl-check 2>&1 || true
test ! -s /tmp/p346-hwc-check
test ! -s /tmp/p346-ctrl-check

stage "scope audit"
python3 - "$HWC" "$CTRL" "$REC" <<'PY'
from pathlib import Path
import sys
h=Path(sys.argv[1]).read_text(); c=Path(sys.argv[2]).read_text(); r=Path(sys.argv[3]).read_text()
bh=Path('/tmp/p346-hwc-before').read_text(); bc=Path('/tmp/p346-ctrl-before').read_text()
for x in (
 'A52_PHASE346_DMA_RAW_SIDEBAND_V1',
 'A52_P346_SIDEBAND_PHYS  0xB1BFA000ULL',
 'A52_P346_SIDEBAND_BYTES 0x2000U',
 'A52_P346_SLOT_BYTES     96U',
 'a52_p346_persist_samples();',
 'a52_p346_sideband_init();',
 'memremap(A52_P346_SIDEBAND_PHYS',
):
 if x not in h+c: raise SystemExit('Phase346 missing '+x)
for x in ('DSI_W32(','DSI_R32(','wait_for_completion_timeout(','clk_set_rate(','regulator_enable(','reset_control_','udelay(','usleep_range(','msleep('):
 if h.count(x)!=bh.count(x) or c.count(x)!=bc.count(x):
  raise SystemExit('Phase346 changed measured primitive '+x)
if 'static void __used a52_r342_start(void)' not in r:
 raise SystemExit('Phase346 requires disabled Phase342 writer')
if '\ta52_r342_start();' in r:
 raise SystemExit('Phase346 sideband collision: Phase342 writer active')
if h.count('memremap(') != bh.count('memremap(')+1:
 raise SystemExit('Phase346 expected one sideband map')
if h.count('__flush_dcache_area(') != bh.count('__flush_dcache_area(')+3:
 raise SystemExit('Phase346 expected two mirrored slot flushes plus one init clear flush')
print('Phase346 scope audit: PASS')
PY

stage "config"
cp /tmp/p346-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase346-olddefconfig.log 2>&1
cmp -s /tmp/p346-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase346-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0
IMAGE="$BUILD/arch/arm64/boot/Image"; test -s "$IMAGE"
for m in 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' 'P276 345R %x %x %llx %x %x %x %x' 'P276 343A map=%u kt=%ld cpu=%u sh=%u lim=%u'; do grep -aFq "$m" "$IMAGE"; done

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$OUT/compile/Image"; cp "$BUILD/.config" "$OUT/config/final.config"
cp phase346-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/346_apply_dma_raw.py scripts/346_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p346-* "$OUT/audit/" 2>/dev/null || true
cp "$HWC" "$CTRL" "$REC" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase345-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296
cat > "$OUT/PHASE346-SCHEMA.txt" <<'EOF'
Phase346 DMA raw sideband
=========================
Physical region: 0xB1BFA000..0xB1BFC000 (former Phase342 sideband; Phase342 runtime disabled)
Two mirrored copies: +0x0000 and +0x1000
Slot size: 96 bytes, little-endian
Slot 0: init marker (point=0xffffffff, status=1)
Slots 1..: Phase345 p0..p6 samples
Fields:
 u64 magic, u64 ns,
 u32 index, point, status, fifo, clk_ctrl, clk_status, int_ctrl, lane_status,
 dma_ctrl, dma_offset, dma_length, sw_trigger, trig_ctrl, ack_err, timeout,
 phy_err, axi2ahb, dbg171, commit, version
magic=0x3634335741524d44 commit=0x346c0de5 version=1
Samples are copied to this region only after p6 and DEBUG_BUS_CTL restore.
EOF
python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase346-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={'phase':'346','name':'DMA-RAW-V1','base_phase':'345','hardware_validated':False,
'sideband_phys':'0xB1BFA000','sideband_bytes':8192,'mirrored':True,'slot_bytes':96,
'phase342_runtime_writer_disabled':True,'measured_dsi_burst_changed':False,
'boot_img_size':(r/'package/boot.img').stat().st_size,'boot_img_sha256':sha(r/'package/boot.img'),
'image_sha256':sha(r/'compile/Image'),'git_sha':os.getenv('GITHUB_SHA')}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage complete
echo 'Phase346 DMA raw build: PASS'
trap - EXIT
