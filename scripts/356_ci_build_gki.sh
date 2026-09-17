#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase356-gki-out"
FAIL="$PWD/phase356-gki-failure"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
TIMER="$ROOT/drivers/clocksource/arm_arch_timer.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase356: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase356-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p356-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/356_apply_delay_loop_origin.py scripts/356_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" "$FAIL/source/" || true
  [ -f "$TIMER" ] && cp "$TIMER" "$FAIL/source/" || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE356_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase356: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase356-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$REC" "$TIMER"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'evt->event_handler(evt);' "$TIMER"

cp phase346-gki-out/config/final.config /tmp/p356-base.config
cp "$REC" /tmp/p356-rec-before
cp "$TIMER" /tmp/p356-timer-before

stage "apply delay-loop origin probe"
python3 -m py_compile scripts/356_apply_delay_loop_origin.py
python3 scripts/356_apply_delay_loop_origin.py --root "$ROOT"
python3 scripts/356_apply_delay_loop_origin.py --root "$ROOT" --check-only
! cmp -s /tmp/p356-rec-before "$REC"
! cmp -s /tmp/p356-timer-before "$TIMER"

git diff --no-index --check /tmp/p356-rec-before "$REC" > /tmp/p356-rec-check 2>&1 || true
git diff --no-index --check /tmp/p356-timer-before "$TIMER" > /tmp/p356-timer-check 2>&1 || true
test ! -s /tmp/p356-rec-check
test ! -s /tmp/p356-timer-check

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
r=Path('gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
t=Path('gki/common/drivers/clocksource/arm_arch_timer.c').read_text()
bt=Path('/tmp/p356-timer-before').read_text()
for tok in (
 'A52_PHASE356_DELAY_LOOP_ORIGIN_V1',
 'A52_R356_SIDEBAND_PHYS   0xB1BF0000ULL',
 'A52_R356_SLOT_BYTES      256U',
 'A52_R356_COMMIT          0x356c0de5U',
 'a52_r356_start();','a52_p356_delay_loop_sample();',
 'regs->regs[0]','regs->regs[19]','regs->regs[21]',
 'copy_from_kernel_nofault','loops_per_jiffy','__const_udelay','__udelay',
):
 if tok not in r+t: raise SystemExit('Phase356 missing '+tok)
if t.count('evt->event_handler(evt);') != bt.count('evt->event_handler(evt);'):
 raise SystemExit('Phase356 changed protected timer callback count')
print('Phase356 scope audit: PASS')
PY

stage "config"
cp /tmp/p356-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase356-olddefconfig.log 2>&1
cmp -s /tmp/p356-base.config "$BUILD/.config"
grep -q '^CONFIG_ARM_ARCH_TIMER=y$' "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase356-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p356_delay_loop_sample$' "$SYSTEM_MAP"
grep -Eq '[[:space:]]__const_udelay$' "$SYSTEM_MAP"
grep -Eq '[[:space:]]__udelay$' "$SYSTEM_MAP"
grep -aFq 'P276 356A map=1' "$IMAGE"
grep -E '[[:space:]](_text|__const_udelay|__udelay|a52_p356_delay_loop_sample|arch_timer_read_counter)$' "$SYSTEM_MAP" > /tmp/p356-symbols.txt || true

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase356-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/356_apply_delay_loop_origin.py scripts/356_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p356-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$TIMER" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE356-SCHEMA.txt" <<'SCHEMA'
Phase356 __const_udelay loop-origin probe
========================================
Base: Phase346.

Phase355 result
---------------
The surviving CPU repeatedly interrupted PID1/init at __const_udelay+0x74.
Exact Phase355 Image disassembly places that PC at the arch_timer_read_counter
load immediately after WFE inside __const_udelay's event-stream delay loop.

Purpose
-------
Capture the live loop state without instrumenting the delay function itself:
- x0  = previous arch counter value returned by arch_timer_read_counter
- x19 = delay-loop start counter
- x21 = calculated target cycles
- x22 = event-period/start loop helper
- loops_per_jiffy
- first four qwords from interrupted kernel SP; __const_udelay's prologue saves
  its caller's PAC-signed LR at stack_qword[1]

This distinguishes a frozen CNTVCT/read path from a huge requested delay and
also identifies the direct caller.

Sideband
--------
Physical: 0xB1BF0000..0xB1BF7FFF
copy A: raw 0xF0000, 16 KiB
copy B: raw 0xF4000, 16 KiB
slot size: 256 bytes
sample slots: 63
meta: copy_base + 0x3f00
arm time: 11.500 s

Sampling
--------
Only PID1 kernel-mode timer interruptions whose PC lies in
[__const_udelay, __udelay) are recorded. First matching CPU becomes owner.
slots 0..31: first 32 matching timer IRQs
slots 32..61: one matching sample per 250 matches, seconds 1..30
slot 62: rolling latest one-per-250 matches thereafter

Interpretation
--------------
- x0 increases normally while x21 remains far ahead: delay target is huge;
  symbolicate low VA bits of stack_qword[1] to identify caller.
- x0 remains unchanged while timer IRQs continue: arch_timer_read_counter /
  CNTVCT path is stalled even though the clockevent path still fires.
- x21 normal and x0 passes target but loop persists: re-check arithmetic/codegen
  and event-stream condition with captured x19/x22.
SCHEMA

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase356-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'356','name':'DELAY-LOOP-ORIGIN-V1','base_phase':'346',
 'hardware_validated':False,
 'sideband_phys':'0xB1BF0000','sideband_bytes':0x8000,
 'copy_bytes':0x4000,'slot_bytes':256,'sample_slots':63,
 'arm_ns':11500000000,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage "complete"
echo "Phase356 delay-loop origin build: PASS"
trap - EXIT
