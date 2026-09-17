#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase355-gki-out"
FAIL="$PWD/phase355-gki-failure"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
TIMER="$ROOT/drivers/clocksource/arm_arch_timer.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase355: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase355-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p355-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/355_apply_timer_pc_sampler.py scripts/355_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" "$FAIL/source/" || true
  [ -f "$TIMER" ] && cp "$TIMER" "$FAIL/source/" || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE355_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase355: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase355-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$REC" "$TIMER"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'static __always_inline irqreturn_t timer_handler' "$TIMER"
grep -Fq 'evt->event_handler(evt);' "$TIMER"

cp phase346-gki-out/config/final.config /tmp/p355-base.config
cp "$REC" /tmp/p355-rec-before
cp "$TIMER" /tmp/p355-timer-before

stage "apply timer PC sampler"
python3 -m py_compile scripts/355_apply_timer_pc_sampler.py
python3 scripts/355_apply_timer_pc_sampler.py --root "$ROOT"
python3 scripts/355_apply_timer_pc_sampler.py --root "$ROOT" --check-only
! cmp -s /tmp/p355-rec-before "$REC"
! cmp -s /tmp/p355-timer-before "$TIMER"

git diff --no-index --check /tmp/p355-rec-before "$REC" > /tmp/p355-rec-check 2>&1 || true
git diff --no-index --check /tmp/p355-timer-before "$TIMER" > /tmp/p355-timer-check 2>&1 || true
test ! -s /tmp/p355-rec-check
test ! -s /tmp/p355-timer-check

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
r=Path('gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
t=Path('gki/common/drivers/clocksource/arm_arch_timer.c').read_text()
bt=Path('/tmp/p355-timer-before').read_text()
for tok in (
 'A52_PHASE355_TIMER_PC_SAMPLER_V1',
 'A52_R355_SIDEBAND_PHYS   0xB1BF0000ULL',
 'A52_R355_SIDEBAND_BYTES  0x8000U',
 'A52_R355_COMMIT          0x355c0de5U',
 'A52_R355_ARM_NS          12150000000ULL',
 'A52_R355_BURST_SAMPLES   64U',
 'A52_R355_SPARSE_DIV      250U',
 'a52_r355_start();','a52_p355_timer_sample();','get_irq_regs();',
 'regs->pc','regs->regs[30]','current->comm',
):
 if tok not in r+t: raise SystemExit('Phase355 missing '+tok)
if t.count('evt->event_handler(evt);') != bt.count('evt->event_handler(evt);'):
 raise SystemExit('Phase355 changed protected timer callback count')
print('Phase355 scope audit: PASS')
PY

stage "config"
cp /tmp/p355-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase355-olddefconfig.log 2>&1
cmp -s /tmp/p355-base.config "$BUILD/.config"
grep -q '^CONFIG_ARM_ARCH_TIMER=y$' "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase355-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p355_timer_sample$' "$SYSTEM_MAP"
grep -aFq 'P276 355A map=1' "$IMAGE"

grep -E '[[:space:]](_text|a52_p355_timer_sample|timer_handler|arch_timer_handler_virt|arch_timer_handler_phys)$' "$SYSTEM_MAP" > /tmp/p355-symbols.txt || true

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase355-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/355_apply_timer_pc_sampler.py scripts/355_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p355-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$TIMER" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE355-SCHEMA.txt" <<'SCHEMA'
Phase355 timer interrupted-PC sampler
=====================================
Base: Phase346.

Why
---
Phase354 showed that after 12.150 s the surviving timer CPU continued servicing
and returning from the ARM architected timer at ~250 Hz for hundreds of seconds,
while it never reached arch_cpu_idle().  Phase355 samples the execution context
that each timer IRQ interrupted so we can identify what that CPU is actually
running underneath the IRQs.

Sideband
--------
Physical: 0xB1BF0000..0xB1BF7FFF (32 KiB)
copy A: +0x0000..+0x3fff, raw offset 0xF0000
copy B: +0x4000..+0x7fff, raw offset 0xF4000
slot size: 128 bytes
sample slots: 127 (0..126)
meta: copy_base + 0x3f80
arm time: 12.150 s

Owner selection
---------------
The first CPU delivering the architected timer after arm becomes owner. Only
that CPU is sampled. This avoids assuming CPU6 will always be the survivor.

Sampling cadence
----------------
slots 0..63   : first 64 consecutive timer IRQs (~256 ms at 250 Hz)
slots 64..125 : one sample per 250 timer IRQs, seconds 1..62
slot 126      : rolling latest one-per-second sample after second 62

Sample struct, little endian
----------------------------
 u64 magic        = 0x353533504d415354
 u64 ns
 u64 pc            # interrupted PC from get_irq_regs()
 u64 lr            # interrupted x30
 u64 sp
 u64 pstate
 u64 runtime_text  # runtime address of kernel _text
 u64 sequence      # A52 flight-recorder sequence
 u64 tick_index
 u32 cpu
 u32 pid
 u32 tgid
 u32 preempt
 u32 task_flags
 u32 sample_flags  # bit0 regs valid; bit1 user; bit2 current->mm != NULL
 u32 sample_class  # 0 burst; 1 fixed sparse; 2 rolling latest
 u32 slot_index
 u32 commit        = 0x355c0de5
 u32 version       = 1
 char comm[16]

Symbolication
-------------
For a kernel-mode sample, calculate:
 link_pc = System.map[_text] + (sample.pc - sample.runtime_text)
then resolve link_pc against phase355-gki-out/compile/System.map.

Interpretation
--------------
- Repeated kernel PC/function + same task: likely spin/livelock target.
- PCs moving inside one call chain/task: likely busy loop or retry path.
- User-mode PC + one process: investigate that userspace task and why other
  CPUs/system services stopped.
- Different tasks/PCs continue scheduling: the perceived freeze is elsewhere;
  pivot to display/userspace dependency rather than CPU execution.
SCHEMA

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase355-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'355','name':'TIMER-PC-SAMPLER-V1','base_phase':'346',
 'hardware_validated':False,
 'sideband_phys':'0xB1BF0000','sideband_bytes':0x8000,
 'copy_bytes':0x4000,'slot_bytes':128,'sample_slots':127,
 'arm_ns':12150000000,'burst_samples':64,'sparse_div':250,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage "complete"
echo "Phase355 timer PC sampler build: PASS"
trap - EXIT
