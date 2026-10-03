#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
OUT="$ROOT/phase432ctrl-tg-golden-out"
FAIL="$ROOT/phase432ctrl-tg-golden-failure"
STAGE=startup

stage(){ STAGE="$1"; echo "== Phase432-CTRL-TG stage: $STAGE =="; }
fail_report(){
  set +e
  rm -rf "$FAIL"; mkdir -p "$FAIL"/{logs,audit,source}
  printf '%s\n' "$STAGE" > "$FAIL/FAILED-STAGE.txt"
  cp phase432ctrl-tg-*.log "$FAIL/logs/" 2>/dev/null || true
  cp /tmp/p432cg-* "$FAIL/audit/" 2>/dev/null || true
  cp scripts/432ctrlg_apply_touchgrass_kmsg_tee.py scripts/432ctrlg_decode_touchgrass_kmsg.py "$FAIL/audit/" 2>/dev/null || true
  [ -f "$KERNEL/kernel/printk/printk.c" ] && cp "$KERNEL/kernel/printk/printk.c" "$FAIL/source/" || true
}
trap 'rc=$?; [ "$rc" -eq 0 ] || fail_report; exit "$rc"' EXIT

stage "reconstruct exact clean TouchGrass 4.19.200 Golden base"
./scripts/01_prepare_source.sh
./scripts/03_apply_linux_4.19.153.sh
./scripts/04_apply_linux_4.19.154.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.154 4.19.159
./scripts/checkpoint_resolve_linux_4.19.159.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.159 4.19.164
./scripts/checkpoint_resolve_linux_4.19.164.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.164 4.19.180
./scripts/checkpoint_resolve_linux_4.19.180.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.180 4.19.200
./scripts/checkpoint_resolve_linux_4.19.200.sh
./scripts/07_patch_resukisu_exec_hook.sh

PRINTK="$KERNEL/kernel/printk/printk.c"
test -s "$PRINTK"
cp "$PRINTK" /tmp/p432cg-printk-before.c

stage "apply P432C KMSG-only tee"
python3 -m py_compile scripts/432ctrlg_apply_touchgrass_kmsg_tee.py scripts/432ctrlg_decode_touchgrass_kmsg.py
python3 scripts/432ctrlg_apply_touchgrass_kmsg_tee.py --root "$KERNEL"
python3 scripts/432ctrlg_apply_touchgrass_kmsg_tee.py --root "$KERNEL" --check-only
git -C "$KERNEL" diff --check -- kernel/printk/printk.c
cp "$PRINTK" /tmp/p432cg-printk-after.c
diff -u /tmp/p432cg-printk-before.c /tmp/p432cg-printk-after.c > /tmp/p432cg-printk.diff || true

stage "strict KMSG-only scope audit"
python3 - <<'PY'
from pathlib import Path
b=Path('/tmp/p432cg-printk-before.c').read_text()
a=Path('/tmp/p432cg-printk-after.c').read_text()
required=(
    'A52_PHASE432_CTRL_TG_KMSG_ONLY_V1',
    'A52_P432CG_RAM_PHYS          0xB1400000ULL',
    'A52_P432CG_INIT_LIMIT        600U',
    'mirror = *from;',
    'a52_p432cg_capture(from);',
    'ktime_get_boot_ns()',
    'odsign','odrefresh','init_user0','restorecon','fsverity','apexd',
    'keystore','vold','keymaster','KeyMint','qseecom',
)
for t in required:
    if t not in a:
        raise SystemExit('P432C-TG missing token: '+t)

# The normal devkmsg behavior must remain byte-for-byte recognizable around the
# tee: same allocation, same real iterator copy, same printk_emit and return.
for t in (
    'buf = kmalloc(len+1, GFP_KERNEL);',
    'if (!copy_from_iter_full(buf, len, from))',
    'printk_emit(facility, level, NULL, 0, "%s", line);',
    'kfree(buf);',
):
    if a.count(t) != b.count(t):
        raise SystemExit('P432C-TG changed normal devkmsg contract: '+t)

