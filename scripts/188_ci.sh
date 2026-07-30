#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-normal-display-defer"
OUT="$PWD/artifacts/a52xq-pinctrl-stage-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

# Reconstruct the complete phase-187 source first. This preserves the working
# PDC and AMOLED providers and normal deferred probing without supplier bypasses.
bash scripts/187_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase188.config"
cp "$ROOT/drivers/pinctrl/qcom/pinctrl-msm.c" "$OUT/stage/pinctrl-msm-before-phase188.c"

python3 scripts/188_apply.py --root "$ROOT" | tee "$OUT/logs/phase188-apply.log"

cp "$ROOT/drivers/pinctrl/qcom/pinctrl-msm.c" "$OUT/stage/pinctrl-msm-after-phase188.c"
cp scripts/188_apply.py "$OUT/stage/"
cp scripts/188_decode_near_header.py "$OUT/tools/decode-a52-r188-near-header.py"
chmod +x "$OUT/tools/decode-a52-r188-near-header.py"
git -C "$ROOT" diff --check

# Instrumentation only. Configuration must stay byte-for-byte identical to phase 187.
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase188.config" "$OUT/config/final.config"

for symbol in \
  CONFIG_SPMI=y \
  CONFIG_SPMI_MSM_PMIC_ARB=y \
  CONFIG_MFD_SPMI_PMIC=y \
  CONFIG_REGMAP_SPMI=y \
  CONFIG_REGULATOR_QPNP_AMOLED=y \
  CONFIG_QCOM_PDC=y \
  CONFIG_PINCTRL_LAGOON=y \
  CONFIG_DISP_CC_LAGOON=y; do
  grep -Fqx "$symbol" "$BUILD/.config"
done

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
msm = (root / 'drivers/pinctrl/qcom/pinctrl-msm.c').read_text()
dd = (root / 'drivers/base/dd.c').read_text()
audit = (root / 'drivers/a52_secure/a52_display_bind_audit.c').read_text()
markers = (
    'PINCTRL msm alloc ok=1',
    'PINCTRL msm state np=%u ng=%u tiles=%u',
    'PINCTRL msm map tile=%d res=%u',
    'PINCTRL msm map single res=%u',
    'PINCTRL msm pmreset enter',
    'PINCTRL msm irq exit rc=%d',
    'PINCTRL msm pctl-register enter',
    'PINCTRL msm pctl-register exit rc=%ld',
    'PINCTRL msm gpio-init enter',
    'PINCTRL msm gpio-init exit rc=%d',
    'PINCTRL gpio wake-parse enter',
    'PINCTRL gpio wake-domain ok=%u',
    'PINCTRL gpio wake-handle enter',
    'PINCTRL gpio parent-alloc enter',
    'PINCTRL gpio chip-add enter irq=%d',
    'PINCTRL gpio range-add enter',
    'PINCTRL gpio exit rc=0',
    'PINCTRL msm probe exit rc=0',
)
for marker in markers:
    assert marker in msm, marker
assert 'qcom,lagoon-pinctrl' in msm
assert 'return name && !strcmp(name, "1d84000.ufshc");' in dd
assert 'DISP RP bypass' not in dd
assert dd.count('a52_device_links_force_probe(dev, &kept, &dropped);') == 1
assert 'device_attach(' not in audit
assert 'retry_all(' not in audit
assert 'a52_device_links_force_probe(&pdev->dev' not in audit
PY

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase188-pinctrl-stage-trace.patch"
test -s "$OUT/stage/phase188-pinctrl-stage-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase188-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase188-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase188-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase188-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/pinctrl/qcom/pinctrl-msm.o' "$OUT/logs/phase188-compile.log"
for marker in \
  'qcom,lagoon-pdc' \
  'qcom,qpnp-amoled-regulator' \
  'PINCTRL msm alloc ok=1' \
  'PINCTRL msm pctl-register enter' \
  'PINCTRL msm gpio-init enter' \
  'PINCTRL gpio wake-domain ok=%u' \
  'PINCTRL gpio chip-add enter irq=%d' \
  'PINCTRL msm probe exit rc=0'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done
