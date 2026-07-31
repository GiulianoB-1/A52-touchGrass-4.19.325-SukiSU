#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-display-component-trace"
OUT="$PWD/artifacts/a52xq-sde-rscc-probe-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/191_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase192.config"
cp "$ROOT/drivers/a52_display/msm/sde_rsc.c" "$OUT/stage/sde-rsc-before-phase192.c"

python3 scripts/192_apply.py --root "$ROOT" | tee "$OUT/logs/phase192-apply.log"

cp "$ROOT/drivers/a52_display/msm/sde_rsc.c" "$OUT/stage/sde-rsc-after-phase192.c"
cp scripts/192_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check

cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase192.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
rsc = (root / 'drivers/a52_display/msm/sde_rsc.c').read_text()
msm = (root / 'drivers/a52_display/msm/msm_drv.c').read_text()
comp = (root / 'drivers/base/component.c').read_text()
lagoon = (root / 'drivers/pinctrl/qcom/pinctrl-lagoon.c').read_text()
for marker in (
    'RSCC main-register enter',
    'RSCC rpmh-register enter',
    'RSCC state=%s compat=%s node=%u pdev=%u bound=%s',
    'RSCC rpmh-probe enter',
    'RSCC probe enter dev=%s node=%s counter=%d rpmh=%u',
    'RSCC probe stage=power-init rc=%d',
    'RSCC probe stage=rpmh-link rc=%d present=%u',
    'RSCC probe stage=map-wrapper rc=%d',
    'RSCC probe stage=map-drv rc=%d',
    'RSCC probe stage=get-vdd rc=%d',
    'RSCC probe stage=hw-register rc=%d',
    'RSCC probe stage=vdd-enable rc=%d',
    'RSCC probe stage=resource-enable rc=%d',
    'RSCC probe stage=timer-calc rc=%d',
    'RSCC component-add exit rc=%d',
    'RSCC probe fail stage=%s stage_rc=%d return_rc=%d',
    'RSCC bind enter dev=%s master=%s',
):
    assert marker in rsc, marker
assert rsc.count('ret = component_add(&pdev->dev, &sde_rsc_comp_ops);') == 1
assert rsc.count('ret = platform_driver_register(&sde_rsc_platform_driver);') == 1
assert rsc.count('ret = platform_driver_register(&sde_rsc_rpmh_driver);') == 1
assert 'DRMCOMP connector i=%u' in msm
assert 'COMP slot i=%zu' in comp
reserved = lagoon.split('static const int lagoon_reserved_gpios[] = {', 1)[1].split('};', 1)[0]
assert '13, 14, 15, 16,' in reserved
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase192-sde-rscc-probe-trace.patch"
test -s "$OUT/stage/phase192-sde-rscc-probe-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-

set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase192-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase192-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase192-compile.log" || true
  exit "$rc"
fi

if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase192-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/a52_display/msm/sde_rsc.o' "$OUT/logs/phase192-compile.log"

for marker in \
  'RSCC main-register enter' \
  'RSCC rpmh-register enter' \
  'RSCC state=%s compat=%s node=%u pdev=%u bound=%s' \
  'RSCC probe stage=rpmh-link rc=%d present=%u' \
  'RSCC probe stage=timer-calc rc=%d' \
  'RSCC component-add exit rc=%d' \
  'RSCC bind exit rc=0' \
  'DRMCOMP connectors prop=%u len=%d' \
  'COMP slot i=%zu found=%u dev=%s bound=%u dup=%u' \
  'PINCTRL Lagoon reserved secure=13-16'; do
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done

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
A52 GKI 5.10 phase 192 SDE RSCC registration/probe trace candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 191 hardware result:
  - the active DRM master requires writeback, primary DSI, and SDE RSCC
  - writeback was already present
  - primary DSI later registered successfully
  - SDE RSCC remained the only missing component
  - DRM master bind and KMS initialization therefore never ran
  - BOOT_READY was reached and the kernel stayed alive through 62.496 seconds

Phase 192 is instrumentation-only. It records:
  - SDE RSCC and RSCC-RPMh driver registration
  - DT node and platform-device presence
  - RPMh child probe and cell index
  - every main RSCC probe stage and exact failure code
  - cleanup, component_add(), and component bind

It does not force probe, change an errno, remove RSCC from the component list,
change display commands, timing, backlight, clocks, regulators, DTB, ramdisk,
or recovery DTBO. Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-sde-rscc-probe-trace')
base = json.loads(Path('artifacts/a52xq-display-component-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
rsc = (root / 'stage/sde-rsc-after-phase192.c').read_text()
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = dict(base)
audit.update({
    'status': 'a52-sde-rscc-probe-trace-audited',
    'phase': 192,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase191': False,
    'phase191_hardware_validated': True,
    'phase191_kernel_alive_ms': 62496.028,
    'phase191_missing_component': 'qcom,sde_rscc / qcom,sde-rsc',
    'rscc_registration_trace_added': 'RSCC main-register enter' in rsc,
    'rscc_rpmh_trace_added': 'RSCC rpmh-probe enter' in rsc,
    'rscc_probe_stage_trace_added': 'RSCC probe stage=timer-calc rc=%d' in rsc,
    'rscc_component_trace_added': 'RSCC component-add exit rc=%d' in rsc,
    'rscc_bind_trace_added': 'RSCC bind exit rc=0' in rsc,
    'component_control_flow_changed': False,
    'probe_return_values_changed': False,
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
    'phase191_hardware_validated',
    'rscc_registration_trace_added',
    'rscc_rpmh_trace_added',
    'rscc_probe_stage_trace_added',
    'rscc_component_trace_added',
    'rscc_bind_trace_added',
    'dtb_preserved', 'ramdisk_preserved', 'recovery_dtbo_preserved',
):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(
    json.dumps(audit, indent=2, sort_keys=True) + '\n'
)
PY

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
