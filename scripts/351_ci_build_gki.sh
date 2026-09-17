#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
BUILD="$PWD/workspace/gki-phase199-out"
OUT="$PWD/phase351-gki-out"
FAIL="$PWD/phase351-gki-failure"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
PSCI="$ROOT/drivers/cpuidle/cpuidle-psci.c"

STAGE=startup
stage(){ STAGE="$1"; echo "== Phase351: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source,compile}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase351-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p351-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/351_apply_psci_idle_frontier.py scripts/351_ci_build_gki.sh "$FAIL/audit/" 2>/dev/null || true
  [ -f "$REC" ] && cp "$REC" "$FAIL/source/" || true
  [ -f "$PSCI" ] && cp "$PSCI" "$FAIL/source/" || true
  [ -s "$BUILD/arch/arm64/boot/Image" ] && cp "$BUILD/arch/arm64/boot/Image" "$FAIL/compile/Image" || true
  [ -s "$BUILD/System.map" ] && cp "$BUILD/System.map" "$FAIL/compile/System.map" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "obtain Phase346 baseline"
if [ "${PHASE351_PREWARMED_PHASE346:-0}" = "1" ]; then
  echo "Phase351: using validated cached Phase346 baseline"
else
  PHASE346_PREWARMED_PHASE345=1 bash scripts/346_ci_build_gki.sh 2>&1 | tee phase351-phase346.log
fi

for f in phase346-gki-out/package/boot.img phase346-gki-out/compile/Image phase346-gki-out/config/final.config "$REC" "$PSCI"; do
  test -s "$f"
done
test "$(stat -c '%s' phase346-gki-out/package/boot.img)" -eq 100663296
grep -Fq 'A52_PHASE346_DMA_RAW_SIDEBAND_V1' gki/common/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c
grep -Fq 'A52_PHASE343_INSTRUCTION_COUNTER_FRONTIER_V1' "$REC"
grep -Fq 'psci_enter_domain_idle_state' "$PSCI"
grep -Fq 'pm_runtime_put_sync_suspend(pd_dev)' "$PSCI"
grep -Fq 'psci_cpu_suspend_enter(state)' "$PSCI"

cp phase346-gki-out/config/final.config /tmp/p351-base.config
cp "$REC" /tmp/p351-rec-before
cp "$PSCI" /tmp/p351-psci-before

stage "apply PSCI idle frontier"
python3 -m py_compile scripts/351_apply_psci_idle_frontier.py
python3 scripts/351_apply_psci_idle_frontier.py --root "$ROOT"
python3 scripts/351_apply_psci_idle_frontier.py --root "$ROOT" --check-only
! cmp -s /tmp/p351-rec-before "$REC"
! cmp -s /tmp/p351-psci-before "$PSCI"

git diff --no-index --check /tmp/p351-rec-before "$REC" > /tmp/p351-rec-check 2>&1 || true
git diff --no-index --check /tmp/p351-psci-before "$PSCI" > /tmp/p351-psci-check 2>&1 || true
test ! -s /tmp/p351-rec-check
test ! -s /tmp/p351-psci-check

stage "scope audit"
python3 - <<'PY'
from pathlib import Path
r=Path('gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
p=Path('gki/common/drivers/cpuidle/cpuidle-psci.c').read_text()
b=Path('/tmp/p351-psci-before').read_text()
for t in (
 'A52_PHASE351_PSCI_IDLE_FRONTIER_V1',
 'A52_R351_SIDEBAND_PHYS  0xB1BF4000ULL',
 'A52_R351_COMMIT         0x351c0de5U',
 'a52_r351_start();',
 'a52_p351_mark(3U', 'a52_p351_mark(6U', 'a52_p351_mark(8U',
 'a52_p351_mark(10U', 'a52_p351_mark(12U', 'a52_p351_mark(14U',
):
 if t not in r+p: raise SystemExit('Phase351 missing '+t)
for t in (
 'cpu_pm_enter()', 'pm_runtime_put_sync_suspend(pd_dev)',
 'psci_cpu_suspend_enter(state)', 'pm_runtime_get_sync(pd_dev)',
 'cpu_pm_exit()', 'psci_enter_state(idx, state[idx])',
 'pm_runtime_put_sync(pd_dev)',
):
 if p.count(t) != b.count(t):
  raise SystemExit('Phase351 changed protected call count '+t)
print('Phase351 scope audit: PASS')
PY

stage "config"
cp /tmp/p351-base.config "$BUILD/.config"
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig > phase351-olddefconfig.log 2>&1
cmp -s /tmp/p351-base.config "$BUILD/.config"
grep -q '^CONFIG_ARM_PSCI_CPUIDLE=y$' "$BUILD/.config"
grep -q '^CONFIG_ARM_PSCI_CPUIDLE_DOMAIN=y$' "$BUILD/.config"
grep -q '^CONFIG_PM_GENERIC_DOMAINS=y$' "$BUILD/.config"
grep -q '^CONFIG_QCOM_RPMH=y$' "$BUILD/.config"

stage "compile"
set +e
make -C "$ROOT" O="$BUILD" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 -j"$(nproc)" Image 2>&1 | tee phase351-compile.log
rc=${PIPESTATUS[0]}; set -e; test "$rc" -eq 0

IMAGE="$BUILD/arch/arm64/boot/Image"
SYSTEM_MAP="$BUILD/System.map"
test -s "$IMAGE"; test -s "$SYSTEM_MAP"
grep -Eq '[[:space:]]a52_p351_mark$' "$SYSTEM_MAP"
grep -aFq 'P276 351A map=1' "$IMAGE"

stage "package"
rm -rf "$OUT"; mkdir -p "$OUT"/{compile,config,package,audit,source}
cp "$IMAGE" "$SYSTEM_MAP" "$OUT/compile/"
cp "$BUILD/.config" "$OUT/config/final.config"
cp phase351-*.log "$OUT/audit/" 2>/dev/null || true
cp scripts/351_apply_psci_idle_frontier.py scripts/351_ci_build_gki.sh "$OUT/audit/"
cp /tmp/p351-* "$OUT/audit/" 2>/dev/null || true
cp "$REC" "$PSCI" "$OUT/source/"
gzip -n -c "$IMAGE" > "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source phase346-gki-out/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
test "$(stat -c '%s' "$OUT/package/boot.img")" -eq 100663296

cat > "$OUT/PHASE351-SCHEMA.txt" <<'EOF'
Phase351 PSCI idle / power-domain frontier
==========================================
Base: Phase346.

Motivation:
Phase350 proved no stop_machine/cpu-stopper path ran before the ~12 s freeze.
TouchGrass 4.19 uses ARM_CPUIDLE + MSM_PM/QTI_SYSTEM_PM; this 5.10 GKI uses
ARM_PSCI_CPUIDLE_DOMAIN and hierarchical runtime-PM/genpd coordination.

Raw physical range: 0xB1BF4000..0xB1BF7FFF
copy A: +0x0000 (8 KiB)
copy B: +0x2000 (8 KiB)
16 events x 8 CPUs x 64 bytes = 8 KiB per mirror.
Each cell stores the LAST occurrence of event+CPU.

slot:
 u64 magic
 u64 ns
 u64 sequence
 u64 jiffies64
 u32 event
 u32 cpu
 u32 pid
 u32 tgid
 u32 state
 u32 arg0
 u32 commit
 u32 version

magic  = 0x313533454c444950
commit = 0x351c0de5
version= 1

events:
 0 INIT
 1 SIMPLE_DEEPEST_ENTER   state=PSCI state arg0=idx
 2 SIMPLE_DEEPEST_RETURN  state=PSCI state arg0=ret
 3 DOMAIN_ENTER           state=idx
 4 CPU_PM_ENTER_PRE       state=idx
 5 CPU_PM_ENTER_POST      state=idx arg0=ret
 6 RPM_PUT_PRE            state=idx
 7 RPM_PUT_POST           state=idx arg0=pm_runtime return
 8 PSCI_ENTER             state=resolved PSCI state arg0=idx
 9 PSCI_RETURN            state=resolved PSCI state arg0=ret
10 RPM_GET_PRE            state=resolved PSCI state
11 RPM_GET_POST           state=resolved PSCI state arg0=pm_runtime return
12 CPU_PM_EXIT_PRE        state=resolved PSCI state
13 DOMAIN_RETURN          state=resolved PSCI state arg0=ret
14 CPUHP_ENTER            state=cpu arg0=1 up / 2 down
15 CPUHP_RETURN           state=cpu arg0 high=direction low16=ret

Interpretation examples:
- event8 newer than event9 on a CPU => final PSCI suspend call did not return.
- event6 newer than event7 => runtime-PM/genpd suspend did not return.
- event10 newer than event11 => runtime-PM/genpd resume did not return.
- clean event13 after every event3 => hierarchical PSCI idle path returned cleanly;
  then move below/aside to GIC/arch timer/RPMh/interconnect/firmware pathways.
EOF

cat > "$OUT/TOUCHGRASS-LOW-POWER-COMPARE.txt" <<'EOF'
TouchGrass vs GKI low-power architecture
========================================
TouchGrass pinned A52 defconfig:
  CONFIG_CPU_IDLE=y
  CONFIG_ARM_CPUIDLE=y
  CONFIG_MSM_PM=y
  CONFIG_QTI_SYSTEM_PM=y
  CONFIG_QCOM_RPMH=y
  CONFIG_QCOM_QMI_POWER_COLLAPSE=y
  (no CONFIG_ARM_PSCI_CPUIDLE)

Phase351 GKI final config is audited for:
  CONFIG_ARM_PSCI_CPUIDLE=y
  CONFIG_ARM_PSCI_CPUIDLE_DOMAIN=y
  CONFIG_PM_GENERIC_DOMAINS=y
  CONFIG_QCOM_RPMH=y

TouchGrass ARM idle path:
  cpuidle-arm -> arm_cpuidle_suspend -> cpu_ops->cpu_suspend / vendor PM stack

GKI 5.10 hierarchical path:
  cpuidle-psci -> cpu_pm_enter -> pm_runtime_put_sync_suspend(genpd)
  -> psci_cpu_suspend_enter -> pm_runtime_get_sync -> cpu_pm_exit

Phase351 brackets every important step of the latter without intentionally
changing state-selection, PSCI arguments, power-domain calls, or return flow.
EOF

python3 - <<'PY'
from pathlib import Path
import hashlib,json,os
r=Path('phase351-gki-out')
def sha(p):
 h=hashlib.sha256(); h.update(p.read_bytes()); return h.hexdigest()
ident={
 'phase':'351','name':'PSCI-IDLE-FRONTIER-V1','base_phase':'346',
 'hardware_validated':False,
 'sideband_phys':'0xB1BF4000','sideband_bytes':0x4000,
 'event_count':16,'cpu_count':8,'slot_bytes':64,
 'measured_power_behavior_changed':False,
 'boot_img_size':(r/'package/boot.img').stat().st_size,
 'boot_img_sha256':sha(r/'package/boot.img'),
 'image_sha256':sha(r/'compile/Image'),
 'git_sha':os.getenv('GITHUB_SHA')
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(ident,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)

stage "complete"
echo "Phase351 PSCI idle frontier build: PASS"
trap - EXIT
