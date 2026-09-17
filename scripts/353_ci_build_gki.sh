#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase353-gki-out"
FAIL="$PWD/phase353-gki-failure"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
IDLE="$ROOT/kernel/sched/idle.c"
TIMER="$ROOT/drivers/clocksource/arm_arch_timer.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase353: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase353-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p353-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/353_apply_late_idle_timer_frontier.py scripts/353_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" "$FAIL/source/" || true
  [ -f "$IDLE" ] && cp "$IDLE" "$FAIL/source/" || true
  [ -f "$TIMER" ] && cp "$TIMER" "$FAIL/source/" || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE353_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase353: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase353-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$REC" "$IDLE" "$TIMER"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'cpuidle_not_available(drv, dev)' "$IDLE"
grep -Fq 'arch_cpu_idle();' "$IDLE"
grep -Fq 'static __always_inline irqreturn_t timer_handler' "$TIMER"
grep -Fq 'evt->event_handler(evt);' "$TIMER"

cp phase346-gki-out/config/final.config /tmp/p353-base.config
cp "$REC" /tmp/p353-rec-before
cp "$IDLE" /tmp/p353-idle-before
cp "$TIMER" /tmp/p353-timer-before

stage "apply late idle/timer frontier"
python3 -m py_compile scripts/353_apply_late_idle_timer_frontier.py
python3 scripts/353_apply_late_idle_timer_frontier.py --root "$ROOT"
python3 scripts/353_apply_late_idle_timer_frontier.py --root "$ROOT" --check-only
! cmp -s /tmp/p353-rec-before "$REC"
! cmp -s /tmp/p353-idle-before "$IDLE"
! cmp -s /tmp/p353-timer-before "$TIMER"

