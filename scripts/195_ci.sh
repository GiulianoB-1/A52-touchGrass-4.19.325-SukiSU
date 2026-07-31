#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-mdss-core-gdsc-provider"
OUT="$PWD/artifacts/a52xq-drm-post-kms-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/194_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase195.config"
cp "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$OUT/stage/msm-drv-before-phase195.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$OUT/stage/sde-kms-before-phase195.c"

python3 scripts/195_apply.py --root "$ROOT" | tee "$OUT/logs/phase195-apply.log"

cp "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$OUT/stage/msm-drv-after-phase195.c"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$OUT/stage/sde-kms-after-phase195.c"
cp scripts/195_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase195.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
msm = (root / 'drivers/a52_display/msm/msm_drv.c').read_text()
kms = (root / 'drivers/a52_display/msm/sde/sde_kms.c').read_text()

msm_markers = (
    'DRMPOST helper enter',
    'DRMPOST helper exit err=%d null=%d crtc=%d enc=%d conn=%d plane=%d',
    'DRMPOST threads enter crtc=%d',
    'DRMPOST commit-thread enter i=%d crtc=%u',
    'DRMPOST commit-sched exit i=%d rc=%d',
    'DRMPOST event-thread enter i=%d crtc=%u',
    'DRMPOST event-sched exit i=%d rc=%d',
    'DRMPOST pp-thread enter',
    'DRMPOST thread-create exit rc=%d',
    'DRMPOST vblank exit rc=%d',
    'DRMPOST irq-install exit rc=%d',
    'DRMPOST dev-register exit rc=%d',
    'DRMPOST mode-reset exit',
    'DRMPOST splash-config exit rc=%d',
    'DRMPOST postinit exit rc=%d',
    'DRMPOST init success',
    'DRMPOST init fail rc=%d',
)
for marker in msm_markers:
    assert msm.count(marker) == 1, (marker, msm.count(marker))

kms_markers = (
    'KMSPOST splash rc=%d regions=%u displays=%u',
    'KMSPOST pm-get exit rc=%d',
    'KMSPOST blocks exit rc=%d crtc=%d enc=%d conn=%d plane=%d',
    'KMSPOST power-decision displays=%u regions=%u',
    'KMSPOST power keep reason=continuous-splash',
    'KMSPOST pm-put enter reason=no-splash',
    'KMSPOST affinity enter irq=%d',
    'KMSPOST hw-init success crtc=%d enc=%d conn=%d plane=%d',
    'KMSPOST hw-init exit rc=%d',
)
for marker in kms_markers:
    assert kms.count(marker) == 1, (marker, kms.count(marker))

# Observation-only safety invariants. The original calls and control decisions remain.
for marker in (
    'kms = _msm_drm_init_helper(priv, ddev, dev, pdev);',
    'ret = msm_drm_display_thread_create(param, priv, ddev, dev);',
    'ret = drm_vblank_init(ddev, priv->num_crtcs);',
    'ret = drm_irq_install(ddev, platform_get_irq(pdev, 0));',
    'ret = drm_dev_register(ddev, 0);',
    'drm_mode_config_reset(ddev);',
    'ret = kms->funcs->cont_splash_config(kms);',
    'drm_kms_helper_poll_init(ddev);',
):
    assert marker in msm, marker

for marker in (
    'rc = _sde_kms_get_splash_data(&sde_kms->splash_data);',
    'rc = pm_runtime_get_sync(sde_kms->dev->dev);',
    'rc = _sde_kms_hw_init_blocks(sde_kms, dev, priv);',
    'if (sde_kms->splash_data.num_splash_displays)',
    'pm_runtime_put_sync(sde_kms->dev->dev);',
    'irq_set_affinity_notifier(irq_num, &sde_kms->affinity_notify);',
):
    assert marker in kms, marker

# The phase-194 functional provider and earlier evidence remain present.
gdsc = (root / 'drivers/regulator/a52-legacy-gdsc-regulator.c').read_text()
for marker in (
    '"mdss_core_gdsc"',
    'A52GDSC disable profile=mdss',
    'REGULATOR_CHANGE_MODE',
):
    assert marker in gdsc, marker