# The diagnostic block itself must contain no active task/display/QSEECOM work.
block=a[a.index('A52_PHASE432_CTRL_TG_KMSG_ONLY_V1'):a.index('static ssize_t devkmsg_write',a.index('A52_PHASE432_CTRL_TG_KMSG_ONLY_V1'))]
for t in (
    'stack_trace_save_tsk','task_pt_regs','get_wchan','try_get_task_stack',
    'msleep(','usleep_range(','udelay(','schedule(','kthread_run(',
    'drm_','dsi_','qseecom_ioctl','printk(','pr_info(','pr_err(',
    'kmalloc(','kzalloc(','vzalloc(',
):
    if t in block:
        raise SystemExit('P432C-TG forbidden instrumentation in tee: '+t)
print('Phase432-CTRL-TG KMSG-only scope audit: PASS')
PY

stage "build TouchGrass Golden control"
set -o pipefail
bash -lc 'source scripts/common.sh; build_kernel "touchgrass-4.19.200-resukisu-v4.1.0-safe"'   2>&1 | tee phase432ctrl-tg-build.log

IMAGE="$ROOT/artifacts/Image-touchgrass-4.19.200-resukisu-v4.1.0-safe"
CONFIG="$ROOT/artifacts/config-touchgrass-4.19.200-resukisu-v4.1.0-safe"
test -s "$IMAGE" -a -s "$CONFIG"
grep -aFq 'A52_PHASE432_CTRL_TG_KMSG_ONLY_V1' "$IMAGE"
grep -aFq 'odsign' "$IMAGE"
grep -aFq 'init_user0' "$IMAGE"
grep -aFq 'KeyMint' "$IMAGE"
grep -aFq 'Linux version 4.19.200-touchGrassKernel+' "$IMAGE"

stage "assemble Golden KMSG-only evidence"
rm -rf "$OUT"; mkdir -p "$OUT"/{audit,source,package,tools}
cp "$IMAGE" "$OUT/Image"
cp "$CONFIG" "$OUT/config"
cp phase432ctrl-tg-*.log "$OUT/audit/" 2>/dev/null || true
cp /tmp/p432cg-* "$OUT/audit/" 2>/dev/null || true
cp "$PRINTK" "$OUT/source/printk.c"
cp scripts/432ctrlg_apply_touchgrass_kmsg_tee.py "$OUT/audit/"
cp scripts/432ctrlg_decode_touchgrass_kmsg.py "$OUT/tools/"

cat > "$OUT/BUILD-IDENTITY.txt" <<'EOF'
experiment=PHASE432-CTRL-TG-GOLDEN-KMSG-ONLY-V1
base=clean-TouchGrass-4.19.200-Golden-reconstruction
touchgrass_base=6bf351bdf18bdb228db79e66f14a7a9c0178e5d7
kernel_version=4.19.200-touchGrassKernel+
diagnostic_delta=kernel/printk/printk.c-only
hook=devkmsg_write-before-devkmsg-off-and-ratelimit
iterator_copy=non-consuming-struct-copy
allocation_in_tee=none
remote_task_inspection=none
display_drm_dsi_instrumentation=none
qseecom_instrumentation=none
persistent_transport=0xB1400000-0xB143FFFF-reserved-RAM
record_bytes=128
record_timestamp=ktime_get_boot_ns
init_general_limit=600
priority_after_limit=took,exited,killed,Wait_for,wait_for_prop,starting_service,processing_action,bootanim,SurfaceFlinger,odsign,odrefresh,init_user0,restorecon,fsverity,apexd,keystore,vold,keymaster,KeyMint,qseecom
recovery_input=existing-B1400000-B1AFFFFF-Golden-reserved-RAM-export
EOF
sha256sum "$OUT/Image" "$OUT/config" > "$OUT/SHA256SUMS"
stage complete
echo "Phase432-CTRL-TG Golden KMSG-only build: PASS"
