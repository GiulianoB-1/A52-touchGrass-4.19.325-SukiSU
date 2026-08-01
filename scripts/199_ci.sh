#!/usr/bin/env bash
set -Eeuo pipefail

OUT="$PWD/artifacts/a52xq-post-kms-crc32c"
BUILD="$PWD/workspace/gki-phase199-out"
ROOT="$PWD/gki/common"
PHASE198="$PWD/workspace/phase198-artifact"
SOURCE="$PWD/workspace/source-artifact"
TOUCHGRASS="$PWD/workspace/touchgrass-a52xq"
mkdir -p "$PWD/workspace"
trap 'rc=$?; mkdir -p "$OUT/logs"; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

: "${GKI_COMMON_SHA:?}"
: "${GH_TOKEN:?}"
: "${SOURCE_ARTIFACT_ID:?}"
: "${SOURCE_ARTIFACT_SHA256:?}"
: "${PHASE198_ARTIFACT_ID:?}"
: "${PHASE198_ARTIFACT_SHA256:?}"

download_artifact() {
  local id="$1" expected="$2" zip="$3" destination="$4"
  rm -rf "$destination" "$zip"
  mkdir -p "$destination"
  curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/${id}/zip" \
    --output "$zip"
  printf '%s  %s\n' "$expected" "$zip" | sha256sum -c -
  unzip -q "$zip" -d "$destination"
  (cd "$destination" && sha256sum -c SHA256SUMS)
}

rm -rf "$OUT" "$BUILD"
mkdir -p "$OUT"/{config,logs,stage,compile,package,tools} "$BUILD"

download_artifact "$SOURCE_ARTIFACT_ID" "$SOURCE_ARTIFACT_SHA256" \
  "$PWD/workspace/source-artifact.zip" "$SOURCE" \
  > "$OUT/logs/source-artifact-verification.log"
download_artifact "$PHASE198_ARTIFACT_ID" "$PHASE198_ARTIFACT_SHA256" \
  "$PWD/workspace/phase198-artifact.zip" "$PHASE198" \
  > "$OUT/logs/phase198-artifact-verification.log"

for required in \
  "$SOURCE/stage/heap19-display-bindcore-source.patch" \
  "$SOURCE/config/final.config" \
  "$PHASE198/config/final.config" \
  "$PHASE198/package/boot.img" \
  "$PHASE198/final-audit.json" \
  "$PHASE198/stage/180_apply.py" \
  "$PHASE198/stage/198_apply_catalog_trace.py"; do
  test -s "$required"
done
test -d "$TOUCHGRASS/.git"

python3 - <<'PY'
import json
from pathlib import Path
p = Path('workspace/phase198-artifact/final-audit.json')
a = json.loads(p.read_text())
assert a['status'] == 'a52-catalog-init-trace-audited'
assert a['phase'] == 198
assert a['flashable_candidate'] is True
assert a['phase194_mdss_core_gdsc_fix_preserved'] is True
assert a['phase196_kms_trace_preserved'] is True
assert a['phase197_triple_rs_preserved'] is True
PY

test -d "$ROOT/.git"
test "$(git -C "$ROOT" rev-parse HEAD)" = "$GKI_COMMON_SHA"
git -C "$ROOT" reset --hard "$GKI_COMMON_SHA"
git -C "$ROOT" clean -fd

BASE_PATCH="$SOURCE/stage/heap19-display-bindcore-source.patch"
git -C "$ROOT" apply --check "$BASE_PATCH"
git -C "$ROOT" apply "$BASE_PATCH"

P177="$PWD/workspace/phase177.patch"
P177_TMP="$PWD/workspace/phase177-payload"
rm -rf "$P177_TMP"; mkdir -p "$P177_TMP"
for item in \
  '00.txt 2e2e8773465271521302902aeedd3c0779c3eba750e191881eb0649654402a39' \
  '01.txt 2cc8aa74d1080a38f3deabaa790ba6cec24accfe542d9747c3f4c6eda00699ba' \
  '02.txt 640c2ce3842ecb8120e14494bb23b5d2e4a1c68700c2a67bab2ba55a04e36731' \
  '03.txt 3799c05943d31375851b3e0680f61fcde892d96272edad98454b641589fd69e0' \
  '04.txt 9e18d44f8df3abe72ac2c67a42919f8acbf90590acdb424ba609d0086c7cfc47' \
  '05.txt 9f0a5026d1a78d096b73ff97f3c785a9fd807999a36decb7a89e7fa851477945' \
  '06.txt b62056e0d393a7969bfac6c60295eb3fb6e78228f6955196e9326cdb90ea1f82' \
  '07.txt da48f50560314b78ef184d7910d5b5a8026164cae8227997a3795e457c16180a'; do
  set -- $item
  tr -d '\r\n' < "scripts/177_patch_payload_chunks/$1" > "$P177_TMP/$1"
  printf '%s  %s\n' "$2" "$P177_TMP/$1" | sha256sum -c -