assert 'RSCCCORE suppliers dev=%s rc=%d reason=%s' in \
    (root / 'drivers/base/dd.c').read_text()
PY

git -C "$ROOT" diff --binary --no-ext-diff > \
  "$OUT/stage/phase195-drm-post-kms-trace.patch"
test -s "$OUT/stage/phase195-drm-post-kms-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/phase195-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/phase195-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase195-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
  "$OUT/logs/phase195-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/a52_display/msm/msm_drv.o' \
  "$OUT/logs/phase195-compile.log"
grep -Fq 'drivers/a52_display/msm/sde/sde_kms.o' \
  "$OUT/logs/phase195-compile.log"
for marker in \
  'KMSPOST splash rc=%d regions=%u displays=%u' \
  'KMSPOST blocks exit rc=%d crtc=%d enc=%d conn=%d plane=%d' \
  'KMSPOST power-decision displays=%u regions=%u' \
  'KMSPOST pm-put enter reason=no-splash' \
  'KMSPOST hw-init success crtc=%d enc=%d conn=%d plane=%d' \
  'DRMPOST helper exit err=%d null=%d crtc=%d enc=%d conn=%d plane=%d' \
  'DRMPOST commit-thread enter i=%d crtc=%u' \
  'DRMPOST thread-create exit rc=%d' \
  'DRMPOST vblank exit rc=%d' \
  'DRMPOST irq-install exit rc=%d' \
  'DRMPOST dev-register exit rc=%d' \
  'DRMPOST splash-config exit rc=%d' \
  'DRMPOST init success' \
  'A52GDSC disable profile=mdss name=%s rc=%d before=0x%x after=0x%x' \
  'RSCC component-add exit rc=%d'; do
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
A52 GKI 5.10 phase 195 DRM post-KMS and continuous-splash trace candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 194 hardware result:
  - mdss_core_gdsc bound successfully
  - RSCC passed its supplier gate, probed and joined the DRM component master
  - msm_drm_bind, DSI bind, panel driver init and Samsung panel init executed
  - sde_kms_hw_init_blocks returned
  - the Samsung logo remained visible instead of turning black
  - the last recovered event was immediately after _msm_drm_init_helper

Phase 195 is observation only. It records:
  - continuous-splash region and display counts
  - KMS object counts
  - display commit/event and pp thread creation
  - vblank initialization
  - IRQ runtime-PM and installation
  - DRM device registration and mode reset
  - continuous-splash configuration
  - KMS post-init and poll initialization
  - exact success or failure boundary

It does not force continuous splash, keep the GDSC on, alter return codes,
change scheduling, bypass a dependency, modify DTB/DTBO, change panel commands,
display timing, clock rates, regulator voltages, ramdisk, or recovery DTBO.
Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-drm-post-kms-trace')
base = json.loads(Path('artifacts/a52xq-mdss-core-gdsc-provider/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
msm = (root / 'stage/msm-drv-after-phase195.c').read_text()
kms = (root / 'stage/sde-kms-after-phase195.c').read_text()
audit = dict(base)
audit.update({
    'status': 'a52-drm-post-kms-trace-audited',
    'phase': 195,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase194': False,
    'phase194_hardware_validated': True,
    'phase194_last_recorder_sequence': 755,
    'phase194_last_recorder_timestamp_ms': 1113.392,
    'phase194_last_scope': 'a52.life._msm_drm_init_helper',
    'phase194_visible_result': 'Samsung logo remained visible and frozen',
    'continuous_splash_forced': False,
    'gdsc_keep_on_forced': False,
    'return_codes_changed': False,
    'thread_scheduling_changed': False,
    'dtb_changed': False,
    'dtbo_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'clock_rates_changed': False,
    'regulator_voltage_changed': False,
    'post_kms_trace_present': 'DRMPOST init success' in msm,
    'splash_count_trace_present': 'KMSPOST splash rc=%d regions=%u displays=%u' in kms,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in (
    'phase194_hardware_validated', 'post_kms_trace_present',
    'splash_count_trace_present', 'dtb_preserved', 'ramdisk_preserved',
    'recovery_dtbo_preserved',
):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
