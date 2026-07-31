#!/usr/bin/env bash
set -Eeuo pipefail

BASE_OUT="$PWD/artifacts/a52xq-drm-post-kms-trace"
OUT="$PWD/artifacts/a52xq-kms-block-init-trace"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
ROOT="$PWD/gki/common"
mkdir -p "$OUT/logs"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

bash scripts/195_ci.sh
rm -rf "$OUT"
cp -a "$BASE_OUT" "$OUT"
rm -f "$OUT/SHA256SUMS"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools}

cp "$BUILD/.config" "$OUT/config/before-phase196.config"
cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" "$OUT/stage/sde-kms-before-phase196.c"

python3 scripts/196_apply.py --root "$ROOT" | tee "$OUT/logs/phase196-apply.log"

cp "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" "$OUT/stage/sde-kms-after-phase196.c"
cp scripts/196_apply.py "$OUT/stage/"
git -C "$ROOT" diff --check
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase196.config" "$OUT/config/final.config"

python3 - <<'PY'
from pathlib import Path
p = Path('gki/common/drivers/a52_display/msm/sde/sde_kms.c')
s = p.read_text()
markers = (
    'KMSBLK core-rev exit rev=0x%x',
    'KMSBLK catalog exit rc=%ld null=%d',
    'KMSBLK power-helper exit rc=%d genpd=%d',
    'KMSBLK mmu exit rc=%d base-null=%d',
    'KMSMMU new exit domain=%d rc=%ld',
    'KMSMMU aspace exit domain=%d rc=%ld',
    'KMSMMU splash-map exit domain=%d rc=%d',
    'KMSMMU early-map exit domain=%d rc=%d',
    'KMSBLK reg-dma exit rc=%d',
    'KMSBLK rm exit rc=%d',
    'KMSBLK intr exit rc=%ld null=%d',
    'KMSBLK splash-res exit rc=%d',
    'KMSBLK mdp exit rc=%ld null=%d',
    'KMSBLK vbif exit i=%d id=%u rc=%ld null=%d',
    'KMSBLK sid exit rc=%ld null=%d',
    'KMSBLK perf exit rc=%d',
    'KMSBLK drm-obj exit rc=%d crtc=%d enc=%d conn=%d plane=%d',
    'KMSOBJ irq-domain exit rc=%d',
    'KMSOBJ get-displays rc=%d',
    'KMSOBJ setup-displays rc=%d enc=%d conn=%d',
    'KMSOBJ plane exit i=%d rc=%ld',
    'KMSOBJ crtc exit i=%d rc=%ld',
)
for marker in markers:
    assert s.count(marker) == 1, (marker, s.count(marker))
for marker in (
    'sde_kms->catalog = sde_hw_catalog_init(dev, sde_kms->core_rev);',
    'rc = _sde_kms_hw_init_power_helper(dev, sde_kms);',
    'rc = _sde_kms_mmu_init(sde_kms);',
    'rc = sde_reg_dma_init(sde_kms->reg_dma, sde_kms->catalog,',
    'rc = sde_rm_init(rm, sde_kms->catalog, sde_kms->mmio,',
    'sde_kms->hw_intr = sde_hw_intr_init(sde_kms->mmio, sde_kms->catalog);',
    'ret = sde_rm_cont_splash_res_init(priv, &sde_kms->rm,',
    'sde_kms->hw_mdp = sde_rm_get_mdp(&sde_kms->rm);',
    'sde_kms->hw_sid = sde_hw_sid_init(sde_kms->sid,',
    'rc = sde_core_perf_init(&sde_kms->perf, dev, sde_kms->catalog,',
    'rc = _sde_kms_drm_obj_init(sde_kms);',
    'mmu = msm_smmu_new(sde_kms->dev->dev, i);',
    'aspace = msm_gem_smmu_address_space_create(sde_kms->dev,',
    'ret = _sde_kms_map_all_splash_regions(sde_kms);',
    'ret = mmu->funcs->set_attribute(mmu, DOMAIN_ATTR_EARLY_MAP,',
):
    assert marker in s, marker
