#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$PWD/gki/common"
OUT="$PWD/workspace/gki-phase199-out"
REC="$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c"
DSI="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl.c"
HW="$ROOT/drivers/a52_display/msm/dsi/dsi_ctrl_hw_cmn.c"
CLK="$ROOT/drivers/a52_display/msm/dsi/dsi_clk_manager.c"

fail_report() {
  set +e
  rm -rf phase292-failure
  mkdir -p phase292-failure/{source,logs,audit,config}
  cp phase292-compile.log phase292-failure/logs/ 2>/dev/null || true
  for f in "$REC" "$DSI" "$HW" "$CLK"; do
    [ -f "$f" ] && cp "$f" phase292-failure/source/ || true
  done
  cp scripts/292*.py scripts/292*.sh phase292-failure/audit/ 2>/dev/null || true
  cp /tmp/p292-*.c /tmp/p292-*.config /tmp/p292-*.diff phase292-failure/config/ 2>/dev/null || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

# Reconstruct the exact green Phase291 kernel first. Phase292 is diagnostic-only
# and must inherit the actual clock recovery, target selection, timeout policy,
# and Golden-FDR boot image byte-for-byte before adding read-only observation.
bash scripts/291_ci_build.sh
test -s phase291-out/package/boot.img
for f in "$REC" "$DSI" "$HW" "$CLK"; do test -s "$f"; done
for token in \
  'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1' \
  'A52_PHASE289_STICKY_FIFO_SNAPSHOT_V1' \
  'A52_PHASE289_TARGET_TIMEOUT_RETENTION_V1' \
  'A52_PHASE289_FIFO_CAUSAL_SLOTS_V1' \
  'A52_PHASE280_TIMEOUT_RETENTION_LATCH_V1'; do
  grep -R -Fq "$token" "$REC" "$DSI" "$HW" "$CLK"
done

cp "$OUT/.config" /tmp/p292-base.config
cp "$REC" /tmp/p292-rec-before.c
cp "$DSI" /tmp/p292-dsi-before.c
cp "$HW" /tmp/p292-hw-before.c
cp "$CLK" /tmp/p292-clk-before.c

python3 -m py_compile scripts/292_apply_full_dma_chain_sticky_recorder.py
python3 scripts/292_apply_full_dma_chain_sticky_recorder.py --self-test
python3 scripts/292_apply_full_dma_chain_sticky_recorder.py --root "$ROOT"
python3 scripts/292_apply_full_dma_chain_sticky_recorder.py --root "$ROOT" --check-only

# Exact source-scope audit: all four files are expected to change, config is not.
! cmp -s /tmp/p292-rec-before.c "$REC"
! cmp -s /tmp/p292-dsi-before.c "$DSI"
! cmp -s /tmp/p292-hw-before.c "$HW"
! cmp -s /tmp/p292-clk-before.c "$CLK"
cmp -s /tmp/p292-base.config "$OUT/.config"

for token in \
  'A52_PHASE292_FULL_DMA_STICKY_RECORDER_V1' \
  '#define A52_P292_SLOTS 20U' \
  '#define A52_P292_REPLAYS 3U' \
  'P292 H r=%u v=%lx' \
  'P292 S00 r=%u c=%x f=%x t=%x l=%x mf=%x' \
  'P292 S18 r=%u in=%x ae=%x to=%x pe=%x ct=%x'; do grep -Fq "$token" "$REC"; done
for token in \
  'A52_PHASE292_DSI_CHAIN_TAPS_V1' \
  'a52_p292_snapshot_record(0, 5,' \
  'a52_p292_snapshot_record(4, 5,' \
  'a52_p292_snapshot_record(5, 5,' \
  'a52_p292_snapshot_record(13, 5,' \
  'a52_p292_snapshot_record(14, 5,' \
  'a52_p292_snapshot_record(15, 4,' \
  'a52_p292_snapshot_record(16, 4,' \
  'a52_p292_snapshot_record(17, 5,' \
  'a52_p292_snapshot_record(18, 5,' \
  'a52_p292_flush_timeout_snapshot();'; do grep -Fq "$token" "$DSI"; done
for token in \
  'A52_PHASE292_HW_CHAIN_TAPS_V1' \
  'a52_p292_snapshot_record(6, 4,' \
  'a52_p292_snapshot_record(7, 5,' \
  'a52_p292_snapshot_record(8, 5,' \
  'a52_p292_snapshot_record(9, 5,'; do grep -Fq "$token" "$HW"; done
test "$(grep -Fc 'a52_p292_snapshot_record(10, 5,' "$HW")" -eq 2
test "$(grep -Fc 'a52_p292_snapshot_record(11, 5,' "$HW")" -eq 2
test "$(grep -Fc 'a52_p292_snapshot_record(12, 5,' "$HW")" -eq 2
for token in \
  'A52_PHASE292_CLOCK_CHAIN_TAPS_V1' \
  'a52_p292_snapshot_record(2, 6,' \
  'a52_p292_snapshot_record(3, 4,'; do grep -Fq "$token" "$CLK"; done

python3 - "$REC" "$DSI" "$HW" "$CLK" \
  /tmp/p292-rec-before.c /tmp/p292-dsi-before.c /tmp/p292-hw-before.c /tmp/p292-clk-before.c <<'PY'
from pathlib import Path
import sys
rec,dsi,hw,clk,rec0,dsi0,hw0,clk0=[Path(x).read_text() for x in sys.argv[1:]]

# Phase292 may read registers and store RAM snapshots, but cannot add/remove any
# write/trigger/wait/reset/clock programming primitive in the production path.
for label,before,after in [('dsi',dsi0,dsi),('hw',hw0,hw)]:
    for needle in [
        'DSI_W32(', 'writel(', 'writel_relaxed(',
        'wait_for_completion_timeout(', 'msleep(', 'usleep_range(', 'udelay(',
        'trigger_command_dma(', 'reset_cmd_fifo(', 'soft_reset(',
    ]:
        if before.count(needle) != after.count(needle):
            raise SystemExit(f'Phase292 changed {label} production primitive count for {needle}: {before.count(needle)} -> {after.count(needle)}')
for needle in [
    'clk_set_rate(', 'clk_prepare(', 'clk_unprepare(', 'clk_enable(',
    'clk_disable(', 'clk_prepare_enable(', 'clk_disable_unprepare(',
    'dsi_link_hs_clk_start(', 'dsi_link_hs_clk_stop(',
]:
    if clk0.count(needle) != clk.count(needle):
        raise SystemExit(f'Phase292 changed clock primitive count for {needle}: {clk0.count(needle)} -> {clk.count(needle)}')

# Preserve exact Phase291 behavioral guard and Phase289 target selection.
for token in [
    'A52_PHASE291_CONT_SPLASH_ZERO_RATE_RECOVERY_V1',
    'if (!a52_targets_valid || !a52_zero_handoff)',
    'atomic_set(&a52_p282_fifo_inflight, 1);',
    'P276 282A m=fifo f=%x',
]:
    if token not in clk+dsi:
        raise SystemExit('Phase292 inherited behavior marker missing: '+token)

# Every critical record is first-write-wins RAM; no P292 a52_ackfr_record call is
# allowed in DSI/HW/clock producer files. Persistent emission happens only in the
# recorder's terminal replay function.
for label,src in [('dsi',dsi),('hw',hw),('clk',clk)]:
    if 'a52_ackfr_record("P292' in src:
        raise SystemExit('Phase292 streaming record leaked into '+label+' producer path')
if 'if (!(a52_p292_valid & (1UL << stage)))' not in rec:
    raise SystemExit('Phase292 first-write-wins gate missing')
if 'atomic_cmpxchg(&a52_p292_flushed, 0, 1)' not in rec:
    raise SystemExit('Phase292 one-shot replay gate missing')

# Timeout ordering is deliberately before the inherited status branch / Samsung
# debug panic path: timeout register bank -> P289 replay -> P292 triple replay ->
# Phase280 freeze -> status branch.
t18=dsi.index('a52_p292_snapshot_record(18, 5,')
p289=dsi.index('a52_p289_flush_timeout_snapshot();', t18)
p292=dsi.index('a52_p292_flush_timeout_snapshot();', p289)
freeze=dsi.index('a52_ackfr_retain_timeout_snapshot();', p292)
status_branch=dsi.index('if (status & mask)', freeze)
if not (t18 < p289 < p292 < freeze < status_branch):
    raise SystemExit('Phase292 timeout/replay/freeze/status ordering invalid')

# Trigger evidence straddles the exact unchanged production write on both paths.
for start,end in [
    ('void dsi_ctrl_hw_cmn_kickoff_fifo_command(', '\nvoid dsi_ctrl_hw_cmn_reset_cmd_fifo('),
    ('void dsi_ctrl_hw_cmn_trigger_command_dma(', '\nvoid dsi_ctrl_hw_cmn_clear_rdbk_reg('),
]:
    a=hw.index(start); b=hw.index(end,a); f=hw[a:b]
    pre=f.index('a52_p292_snapshot_record(10, 5,')
    wr=f.index('DSI_W32(ctrl, DSI_CMD_MODE_DMA_SW_TRIGGER, 0x1);')
    post=f.index('a52_p292_snapshot_record(11, 5,')
    ctl=f.index('a52_p292_snapshot_record(12, 5,')
    if not (pre < wr < post < ctl):
        raise SystemExit('Phase292 pre/write/post trigger ordering invalid')

# The new raw-register instrumentation is read-only.
if dsi.count('DSI_R32(') <= dsi0.count('DSI_R32('):
    raise SystemExit('Phase292 expected DSI read-only register taps were not added')
if hw.count('DSI_R32(') <= hw0.count('DSI_R32('):
    raise SystemExit('Phase292 expected HW read-only register taps were not added')
print('Phase292 behavior-preservation, sticky, timeout-retention and trigger-order audits: PASS')
PY

make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 olddefconfig
if ! cmp -s /tmp/p292-base.config "$OUT/.config"; then
  diff -u /tmp/p292-base.config "$OUT/.config" > /tmp/p292-config.diff || true
  echo '::error::Phase292 changed kernel config'
  cat /tmp/p292-config.diff
  exit 1
fi

make -C "$ROOT" O="$OUT" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CLANG_TRIPLE=aarch64-linux-gnu- LLVM=1 LLVM_IAS=1 \
  -j"$(nproc)" Image 2>&1 | tee phase292-compile.log
IMAGE="$OUT/arch/arm64/boot/Image"
test -s "$IMAGE"

rm -rf phase292-out
mkdir -p phase292-out/{compile,config,package,audit,source}
cp "$IMAGE" phase292-out/compile/Image
cp "$OUT/.config" phase292-out/config/final.config
cp /tmp/p292-base.config phase292-out/audit/phase291-final.config
cp /tmp/p292-rec-before.c phase292-out/audit/recorder-before.c
cp /tmp/p292-dsi-before.c phase292-out/audit/dsi-ctrl-before.c
cp /tmp/p292-hw-before.c phase292-out/audit/dsi-ctrl-hw-cmn-before.c
cp /tmp/p292-clk-before.c phase292-out/audit/dsi-clk-manager-before.c
cp phase292-compile.log phase292-out/audit/
cp scripts/292_apply_full_dma_chain_sticky_recorder.py phase292-out/audit/
cp scripts/292_ci_build.sh phase292-out/audit/
cp "$REC" phase292-out/source/a52_ack_secure_flight_recorder.c
cp "$DSI" phase292-out/source/dsi_ctrl.c
cp "$HW" phase292-out/source/dsi_ctrl_hw_cmn.c
cp "$CLK" phase292-out/source/dsi_clk_manager.c

gzip -n -c "$IMAGE" > phase292-out/package/Image.gz
python3 scripts/38_repack_a52_p1_boot.py \
  --source phase291-out/package/boot.img \
  --kernel phase292-out/package/Image.gz \
  --output phase292-out/package/boot.img \
  --report phase292-out/package/repack-report.json

test -s phase292-out/package/boot.img
test "$(stat -c '%s' phase292-out/package/boot.img)" -gt 1000000
file phase292-out/package/boot.img | tee phase292-out/package/boot-file.txt
sha256sum phase292-out/package/boot.img | tee phase292-out/package/boot-sha256.txt

cat > phase292-out/PHASE292-RECORD-SCHEMA.txt <<'EOF'
Phase292 complete sticky DSI DMA-chain recorder
===============================================
Target: the exact Phase282 FIFO-routed diagnostic command already isolated by Phase289.
Persistence: 20 fixed first-write-wins RAM slots; no streaming P292 producer logs.
At the proven timeout boundary the fixed snapshot is replayed THREE times, then the inherited Phase280 retention latch freezes the recorder before the status branch / Samsung timeout panic path.

S00 TARGET   : controller, transfer flags, message type, tx length, message flags
S01 STATE    : power, host_initialized, controller, command-engine, video-engine states
S02 CLK0     : Phase291 byte/pixel/intf targets + actual rates at zero handoff
S03 CLK1     : return code + actual byte/pixel/intf rates after existing set-rate sequence
S04 ARM0     : dma_irq flag + raw INT_CTRL/STATUS/CLK_STATUS/LANE_STATUS before arm
S05 ARM1     : same immediately after DMA_DONE interrupt arm + completion reinit
S06 F0       : FIFO command size/flags/broadcast-master-LPM config
S07 F1       : TPG control, first two command DWORDs, TPG FIFO status
S08 F2       : STATUS/FIFO/LANE/CLK/TPG-FIFO after FIFO population
S09 F3       : DMA_CTRL/LENGTH/TRIG_CTRL/INT_CTRL/CLK_CTRL after DMA programming
S10 PRETRIG  : STATUS/FIFO/LANE/CLK/TPG-FIFO immediately before production SW_TRIGGER
S11 POSTTRIG : same immediately after production SW_TRIGGER
S12 POSTCTL  : CTRL/CLK_CTRL/INT_CTRL/TRIG_CTRL/DLN0_PHY_ERR after trigger
S13 ISR      : translated status, dma_irq-before, raw INT_CTRL, error vector low/high
S14 WAIT0    : dma_irq + raw INT/STATUS/LANE/CLK immediately before completion wait
S15 WAIT1    : wait result, dma_irq, translated interrupt status, raw INT_CTRL
S16 TIME0    : translated/raw interrupt state, dma_irq, DMA_DONE bit at timeout
S17 TIME1    : STATUS/FIFO/LANE/CLK/TPG-FIFO at timeout
S18 TIME2    : INT_CTRL/ACK_ERR/TIMEOUT/DLN0_PHY_ERR/CTRL at timeout
S19 END      : reserved

Interpretation:
- S02 present and S03 valid: Phase291 zero-handoff recovery really executed.
- S05 bit1 in raw INT_CTRL set: DMA_DONE interrupt was armed before launch.
- S10->S11 change shows exactly what the controller/lane/clock state did across SW_TRIGGER.
- S13 present with DMA_DONE: hardware generated the IRQ; inspect interrupt/completion handling.
- S15 timeout + S16 no DMA_DONE: launch never completed in hardware.
- S17/S18 distinguish lane/clock/TPG FIFO/ACK/timeout/PHY-error failure classes.

Phase292 is observation-only: it adds read-only register snapshots and fixed RAM stores. It does not add/remove DSI writes, triggers, waits, resets, recovery actions, clock programming, panel packets, or brightness behavior.
EOF

python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
r=Path('phase292-out')
identity={
    'phase':292,
    'name':'FULL-DMA-CHAIN-STICKY-RECORDER',
    'git_sha':os.environ.get('GITHUB_SHA'),
    'hardware_validated':False,
    'base':'exact green Phase291 continuous-splash zero-rate recovery',
    'behavior_change':False,
    'producer_model':'20 fixed first-write-wins RAM slots; no streaming P292 producer records',
    'retention_model':'three compact full-chain replays immediately before inherited Phase280 timeout freeze',
    'target':'exact inherited Phase282/Phase289 FIFO-routed command',
    'register_writes_added':False,
    'trigger_policy_changed':False,
    'wait_or_timeout_changed':False,
    'reset_or_recovery_changed':False,
    'clock_programming_changed':False,
    'brightness_changed_from_base':False,
    'golden_repack_source':'phase291-out/package/boot.img',
    'repacker':'scripts/38_repack_a52_p1_boot.py',
    'question':'What exact clock/state/IRQ/FIFO/lane/PHY boundary prevents the target DSI command from reaching DMA_DONE?'
}
(r/'BUILD-IDENTITY.json').write_text(json.dumps(identity,indent=2,sort_keys=True)+'\n')
files=[p.relative_to(r) for p in sorted(r.rglob('*')) if p.is_file() and p.name!='SHA256SUMS']
with (r/'SHA256SUMS').open('w') as f:
    for rel in files:
        f.write(hashlib.sha256((r/rel).read_bytes()).hexdigest()+'  ./'+str(rel)+'\n')
PY
(cd phase292-out && sha256sum -c SHA256SUMS)

python3 - <<'PY'
from pathlib import Path
img=Path('phase292-out/compile/Image').read_bytes()
for marker in [
    b'P292 H r=%u v=%lx',
    b'P292 S00 r=%u c=%x f=%x t=%x l=%x mf=%x',
    b'P292 S10 r=%u st=%x fs=%x ln=%x ck=%x tf=%x',
    b'P292 S11 r=%u st=%x fs=%x ln=%x ck=%x tf=%x',
    b'P292 S13 r=%u st=%x q=%x in=%x e0=%x e1=%x',
    b'P292 S18 r=%u in=%x ae=%x to=%x pe=%x ct=%x',
    b'P291 C0 c=%d b=%llx p=%llx i=%llx ab=%lx ap=%lx ai=%lx',
    b'P289 F4 c=%x sw=%x st=%x fs=%x in=%x',
    b'P276 282A m=fifo f=%x',
]:
    if marker not in img:
        raise SystemExit('Phase292 compiled marker missing: '+marker.decode())
print('Phase292 compiled sticky chain + inherited Phase291/289 marker audit: PASS')
PY

python3 scripts/292_apply_full_dma_chain_sticky_recorder.py --root "$ROOT" --check-only
trap - EXIT
echo 'Phase292 full DMA-chain sticky recorder build/Golden-FDR repack: PASS'
