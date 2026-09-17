#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase350-gki-out"
FAIL="$PWD/phase350-gki-failure"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
STOP="$ROOT/kernel/stop_machine.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase350: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase350-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p350-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/350_apply_global_stop_frontier.py scripts/350_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  for f in "$REC" "$STOP"; do [ -f "$f" ] && cp "$f" "$FAIL/source/$(basename "$f")" || true; done
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE350_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase350: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase350-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$REC" "$STOP"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'static int multi_cpu_stop(void *data)' "$STOP"
grep -Fq 'int stop_cpus(const struct cpumask *cpumask' "$STOP"

cp phase346-gki-out/config/final.config /tmp/p350-base.config
cp "$REC" /tmp/p350-rec-before
cp "$STOP" /tmp/p350-stop-before

stage "apply global-stop frontier"
python3 -m py_compile scripts/350_apply_global_stop_frontier.py
python3 scripts/350_apply_global_stop_frontier.py --root "$ROOT"
python3 scripts/350_apply_global_stop_frontier.py --root "$ROOT" --check-only
! cmp -s /tmp/p350-rec-before "$REC"
! cmp -s /tmp/p350-stop-before "$STOP"
git diff --no-index --check /tmp/p350-rec-before "$REC" > /tmp/p350-rec-check 2>&1 || true
git diff --no-index --check /tmp/p350-stop-before "$STOP" > /tmp/p350-stop-check 2>&1 || true
test ! -s /tmp/p350-rec-check
test ! -s /tmp/p350-stop-check

stage "scope audit"
python3 - "$REC" "$STOP" <<'PY'
from pathlib import Path
import sys
r=Path(sys.argv[1]).read_text(); s=Path(sys.argv[2]).read_text()
for token in (
 'A52_PHASE350_GLOBAL_STOP_FRONTIER_V1',
 'A52_R350_SIDEBAND_PHYS  0xB1BF0000ULL',
 'A52_R350_SIDEBAND_BYTES 0x4000U',
 'A52_R350_EVENT_COUNT    16U',
 'A52_R350_CPU_COUNT      8U',
 'A52_R350_MAGIC          0x303533504f545347ULL',
 'A52_R350_COMMIT         0x350c0de5U',
 'a52_r350_start();',
 'a52_p350_mark(4U, (u32)curstate);',
 'a52_p350_mark(12U, (u32)(unsigned long)fn);',
):
 if token not in r+s: raise SystemExit('Phase350 missing '+token)
print('Phase350 scope audit: PASS')
PY

stage "config"
cp /tmp/p350-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase350-olddefconfig.log 2>&1
cmp -s /tmp/p350-base.config "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase350-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p350_mark$' "$SYSTEM_MAP"
grep -aFq 'P276 350A map=1' "$IMAGE"

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase350-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/350_apply_global_stop_frontier.py scripts/350_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p350-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$STOP" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE350-SCHEMA.txt" <<'EOF'
Phase350 global-stop frontier
=============================
Base: Phase346.

Physical sideband: 0xB1BF0000..0xB1BF3FFF
copy A: +0x0000, 8 KiB
copy B: +0x2000, 8 KiB

Fixed matrix: event x CPU, 16 events x 8 CPUs x 64 bytes = 8 KiB/copy.
Each cell is overwritten, so it contains the LAST occurrence before the stall.

slot:
 u64 magic
 u64 ns
 u64 sequence
 u64 jiffies64
 u32 event
 u32 cpu
 u32 pid
 u32 tgid
 u32 state       (preempt_count)
 u32 arg0
 u32 commit
 u32 version

magic=0x303533504f545347
commit=0x350c0de5
version=1

events:
 0 INIT
 1 STOP_CPUS_ENTER       arg0=cpumask weight
 2 STOP_CPUS_EXIT        arg0=ret
 3 MULTI_CPU_STOP_ENTER
 4 MULTI_CPU_STOP_STATE  arg0=enum multi_stop_state, latest state wins
 5 MULTI_CPU_STOP_EXIT   arg0=err
 6 STOP_MACHINE_CPUSLOCKED_ENTER arg0=num_threads
 8 STOP_MACHINE_ENTER
 9 STOP_MACHINE_EXIT     arg0=ret
10 INACTIVE_CPU_STOP_ENTER
11 INACTIVE_CPU_STOP_EXIT arg0=ret
12 STOPPER_WORK_ENTER    arg0=low 32 bits callback address
13 STOPPER_WORK_EXIT     arg0=ret
EOF

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase350-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'350','name':'GLOBAL-STOP-FRONTIER-V1','base_phase':'346',
 'hardware_validated':False,'sideband_phys':'0xB1BF0000','sideband_bytes':0x4000,
 'slot_bytes':64,'event_count':16,'cpu_count':8,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
stage "complete"
echo "Phase350 global-stop frontier build: PASS"
trap - EXIT
