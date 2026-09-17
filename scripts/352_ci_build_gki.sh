#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase352-gki-out"
FAIL="$PWD/phase352-gki-failure"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
CORE="$ROOT/drivers/cpuidle/cpuidle.c"
IDLE="$ROOT/kernel/sched/idle.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase352: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase352-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p352-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/352_apply_cpuidle_identity.py scripts/352_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" "$FAIL/source/" || true
  [ -f "$CORE" ] && cp "$CORE" "$FAIL/source/" || true
  [ -f "$IDLE" ] && cp "$IDLE" "$FAIL/source/" || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE352_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase352: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase352-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$REC" "$CORE" "$IDLE"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'int cpuidle_enter(' "$CORE"
grep -Fq 'target_state->enter(dev, drv, index)' "$CORE"
grep -Fq 'cpuidle_not_available(drv, dev)' "$IDLE"
grep -Fq 'cpuidle_select(drv, dev, &stop_tick)' "$IDLE"

cp phase346-gki-out/config/final.config /tmp/p352-base.config
cp "$REC" /tmp/p352-rec-before
cp "$CORE" /tmp/p352-core-before
cp "$IDLE" /tmp/p352-idle-before

stage "apply generic cpuidle identity"
python3 -m py_compile scripts/352_apply_cpuidle_identity.py
python3 scripts/352_apply_cpuidle_identity.py --root "$ROOT"
python3 scripts/352_apply_cpuidle_identity.py --root "$ROOT" --check-only
! cmp -s /tmp/p352-rec-before "$REC"
! cmp -s /tmp/p352-core-before "$CORE"
! cmp -s /tmp/p352-idle-before "$IDLE"

git diff --no-index --check /tmp/p352-rec-before "$REC" > /tmp/p352-rec-check 2>&1 || true
git diff --no-index --check /tmp/p352-core-before "$CORE" > /tmp/p352-core-check 2>&1 || true
git diff --no-index --check /tmp/p352-idle-before "$IDLE" > /tmp/p352-idle-check 2>&1 || true
test ! -s /tmp/p352-rec-check
test ! -s /tmp/p352-core-check
test ! -s /tmp/p352-idle-check

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
r=Path('gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
c=Path('gki/common/drivers/cpuidle/cpuidle.c').read_text()
i=Path('gki/common/kernel/sched/idle.c').read_text()
bc=Path('/tmp/p352-core-before').read_text()
bi=Path('/tmp/p352-idle-before').read_text()
for t in (
 'A52_PHASE352_CPUIDLE_IDENTITY_V1',
 'A52_R352_SIDEBAND_PHYS  0xB1BF0000ULL',
 'A52_R352_COMMIT         0x352c0de5U',
 'a52_r352_start();',
 'a52_p352_mark_first(1U', 'a52_p352_mark_first(3U',
 'a52_p352_mark_first(4U + (u32)index',
 'a52_p352_mark_first(12U', 'a52_p352_mark_first(14U',
):
 if t not in r+c+i: raise SystemExit('Phase352 missing '+t)
for t in (
 'target_state->enter(dev, drv, index)',
 'cpuidle_state_is_coupled(drv, index)',
 'arch_cpu_idle();',
 'cpuidle_select(drv, dev, &stop_tick)',
):
 if (c+i).count(t) != (bc+bi).count(t):
  raise SystemExit('Phase352 changed protected call count '+t)
print('Phase352 scope audit: PASS')
PY

stage "config"
cp /tmp/p352-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase352-olddefconfig.log 2>&1
cmp -s /tmp/p352-base.config "$BUILD/.config"
grep -q '^CONFIG_CPU_IDLE=y$' "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase352-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p352_mark_first$' "$SYSTEM_MAP"
grep -aFq 'P276 352A map=1' "$IMAGE"

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase352-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/352_apply_cpuidle_identity.py scripts/352_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p352-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$CORE" "$IDLE" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE352-SCHEMA.txt" <<'EOF'
Phase352 generic cpuidle identity probe
=======================================
Base: Phase346.

Purpose:
Phase351 proved the cpuidle-psci/domain implementation is not entered before
our ~12 s global freeze. Phase352 probes the generic scheduler/cpuidle layer to
identify the backend actually selected at runtime without logging every idle
cycle.

Raw physical range: 0xB1BF0000..0xB1BF3FFF
copy A: +0x0000 (8 KiB)
copy B: +0x2000 (8 KiB)
16 events x 8 CPUs x 64 bytes = 8 KiB per mirror.
Each event+CPU cell is written only ONCE.

slot:
 u64 magic
 u64 ns
 u64 sequence
 u64 callback
 u32 event
 u32 cpu
 s32 index
 s32 ret
 u32 flags
 u32 drv_sig   # first 4 bytes of driver name, little endian
 u32 commit
 u32 version

magic  = 0x323533454c444943
commit = 0x352c0de5
version= 1

events:
 0 INIT
 1 DEFAULT_ARCH_IDLE_PRE
 2 DEFAULT_ARCH_IDLE_POST
 3 CPUIDLE_NOT_AVAILABLE
 4 STATE0_FIRST_USE
 5 STATE1_FIRST_USE
 6 STATE2_FIRST_USE
 7 STATE3_FIRST_USE
 8 STATE4_FIRST_USE
 9 STATE5_FIRST_USE
10 STATE6_FIRST_USE
11 STATE7_FIRST_USE
12 FIRST_DIRECT_BACKEND_ENTER
13 FIRST_DIRECT_BACKEND_RETURN
14 FIRST_GOVERNOR_SELECTION  index=selected state ret=stop_tick(0/1)
15 FIRST_CPUIDLE_WRAPPER

For events 4..15, callback is the registered state's enter() function when
available. drv_sig identifies the registered driver without copying strings.

Interpretation:
- event3 + event1/2, with no state events: cpuidle framework unavailable and
  generic arch_cpu_idle is the real path.
- state events 4..11: these are the actual idle state indexes being used.
- event12 gives the first directly invoked target_state->enter callback.
- If event12 is present but Phase351 was empty, the callback belongs to a
  non-PSCI backend; map callback against System.map/source and instrument only
  that backend in Phase353.

This is an identity phase, intentionally first-occurrence-only to avoid making
persistent cache flushes on every idle cycle and perturbing the failure.
EOF

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase352-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'352','name':'CPUIDLE-IDENTITY-V1','base_phase':'346',
 'hardware_validated':False,
 'sideband_phys':'0xB1BF0000','sideband_bytes':0x4000,
 'event_count':16,'cpu_count':8,'slot_bytes':64,
 'first_occurrence_only':True,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage "complete"
echo "Phase352 generic cpuidle identity build: PASS"
trap - EXIT