if grep -aFq 'DISP RP bypass' "$BUILD/arch/arm64/boot/Image"; then
  echo 'forbidden display supplier-bypass marker remains in Image'
  exit 1
fi

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"

python3 scripts/38_repack_a52_p1_boot.py \
  --source source/extracted/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"

python3 "$OUT/tools/decode-a52-r179-rs-recorder.py" --self-test
python3 "$OUT/tools/decode-a52-r180-soft-rs.py" --self-test
python3 "$OUT/tools/decode-a52-r188-near-header.py" --self-test

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 phase 188 Qualcomm pinctrl stage-trace candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Fresh phase-187 hardware evidence, recovered from the correct 0x40000 console
and ftrace banks with one-bit persistent-header recovery, shows:
  - Lagoon PDC completed with rc=0
  - QPNP AMOLED parent, DT parsing and OLEDB/AB/IBB registration completed
  - DSI controller and primary display deferred normally without link dropping
  - f100000.pinctrl entered lagoon_pinctrl_probe around 997 ms
  - no Lagoon pinctrl probe-exit record was produced

Therefore the previous dependency bypass is fixed. The current blocker is inside
the generic 5.10 msm_pinctrl_probe() path.

Phase 188 is instrumentation-only. It records checkpoints around:
  - Qualcomm pinctrl allocation and state setup
  - TLMM MMIO resource lookup and mapping
  - PM-reset registration
  - parent IRQ lookup
  - pinctrl core registration
  - GPIO/IRQ-chip initialization
  - wakeup-parent IRQ-domain resolution
  - Qualcomm direct-wakeup handling
  - IRQ-parent allocation
  - gpiochip and pin-range registration
  - final driver-data setup and probe completion

It also includes decode-a52-r188-near-header.py, which accepts a persistent RAM
signature with at most one damaged bit and records that recovery in summary.json.

No driver behavior, DTB, panel command, display timing, refresh mode, regulator
voltage, ramdisk or recovery DTBO is changed. Normal -EPROBE_DEFER behavior and
the phase-187 removal of display/TLMM supplier bypasses are preserved.

Because recent failed boots dirtied F2FS metadata, back up the exact current BOOT
partition, allow one boot attempt only, and collect untouched raw 1 MiB RAMOOPS
before restoring the exact BOOT backup.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-pinctrl-stage-trace')
base = json.loads(Path('artifacts/a52xq-normal-display-defer/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
source = (root / 'stage/pinctrl-msm-after-phase188.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-pinctrl-stage-trace-audited',
    'phase': 188,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase187': False,
    'instrumentation_change_from_phase187': True,
    'phase187_hardware_result': 'visible reset/panic after approximately five seconds',
    'phase187_last_fresh_record': 'PINCTRL Lagoon probe enter dev=f100000.pinctrl',
    'phase187_fresh_record_monotonic_ms': 997.153,
    'root_cause_scope': 'inside generic msm_pinctrl_probe; exact sub-stage not yet known',
    'pinctrl_stage_trace_added': all(x in source for x in (
        'PINCTRL msm alloc ok=1', 'PINCTRL msm pctl-register enter',
        'PINCTRL msm gpio-init enter', 'PINCTRL gpio wake-domain ok=%u',
        'PINCTRL gpio chip-add enter irq=%d', 'PINCTRL msm probe exit rc=0')),
    'near_header_decoder_added': (root / 'tools/decode-a52-r188-near-header.py').is_file(),
    'pdc_fix_preserved': 'qcom,lagoon-pdc' in image.read_bytes().decode('latin1'),
    'amoled_fix_preserved': 'qcom,qpnp-amoled-regulator' in image.read_bytes().decode('latin1'),
    'display_supplier_bypass_removed': 'DISP RP bypass' not in image.read_bytes().decode('latin1'),
    'normal_deferred_probe_preserved': 'DISP RP defer-normal' in image.read_bytes().decode('latin1'),
    'dtb_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'display_modes_changed': False,
    'regulator_voltage_changed': False,
    'storage_write_added': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in (
    'pinctrl_stage_trace_added', 'near_header_decoder_added',
    'pdc_fix_preserved', 'amoled_fix_preserved',
    'display_supplier_bypass_removed', 'normal_deferred_probe_preserved',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved'):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