git diff --no-index --check /tmp/p353-rec-before "$REC" > /tmp/p353-rec-check 2>&1 || true
git diff --no-index --check /tmp/p353-idle-before "$IDLE" > /tmp/p353-idle-check 2>&1 || true
git diff --no-index --check /tmp/p353-timer-before "$TIMER" > /tmp/p353-timer-check 2>&1 || true
test ! -s /tmp/p353-rec-check
test ! -s /tmp/p353-idle-check
test ! -s /tmp/p353-timer-check

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
r=Path('gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
i=Path('gki/common/kernel/sched/idle.c').read_text()
t=Path('gki/common/drivers/clocksource/arm_arch_timer.c').read_text()
bi=Path('/tmp/p353-idle-before').read_text()
bt=Path('/tmp/p353-timer-before').read_text()
for tok in (
 'A52_PHASE353_LATE_IDLE_TIMER_FRONTIER_V1',
 'A52_R353_SIDEBAND_PHYS   0xB1BF4000ULL',
 'A52_R353_COMMIT          0x353c0de5U',
 '10500000000ULL','11000000000ULL','11500000000ULL','11700000000ULL',
 '11850000000ULL','12000000000ULL','12150000000ULL','12200000000ULL',
 'a52_r353_start();','a52_p353_mark_late(0U);','a52_p353_mark_late(1U);',
 'a52_p353_mark_late(2U);','a52_p353_mark_late(3U);','a52_p353_cpuidle_identity',
):
 if tok not in r+i+t: raise SystemExit('Phase353 missing '+tok)
for tok in ('arch_cpu_idle();','cpuidle_not_available(drv, dev)','evt->event_handler(evt);'):
 if (i+t).count(tok) != (bi+bt).count(tok):
  raise SystemExit('Phase353 changed protected call count '+tok)
print('Phase353 scope audit: PASS')
PY

stage "config"
cp /tmp/p353-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase353-olddefconfig.log 2>&1
cmp -s /tmp/p353-base.config "$BUILD/.config"
grep -q '^CONFIG_CPU_IDLE=y$' "$BUILD/.config"
grep -q '^CONFIG_ARM_ARCH_TIMER=y$' "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase353-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p353_mark_late$' "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p353_cpuidle_identity$' "$SYSTEM_MAP"
grep -aFq 'P276 353A map=1' "$IMAGE"

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase353-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/353_apply_late_idle_timer_frontier.py scripts/353_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p353-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$IDLE" "$TIMER" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE353-SCHEMA.txt" <<'SCHEMA'
Phase353 late architectural-idle / architected-timer frontier
=============================================================
Base: Phase346.

Why this phase exists
---------------------
Phase352 proved the generic cpuidle framework is unavailable on every observed
CPU and that the real idle fallback is arch_cpu_idle(). Phase353 samples the
late boundary around the reproducible ~12 s global freeze without logging each
idle cycle.

Dedicated sideband
------------------
Physical: 0xB1BF4000..0xB1BF7FFF (16 KiB)
copy A: +0x0000 (8 KiB)
copy B: +0x2000 (8 KiB)
With a full 1 MiB raw ramoops snapshot rooted at 0xB1B00000:
copy A raw offset = 0xF4000
copy B raw offset = 0xF6000

Timing matrix in each copy: +0x0000..+0x0fff
8 buckets x 8 CPUs x 64-byte slots.
slot offset = copy_base + ((bucket * 8 + cpu) * 64)

Thresholds / buckets:
0  10.500 s
1  11.000 s
2  11.500 s
3  11.700 s
4  11.850 s
5  12.000 s
6  12.150 s
7  12.200 s

Timing slot, little endian:
 u64 magic           = 0x3335334c44495441
 u64 threshold_ns
 u64 arch_enter_ns   # first arch_cpu_idle entry at/after threshold
 u64 arch_return_ns  # first arch_cpu_idle return at/after threshold
 u64 timer_enter_ns  # first valid architected timer callback entry at/after threshold
 u64 timer_return_ns # first valid architected timer callback return at/after threshold
 u32 cpu
 u16 bucket
 u16 reserved
 u32 commit          = 0x353c0de5
 u32 version         = 1

Each event field is written at most once for each CPU/bucket.

Late cpuidle identity
---------------------
Per-copy offset +0x1000, one 64-byte slot per CPU. It is deliberately not
recorded before 10.500 s, so it reports the cpuidle state close to the failure.
reason bits:
 bit0: cpuidle driver is NULL
 bit1: cpuidle device is NULL
 bit2: cpuidle device exists but is disabled
Fields also include driver state_count and dev_enabled.

Meta slot
---------
Per-copy offset +0x1800. Magic 0x3335334154454d50, init timestamp, sideband
physical address, sequence, commit/version, bucket count and CPU count.

Interpretation
--------------
- arch_enter != 0, arch_return == 0 at the latest populated bucket, followed by
  no timer_enter: strong evidence that the CPU entered architectural idle/WFI
  and never received a wake interrupt.
- timer_enter != 0, timer_return == 0: architected timer IRQ arrived but its
  clockevent callback did not return; pivot into tick/hrtimer callback path.
- timer enter/return and arch enter/return continue through 12.200 s: the
  simple WFI/timer-wake hypothesis is not sufficient; move toward GIC/other IRQ,
  firmware/PSCI side effects, or a SoC/interconnect-wide stall.
- Do not require all CPUs to have all buckets: idle/timer activity is naturally
  per-CPU and some CPUs may be busy/offline.
SCHEMA

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase353-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'353','name':'LATE-IDLE-TIMER-FRONTIER-V1','base_phase':'346',
 'hardware_validated':False,
 'sideband_phys':'0xB1BF4000','sideband_bytes':0x4000,
 'copy_bytes':0x2000,'slot_bytes':64,'buckets':8,'cpu_count':8,
 'threshold_ns':[10500000000,11000000000,11500000000,11700000000,11850000000,12000000000,12150000000,12200000000],
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage "complete"
echo "Phase353 late idle/timer frontier build: PASS"
trap - EXIT