done
cat "$P177_TMP"/*.txt | base64 --decode | gzip -dc > "$P177"
printf '%s  %s\n' 9412b28da19c71e7bc97e767e83ce717e146313d32321c7429e6d4050a4f0d00 "$P177" | sha256sum -c -
git -C "$ROOT" apply --check "$P177"
git -C "$ROOT" apply "$P177"

P179="$PWD/workspace/phase179.patch"
tr -d '\r\n' < scripts/179_payloads/patch.gz.b64 | base64 --decode | gzip -dc > "$P179"
printf '%s  %s\n' 81bc17510b643274dba9652baa5edf52e9c2127af02a77eb1597637be0c3c59f "$P179" | sha256sum -c -
git -C "$ROOT" apply --check "$P179"
git -C "$ROOT" apply "$P179"

S="$PHASE198/stage"
python3 "$S/180_apply.py" --root "$ROOT" --audit-source "$S/180_a52_display_bind_audit.c"
for phase in 181 182 183 185; do python3 "$S/${phase}_apply.py" --root "$ROOT"; done
python3 "$S/186_apply.py" --root "$ROOT" --touchgrass "$TOUCHGRASS"
for phase in 187 188 189 190 191 192 193 194 195 196; do
  python3 "$S/${phase}_apply.py" --root "$ROOT"
done
python3 "$S/197_apply_triple_rs.py" --self-test
python3 "$S/197_apply_triple_rs.py" --root "$ROOT"
python3 "$S/198_apply_catalog_trace.py" --self-test
python3 "$S/198_apply_catalog_trace.py" --root "$ROOT"
git -C "$ROOT" diff --check

python3 - <<'PY'
from pathlib import Path
root = Path('gki/common')
stage = Path('workspace/phase198-artifact/stage')
pairs = {
 'drivers/a52_secure/a52_ack_secure_flight_recorder.c': 'recorder-after-phase197.c',
 'fs/pstore/ram.c': 'ram-after-phase197.c',
 'init/main.c': 'main-after-phase197.c',
 'drivers/a52_display/msm/msm_drv.c': 'msm-drv-after-phase195.c',
 'drivers/a52_display/msm/sde/sde_kms.c': 'sde-kms-after-phase196.c',
 'drivers/a52_display/msm/sde/sde_hw_catalog.c': 'sde-hw-catalog-after-phase198.c',
 'drivers/a52_display/msm/dsi/dsi_ctrl.c': 'dsi-ctrl-after-phase182.c',
 'drivers/a52_display/msm/dsi/dsi_display.c': 'dsi-display-after-phase191.c',
 'drivers/a52_display/msm/sde_rsc.c': 'sde-rsc-after-phase192.c',
 'drivers/a52_secure/a52_display_bind_audit.c': 'display-bind-audit-after-phase187.c',
 'drivers/regulator/qpnp-amoled-regulator.c': 'qpnp-amoled-regulator-after-phase186.c',
 'drivers/regulator/a52-legacy-gdsc-regulator.c': 'a52-legacy-gdsc-after-phase194.c',
 'drivers/base/core.c': 'core-after-phase193.c',
 'drivers/base/dd.c': 'dd-after-phase193.c',
 'drivers/pinctrl/qcom/pinctrl-lagoon.c': 'pinctrl-lagoon-after-phase190.c',
}
for source, reference in pairs.items():
    if (root/source).read_bytes() != (stage/reference).read_bytes():
        raise SystemExit(f'Phase 198 reconstruction mismatch: {source}')
print(f'Phase 198 exact-source comparisons: {len(pairs)} PASS')
PY

cp "$PHASE198/config/final.config" "$BUILD/.config"
cp "$BUILD/.config" "$OUT/config/before-phase199.config"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-before-phase199.c"
sha256sum \
  "$ROOT/fs/pstore/ram.c" "$ROOT/init/main.c" \
  "$ROOT/drivers/a52_display/msm/msm_drv.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_kms.c" \
  "$ROOT/drivers/a52_display/msm/sde/sde_hw_catalog.c" \
  "$ROOT/drivers/regulator/a52-legacy-gdsc-regulator.c" \
  > "$OUT/stage/phase198-invariants-before-phase199.sha256"

python3 scripts/199_apply_recorder_crc32c.py --self-test | tee "$OUT/logs/phase199-patcher-self-test.log"
python3 scripts/199_apply_recorder_crc32c.py --root "$ROOT" | tee "$OUT/logs/phase199-apply.log"
cp "$ROOT/drivers/a52_secure/a52_ack_secure_flight_recorder.c" "$OUT/stage/recorder-after-phase199.c"
cp scripts/199_apply_recorder_crc32c.py "$OUT/stage/"
cp "$PHASE198/tools/decode-a52-r179-rs-recorder.py" "$OUT/tools/"
cp tools/decode-a52-r199-crc32c-base.py tools/decode-a52-r199-crc32c-triple.py "$OUT/tools/"
git -C "$ROOT" diff --check
sha256sum -c "$OUT/stage/phase198-invariants-before-phase199.sha256"

python3 - <<'PY'
from pathlib import Path
root=Path('gki/common')
rec=(root/'drivers/a52_secure/a52_ack_secure_flight_recorder.c').read_text()
ram=(root/'fs/pstore/ram.c').read_text(); main=(root/'init/main.c').read_text()
msm=(root/'drivers/a52_display/msm/msm_drv.c').read_text()
kms=(root/'drivers/a52_display/msm/sde/sde_kms.c').read_text()
cat=(root/'drivers/a52_display/msm/sde/sde_hw_catalog.c').read_text()
required=(
 'A52 GKI 5.10 display takeover recorder, phase 199', '#define A52_R179_CAPACITY 896U',
 '#define A52_R179_MESSAGE_LEN 90U', '#define A52_R179_COMMIT 0x5a52c199U',
 '#define A52_R179_VERSION 2U', '#define A52_R179_PREFIX "R99"', '__le32 crc32c;',
 'a52_r199_crc32c', '0x82f63b78U', 'memcpy(data->magic, "A52R0199"',
 'offsetof(struct a52_r179_data, crc32c)', '!strncmp(message, "DRMPOST ", 8)',
 '!strncmp(message, "KMSPOST ", 8)', '!strncmp(message, "KMSBLK ", 7)',
 '!strncmp(message, "CAT ", 4)', '!strncmp(message, "A52GDSC ", 8)',
 'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c',
 'phase199 triple-copy RS+CRC32C recorder enabled')
for m in required: assert m in rec, m
assert 'copies=3 crc=0' not in rec
assert '#define A52_R179_BANK_RECORD BIT(2)' in rec and 'A52_R179_BANK_ALL' in rec
assert '#define A52_DIAG_RECORD_PHYS 0xB1B00000ULL' in ram
assert main.count('A52USR2 BOOT_EARLY stage=mm_init') == 3
for m in ('DRMPOST thread-create enter','DRMPOST vblank enter crtc=%d','DRMPOST irq-install enter irq=%d','DRMPOST dev-register enter','DRMPOST mode-reset enter','DRMPOST splash-config enter','DRMPOST postinit enter','DRMPOST poll-init enter','DRMPOST init success'): assert m in msm,m
for m in ('KMSBLK catalog enter rev=0x%x','KMSBLK catalog exit rc=%ld null=%d'): assert m in kms,m
for m in ('CAT enter rev=0x%x np-null=%d','CAT success ctl=%u sspp=%u mixer=%u intf=%u wb=%u'): assert m in cat,m
PY

git -C "$ROOT" diff --binary --no-ext-diff > "$OUT/stage/phase199-post-kms-crc32c.patch"
test -s "$OUT/stage/phase199-post-kms-crc32c.patch"

CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH" CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu-
make -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig 2>&1 | tee "$OUT/logs/olddefconfig.log"
cp "$BUILD/.config" "$OUT/config/final.config"
cmp "$OUT/config/before-phase199.config" "$OUT/config/final.config"
set +e
make -k -C "$ROOT" O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 KCFLAGS=-Wno-error=frame-larger-than Image > "$OUT/logs/phase199-compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' "$OUT/logs/phase199-compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/phase199-compile.log" || true
  exit "$rc"
fi
if grep -nE '(^|: )(fatal error|error): |undefined reference to' "$OUT/logs/phase199-compile.log" > "$OUT/logs/compiler-errors.txt"; then cat "$OUT/logs/compiler-errors.txt"; exit 1; fi

test -s "$BUILD/arch/arm64/boot/Image"
grep -Fq 'drivers/a52_secure/a52_ack_secure_flight_recorder.o' "$OUT/logs/phase199-compile.log"
for marker in 'A52R0199' 'phase199 triple-copy RS+CRC32C recorder enabled' 'BOOT rs=ready phase=199 roots=%u copies=3 crc=crc32c' 'DRMPOST thread-create enter' 'DRMPOST vblank enter crtc=%d' 'DRMPOST irq-install enter irq=%d' 'DRMPOST dev-register enter' 'DRMPOST splash-config enter' 'DRMPOST postinit enter' 'DRMPOST poll-init enter' 'KMSBLK catalog enter rev=0x%x' 'CAT success ctl=%u sspp=%u mixer=%u intf=%u wb=%u'; do grep -aFq "$marker" "$BUILD/arch/arm64/boot/Image"; done

cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"; gzip -t "$OUT/package/Image.gz"
python3 scripts/38_repack_a52_p1_boot.py --source "$PHASE198/package/boot.img" --kernel "$OUT/package/Image.gz" --output "$OUT/package/boot.img" --report "$OUT/package/repack-report.json"
python3 "$OUT/tools/decode-a52-r199-crc32c-base.py" --self-test | tee "$OUT/logs/phase199-base-decoder-self-test.log"
python3 "$OUT/tools/decode-a52-r199-crc32c-triple.py" --self-test | tee "$OUT/logs/phase199-triple-decoder-self-test.log"

cat > "$OUT/README-FIRST.txt" <<'README'
A52 GKI 5.10 Phase 199 post-KMS trace with triple-copy RS + CRC32C

FLASH ONLY: package/boot.img to BOOT.

Recorder guarantees:
- three independent physical RAMOOPS copies
- 32 Reed-Solomon parity symbols per copy
- CRC32C over record metadata and message
- R99 fixed 255-byte transport
- 896 initial records
- post-capacity retention for DRMPOST, KMSPOST, KMSBLK, CAT and A52GDSC

The display control flow is unchanged from Phase 198. Collect the untouched full
1 MiB RAMOOPS image. Decode using tools/decode-a52-r199-crc32c-triple.py.

Compile-audited, not hardware validated.
README

python3 - <<'PY'
import hashlib,json
from pathlib import Path
root=Path('artifacts/a52xq-post-kms-crc32c'); base=json.loads(Path('workspace/phase198-artifact/final-audit.json').read_text()); repack=json.loads((root/'package/repack-report.json').read_text()); image=root/'compile/Image'; boot=root/'package/boot.img'; rec=(root/'stage/recorder-after-phase199.c').read_text()
base.update({'status':'a52-post-kms-crc32c-audited','phase':199,'base_phase':198,'base_artifact_sha256':'99ff045d09811d43106612ef2682216a7d29dfaa7825e2360bef403e31a41eb6','hardware_validated':False,'flashable_candidate':True,'functional_change_from_phase198':'recorder-integrity-and-retention-only','display_control_flow_changed':False,'return_codes_changed':False,'catalog_trace_preserved':True,'post_kms_trace_preserved':True,'phase194_mdss_core_gdsc_fix_preserved':True,'recorder_format':'R99-base64-RS-CRC32C','recorder_copy_count':3,'recorder_banks':['record','console','ftrace'],'recorder_data_bytes':157,'recorder_message_bytes':89,'recorder_parity_symbols_per_copy':32,'recorder_crc':'CRC32C','recorder_crc_polynomial_reflected':'0x82f63b78','recorder_initial_capacity':896,'post_capacity_retention':['BOOT','HB','REFGEN','DISP','WDT','DRMPOST','KMSPOST','KMSBLK','CAT','A52GDSC'],'decoder_cross_copy_fusion':['bit-majority','clear-bit-OR'],'iommu_bypass_added':False,'continuous_splash_forced':False,'gdsc_keep_on_forced':False,'dtb_changed':False,'dtbo_changed':False,'panel_commands_changed':False,'display_timing_changed':False,'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'boot_sha256':hashlib.sha256(boot.read_bytes()).hexdigest(),'boot_bytes':boot.stat().st_size,'dtb_preserved':repack['invariants']['dtb_preserved'],'ramdisk_preserved':repack['invariants']['ramdisk_preserved'],'recovery_dtbo_preserved':repack['invariants']['recovery_dtbo_preserved']})
for k in ('catalog_trace_preserved','post_kms_trace_preserved','phase194_mdss_core_gdsc_fix_preserved','dtb_preserved','ramdisk_preserved','recovery_dtbo_preserved'): assert base[k] is True,k
assert base['recorder_copy_count']==3 and base['recorder_parity_symbols_per_copy']==32 and base['recorder_crc']=='CRC32C'
assert 'copies=3 crc=0' not in rec
(root/'final-audit.json').write_text(json.dumps(base,indent=2,sort_keys=True)+'\n')
PY

(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS && sha256sum -c SHA256SUMS)
