#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(pwd)"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
OUT="$ROOT/artifacts/p1-full-hardware-diag"

chmod +x scripts/*.sh scripts/*.py

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

test "$(make -s -C "$KERNEL" kernelversion)" = 4.19.200

cp scripts/checkpoint_merge_linux_4.19.210.sh scripts/checkpoint_merge_linux_4.19.205.generated.sh
sed -i \
  -e 's/TO_TAG=v4.19.210/TO_TAG=v4.19.205/' \
  -e '/^TARGET_COMMIT=/d' \
  -e '/test "$to_sha" = "$TARGET_COMMIT"/d' \
  scripts/checkpoint_merge_linux_4.19.205.generated.sh
chmod +x scripts/checkpoint_merge_linux_4.19.205.generated.sh
./scripts/checkpoint_merge_linux_4.19.205.generated.sh

test "$(make -s -C "$KERNEL" kernelversion)" = 4.19.205

cp scripts/checkpoint_fix_linux_4.19.210_compile.sh scripts/checkpoint_fix_linux_4.19.205.generated.sh
sed -i 's/TARGET_VERSION=4.19.210/TARGET_VERSION=4.19.205/' \
  scripts/checkpoint_fix_linux_4.19.205.generated.sh
chmod +x scripts/checkpoint_fix_linux_4.19.205.generated.sh
./scripts/checkpoint_fix_linux_4.19.205.generated.sh

python3 - <<'PY'
from pathlib import Path

root = Path('workspace/touchgrass-a52xq')

path = root / 'drivers/soc/qcom/event_timer.c'
text = path.read_text()
newer = '''static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {
\t.rb_root = RB_ROOT_CACHED,
};
'''
older = '''static DEFINE_PER_CPU(struct timerqueue_head, timer_head) = {
\t.head = RB_ROOT,
\t.next = NULL,
};
'''
if newer not in text:
    raise SystemExit('event_timer newer initializer not found after generic repair')
path.write_text(text.replace(newer, older, 1))

path = root / 'kernel/sched/cpufreq_schedutil.c'
text = path.read_text()
call = 'sugov_clear_global_tunables();'
definition = 'static void sugov_clear_global_tunables(void)'
anchor = 'static void sugov_exit('
if call in text and definition not in text:
    if anchor not in text:
        raise SystemExit('schedutil exit anchor missing')
    helper = '''static void sugov_clear_global_tunables(void)
{
\tif (!have_governor_per_policy())
\t\tglobal_tunables = NULL;
}

'''
    path.write_text(text.replace(anchor, helper + anchor, 1))
elif call not in text:
    raise SystemExit('schedutil cleanup call missing after generic repair')
PY

python3 - <<'PY'
from pathlib import Path

src = Path('scripts/40_build_a52_p1_full_hardware_diag.sh')
dst = Path('scripts/build-4.19.205-touchscreen-no-root.generated.sh')
text = src.read_text()

replacements = [
    ('TARGET_VERSION=4.19.325', 'TARGET_VERSION=4.19.205'),
    ('a52xq-p1-full-hardware-diag-', 'a52xq-linux-4.19.205-touchscreen-no-root-'),
    ('  -d TOUCHSCREEN_STM_FTS5CU56A \\\n',
     '  -d KSU -d KSU_SUSFS -e TOUCHSCREEN_STM_FTS5CU56A \\\n'),
    ("grep -Fxq '# CONFIG_TOUCHSCREEN_STM_FTS5CU56A is not set' \"$FINAL_CONFIG\" \\\n  || fail \"FTS touchscreen driver remained enabled\"",
     "grep -Fxq 'CONFIG_TOUCHSCREEN_STM_FTS5CU56A=y' \"$FINAL_CONFIG\" \\\n  || fail \"FTS touchscreen driver was not enabled\""),
    ('echo "disabled_symbol=CONFIG_TOUCHSCREEN_STM_FTS5CU56A"',
     'echo "enabled_symbol=CONFIG_TOUCHSCREEN_STM_FTS5CU56A"'),
    ('touch_policy=disable-only-stm-fts', 'touch_policy=enable-stm-fts'),
    ('Only the active STMicroelectronics FTS implementation is\n# disabled.',
     'The active STMicroelectronics FTS implementation is compiled in.'),
    ('active FTS implementation is disabled.',
     'active FTS implementation is compiled in.'),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f'Expected build-template text not found: {old!r}')
    text = text.replace(old, new, 1)

dst.write_text(text)
dst.chmod(0o755)
PY

./scripts/build-4.19.205-touchscreen-no-root.generated.sh

test -s "$OUT/Image.gz"
grep -Fxq 'CONFIG_TOUCHSCREEN_STM_FTS5CU56A=y' "$OUT/final.config"
grep -Fxq 'CONFIG_INPUT_TOUCHSCREEN=y' "$OUT/final.config"
! grep -Eq '^CONFIG_KSU(=|_)' "$OUT/final.config"
grep -Fxq 'CONFIG_DRM=y' "$OUT/final.config"
grep -Fxq 'CONFIG_QCOM_KGSL=y' "$OUT/final.config"
grep -Fxq 'CONFIG_SCSI_UFS_QCOM=y' "$OUT/final.config"
grep -Fxq 'CONFIG_PSTORE_RAM=y' "$OUT/final.config"

cat > "$OUT/release-state.txt" <<'EOF'
kernel_version=4.19.205
root_integration=none
touchscreen_driver=CONFIG_TOUCHSCREEN_STM_FTS5CU56A=y
hardware_stack=full-a52-lagoon
boot_source=checksum-locked-original
hardware_validation=not-yet-performed
EOF
