#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase354-gki-out"
FAIL="$PWD/phase354-gki-failure"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
IDLE="$ROOT/kernel/sched/idle.c"
TIMER="$ROOT/drivers/clocksource/arm_arch_timer.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase354: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase354-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p354-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/354_apply_final_idle_timer_frontier.py scripts/354_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" "$FAIL/source/" || true
  [ -f "$IDLE" ] && cp "$IDLE" "$FAIL/source/" || true
  [ -f "$TIMER" ] && cp "$TIMER" "$FAIL/source/" || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE354_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase354: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase354-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$REC" "$IDLE" "$TIMER"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'arch_cpu_idle();' "$IDLE"
grep -Fq 'static __always_inline irqreturn_t timer_handler' "$TIMER"
grep -Fq 'evt->event_handler(evt);' "$TIMER"

cp phase346-gki-out/config/final.config /tmp/p354-base.config
cp "$REC" /tmp/p354-rec-before
cp "$IDLE" /tmp/p354-idle-before
cp "$TIMER" /tmp/p354-timer-before

stage "apply final idle/timer frontier"
python3 -m py_compile scripts/354_apply_final_idle_timer_frontier.py
python3 scripts/354_apply_final_idle_timer_frontier.py --root "$ROOT"
python3 scripts/354_apply_final_idle_timer_frontier.py --root "$ROOT" --check-only
! cmp -s /tmp/p354-rec-before "$REC"
! cmp -s /tmp/p354-idle-before "$IDLE"
! cmp -s /tmp/p354-timer-before "$TIMER"

git diff --no-index --check /tmp/p354-rec-before "$REC" > /tmp/p354-rec-check 2>&1 || true
git diff --no-index --check /tmp/p354-idle-before "$IDLE" > /tmp/p354-idle-check 2>&1 || true
git diff --no-index --check /tmp/p354-timer-before "$TIMER" > /tmp/p354-timer-check 2>&1 || true
test ! -s /tmp/p354-rec-check
test ! -s /tmp/p354-idle-check
test ! -s /tmp/p354-timer-check

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
r=Path('gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
i=Path('gki/common/kernel/sched/idle.c').read_text()
t=Path('gki/common/drivers/clocksource/arm_arch_timer.c').read_text()
bi=Path('/tmp/p354-idle-before').read_text()
bt=Path('/tmp/p354-timer-before').read_text()
for tok in (
 'A52_PHASE354_FINAL_IDLE_TIMER_FRONTIER_V1',
 'A52_R354_SIDEBAND_PHYS   0xB1BF4000ULL',
 'A52_R354_COMMIT          0x354c0de5U',
 'A52_R354_ARM_NS          12150000000ULL',
 'a52_r354_start();','a52_p354_mark_final(0U);','a52_p354_mark_final(1U);',
 'a52_p354_mark_final(2U);','a52_p354_mark_final(3U);',
):
 if tok not in r+i+t: raise SystemExit('Phase354 missing '+tok)
for tok in ('arch_cpu_idle();','evt->event_handler(evt);'):
 if (i+t).count(tok) != (bi+bt).count(tok):
  raise SystemExit('Phase354 changed protected call count '+tok)
print('Phase354 scope audit: PASS')
PY

stage "config"
cp /tmp/p354-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase354-olddefconfig.log 2>&1
cmp -s /tmp/p354-base.config "$BUILD/.config"
grep -q '^CONFIG_CPU_IDLE=y$' "$BUILD/.config"
grep -q '^CONFIG_ARM_ARCH_TIMER=y$' "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase354-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p354_mark_final$' "$SYSTEM_MAP"
grep -aFq 'P276 354A map=1' "$IMAGE"

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase354-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/354_apply_final_idle_timer_frontier.py scripts/354_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p354-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$IDLE" "$TIMER" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE354-SCHEMA.txt" <<'SCHEMA'
Phase354 final architectural-idle / architected-timer frontier
==============================================================
Base: Phase346.

Purpose
-------
Phase353 proved that the ARM architected timer callback still entered and
returned at ~12.200009 s, but its first-after-threshold schema could miss a
later non-returning event. Phase354 arms at 12.150 s and continuously overwrites
one per-CPU slot with the LAST architectural-idle and architected-timer events.

Dedicated sideband
------------------
Physical: 0xB1BF4000..0xB1BF7FFF (16 KiB)
copy A: +0x0000 (8 KiB), raw 1MiB offset 0xF4000
copy B: +0x2000 (8 KiB), raw 1MiB offset 0xF6000
Per-CPU slot: 128 bytes at copy_base + cpu*128
Meta slot: copy_base + 0x1000

Per-CPU slot, little endian
---------------------------
 u64 magic             = 0x3435334c414e4946 ("FINAL354")
 u64 armed_ns          = 12150000000
 u64 last_event_ns
 u64 arch_enter_ns
 u64 arch_return_ns
 u64 timer_enter_ns
 u64 timer_return_ns
 u64 sequence
 u32 arch_enter_count
 u32 arch_return_count
 u32 timer_enter_count
 u32 timer_return_count
 u32 last_kind         # 0 arch-enter, 1 arch-return, 2 timer-enter, 3 timer-return
 u32 cpu
 u32 commit            = 0x354c0de5
 u32 version           = 1
 u64 reserved[4]

Interpretation
--------------
- arch_enter_count > arch_return_count and arch_enter_ns > arch_return_ns:
  final fallback architectural idle/WFI did not return.
- timer_enter_count > timer_return_count and timer_enter_ns > timer_return_ns:
  architected timer IRQ arrived but its clockevent callback did not return.
- both pairs remain balanced and continue to the final timestamp:
  move beyond simple WFI/timer failure toward generic IRQ/GIC, SCM/firmware,
  RPMh/interconnect/power collapse, or another system-wide execution blocker.
- Compare both mirrors. Failing-kernel pmsg activity can clear bits even though
  the modified recovery now preserves the final 1MiB image exactly.
SCHEMA

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase354-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'354','name':'FINAL-IDLE-TIMER-FRONTIER-V1','base_phase':'346',
 'hardware_validated':False,
 'sideband_phys':'0xB1BF4000','sideband_bytes':0x4000,
 'copy_bytes':0x2000,'slot_bytes':128,'cpu_count':8,
 'arm_ns':12150000000,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage "complete"
echo "Phase354 final idle/timer frontier build: PASS"
trap - EXIT
