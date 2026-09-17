#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase357-gki-out"
FAIL="$PWD/phase357-gki-failure"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
EXIT="$ROOT/kernel/exit.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase357: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase357-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p357-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/357_apply_init_exit_cause.py scripts/357_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" "$FAIL/source/" || true
  [ -f "$EXIT" ] && cp "$EXIT" "$FAIL/source/" || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE357_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase357: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase357-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$REC" "$EXIT"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'Attempted to kill init! exitcode=0x%08x' "$EXIT"
grep -Fq 'void do_exit(long code)' "$EXIT"
grep -Fq 'void do_group_exit(int exit_code)' "$EXIT"
grep -Fq 'void make_task_dead(int signr)' "$EXIT"

cp phase346-gki-out/config/final.config /tmp/p357-base.config
cp "$REC" /tmp/p357-rec-before
cp "$EXIT" /tmp/p357-exit-before

stage "apply init-exit cause probe"
python3 -m py_compile scripts/357_apply_init_exit_cause.py
python3 scripts/357_apply_init_exit_cause.py --root "$ROOT"
python3 scripts/357_apply_init_exit_cause.py --root "$ROOT" --check-only
! cmp -s /tmp/p357-rec-before "$REC"
! cmp -s /tmp/p357-exit-before "$EXIT"

git diff --no-index --check /tmp/p357-rec-before "$REC" > /tmp/p357-rec-check 2>&1 || true
git diff --no-index --check /tmp/p357-exit-before "$EXIT" > /tmp/p357-exit-check 2>&1 || true
test ! -s /tmp/p357-rec-check
test ! -s /tmp/p357-exit-check

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
r=Path('gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
e=Path('gki/common/kernel/exit.c').read_text()
be=Path('/tmp/p357-exit-before').read_text()
for tok in (
 'A52_PHASE357_INIT_EXIT_CAUSE_V1',
 'A52_R357_SIDEBAND_PHYS   0xB1BF0000ULL',
 'A52_R357_COMMIT          0x357c0de5U',
 'a52_r357_start();',
 'a52_p357_note_do_exit(code);',
 'a52_p357_note_group_exit(exit_code);',
 'a52_p357_note_make_task_dead(signr);',
 'a52_p357_persist_init_panic(code,',
 'Attempted to kill init! exitcode=0x%08x',
):
 if tok not in r+e: raise SystemExit('Phase357 missing '+tok)
if e.count('panic("Attempted to kill init! exitcode=0x%08x') != be.count('panic("Attempted to kill init! exitcode=0x%08x'):
 raise SystemExit('Phase357 changed protected init-panic call count')
print('Phase357 scope audit: PASS')
PY

stage "config"
cp /tmp/p357-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase357-olddefconfig.log 2>&1
cmp -s /tmp/p357-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase357-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p357_persist_init_panic$' "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p357_note_do_exit$' "$SYSTEM_MAP"
grep -aFq 'P276 357A map=1' "$IMAGE"
grep -E '[[:space:]](_text|do_exit|do_group_exit|make_task_dead|panic|a52_p357_persist_init_panic)$' "$SYSTEM_MAP" > /tmp/p357-symbols.txt || true

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase357-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/357_apply_init_exit_cause.py scripts/357_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p357-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$EXIT" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE357-SCHEMA.txt" <<'SCHEMA'
Phase357 PID1/init exit-cause recorder
======================================
Base: Phase346.

Why
---
Phase355/356 showed that the surviving CPU is PID1/init executing panic()'s
final mdelay(PANIC_TIMER_STEP) loop. Phase356's saved stack return address
symbolicates to panic+0x300 immediately after __const_udelay().

Purpose
-------
Persist the state by which PID1 reaches Linux do_exit() and, most importantly,
record the values immediately before the global-init branch calls:

  panic("Attempted to kill init! exitcode=0x%08x\n", ...)

If a valid Phase357 record exists, that directly proves this is the panic path.

Sideband
--------
Physical: 0xB1BF0000..0xB1BF7FFF (32 KiB)
copy A: raw 0xF0000, 16 KiB
copy B: raw 0xF4000, 16 KiB
record size: 256 bytes
64 replicated records in each copy.

The same final record is deliberately written to every slot in both mirrors so
pmsg clear-bit corruption cannot easily erase the diagnosis.

Record fields
-------------
 u64 magic = 0x3735335449584549
 u64 ns
 u64 sequence
 u64 runtime_text
 u64 do_exit_code
 u64 panic_code
 u64 jobctl
 u64 mm
 u64 task_flags
 u32 note_mask
 u32 cpu
 u32 pid
 u32 tgid
 s32 group_exit_code
 s32 task_exit_code
 s32 do_group_exit_code
 s32 make_task_dead_signr
 u32 signal_flags
 s32 exit_signal
 u32 pending_signal
 u32 fatal_signal
 s32 signal_live
 u32 commit = 0x357c0de5
 u32 version = 1
 char comm[16]

note_mask
---------
 bit0: PID1 entered do_group_exit()
 bit1: PID1 entered do_exit()
 bit2: PID1 entered make_task_dead()
 bit3: global-init panic branch reached

Interpretation
--------------
- bit3 present: global-init panic is directly proven.
- make_task_dead_signr nonzero / bit2: init died through a fatal exception/signal
  path; the signr becomes the next focus.
- do_group_exit bit set: explicit/group exit path reached; inspect exit code.
- exit codes use Linux wait-status encoding. A low 7-bit signal component can
  identify a terminating signal; ordinary exit status is encoded in bits 8+.
SCHEMA

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase357-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'357','name':'INIT-EXIT-CAUSE-V1','base_phase':'346',
 'hardware_validated':False,
 'sideband_phys':'0xB1BF0000','sideband_bytes':0x8000,
 'copy_bytes':0x4000,'slot_bytes':256,'replicas_per_copy':64,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage "complete"
echo "Phase357 init-exit cause build: PASS"
trap - EXIT