assert 'KMSPOST blocks exit rc=%d crtc=%d enc=%d conn=%d plane=%d' in s
assert 'A52GDSC disable profile=mdss' in Path('gki/common/drivers/regulator/a52-legacy-gdsc-regulator.c').read_text()
PY

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase196-kms-block-init-trace.patch"
test -s "$OUT/stage/phase196-kms-block-init-trace.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 KCFLAGS=-Wno-error=frame-larger-than Image > "$OUT/logs/phase196-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' "$OUT/logs/phase196-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase196-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' "$OUT/logs/phase196-compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/a52_display/msm/sde/sde_kms.o' "$OUT/logs/phase196-compile.log"
while IFS= read -r marker; do
  [ -z "$marker" ] && continue
  grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"
done < scripts/196_EXPECTED_MARKERS.txt

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source source/extracted/package/boot.img --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
python3 "$OUT/tools/decode-a52-r179-rs-recorder.py" --self-test
python3 "$OUT/tools/decode-a52-r180-soft-rs.py" --self-test
python3 "$OUT/tools/decode-a52-r188-near-header.py" --self-test

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 GKI 5.10 phase 196 KMS hardware-block initialization trace candidate

FLASH ONLY:
  package/boot.img -> BOOT partition

Phase 195 hardware result:
  - continuous splash was found: one region and one display
  - runtime-PM get succeeded
  - DSI and Samsung panel initialization succeeded
  - _sde_kms_hw_init_blocks returned -ENODEV
  - no CRTC, encoder, connector or plane was created
  - the GDSC collapse was failure cleanup, not the root cause

Phase 196 is observation only. It records every major KMS hardware-block step,
per-SMMU-domain creation, display address-space creation, splash mapping,
early-map release, resource-manager setup, MDP/VBIF/SID/perf setup and DRM
object construction.

It does not bypass -ENODEV, force an IOMMU domain, alter splash behavior,
keep the GDSC on, change return values, modify DTB/DTBO, panel commands,
display timing, clocks, regulator voltages, ramdisk or recovery DTBO.
Compile-audited, not hardware validated.
EOF

python3 - <<'PY'
import hashlib, json
from pathlib import Path
root = Path('artifacts/a52xq-kms-block-init-trace')
base = json.loads(Path('artifacts/a52xq-drm-post-kms-trace/final-audit.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'; boot = root / 'package/boot.img'
s = (root / 'stage/sde-kms-after-phase196.c').read_text()
audit = dict(base)
audit.update({
    'status': 'a52-kms-block-init-trace-audited',
    'phase': 196,
    'hardware_validated': False,
    'flashable_candidate': True,
    'functional_change_from_phase195': False,
    'phase195_hardware_validated': True,
    'phase195_kms_blocks_errno': -19,
    'phase195_splash_regions': 1,
    'phase195_splash_displays': 1,
    'phase195_drm_object_counts': {'crtc': 0, 'encoder': 0, 'connector': 0, 'plane': 0},
    'smmu_trace_present': 'KMSMMU new exit domain=%d rc=%ld' in s,
    'block_trace_present': 'KMSBLK catalog exit rc=%ld null=%d' in s,
    'drm_object_trace_present': 'KMSOBJ irq-domain exit rc=%d' in s,
    'return_codes_changed': False,
    'iommu_bypass_added': False,
    'continuous_splash_forced': False,
    'gdsc_keep_on_forced': False,
    'dtb_changed': False,
    'dtbo_changed': False,
    'panel_commands_changed': False,
    'display_timing_changed': False,
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
})
for key in ('phase195_hardware_validated','smmu_trace_present','block_trace_present','drm_object_trace_present','dtb_preserved','ramdisk_preserved','recovery_dtbo_preserved'):
    assert audit[key] is True, key
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True)+'\n')
PY
(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
