#!/usr/bin/env bash
set -Eeuo pipefail

OUT="$PWD/artifacts/a52xq-display-init-recorder-fec"
BUILD="$PWD/workspace/gki-display-init-recorder-fec-out"
DISPLAY_PATCH="$PWD/patches/177-a52-display-init-recorder-plain.patch"
SINGLE_MAP="$PWD/scripts/178_apply_a52_display_init_recorder_fec.py"
mkdir -p "$OUT"/{logs,stage,config,compile,package,tools} "$BUILD"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GKI_COMMON_SHA:?GKI_COMMON_SHA is required}"
: "${SOURCE_ARTIFACT_ID:?SOURCE_ARTIFACT_ID is required}"
: "${SOURCE_ARTIFACT_SHA256:?SOURCE_ARTIFACT_SHA256 is required}"
test "${GKI_CACHE_HIT:-false}" = true

test -s "$SINGLE_MAP"

# Reconstruct the exact display-probe patch from the checksum-bound payload
# chunks used by recorder 177. The tracked patch path is only the output target.
CHUNK_DIR="$PWD/scripts/177_patch_payload_chunks"
mkdir -p "$(dirname "$DISPLAY_PATCH")"
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
  normal="$OUT/logs/display-$1.normalised"
  tr -d '\r\n' < "$CHUNK_DIR/$1" > "$normal"
  test "$(wc -c < "$normal")" = 1216
  printf '%s  %s\n' "$2" "$normal" | sha256sum -c -
done
cat "$OUT/logs/display-00.txt.normalised" "$OUT/logs/display-01.txt.normalised" \
    "$OUT/logs/display-02.txt.normalised" "$OUT/logs/display-03.txt.normalised" \
    "$OUT/logs/display-04.txt.normalised" "$OUT/logs/display-05.txt.normalised" \
    "$OUT/logs/display-06.txt.normalised" "$OUT/logs/display-07.txt.normalised" \
    > "$OUT/logs/display-trace-patch.gz.b64"
base64 --decode "$OUT/logs/display-trace-patch.gz.b64" \
  > "$OUT/logs/display-trace-patch.gz"
gzip -t "$OUT/logs/display-trace-patch.gz"
gzip -dc "$OUT/logs/display-trace-patch.gz" > "$DISPLAY_PATCH"
printf '%s  %s\n' \
  '9412b28da19c71e7bc97e767e83ce717e146313d32321c7429e6d4050a4f0d00' \
  "$DISPLAY_PATCH" | sha256sum -c -
test "$(wc -l < "$DISPLAY_PATCH")" = 796

sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc || true
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git curl ca-certificates unzip make gcc g++ python3 bc bison flex \
  clang lld llvm gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
  libssl-dev libelf-dev rsync cpio gzip lz4 file dwarves

fetch_fec_file() {
  local path="$1"
  mkdir -p "$(dirname "$path")"
  curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H 'Accept: application/vnd.github.raw+json' \
    "https://api.github.com/repos/${GITHUB_REPOSITORY}/contents/${path}?ref=agent%2Fa52-display-recorder-fec-v1" \
    --output "$path"
}

# Materialize the audited recorder-v3 patcher and matching RS decoder from the
# dedicated FEC branch. The packed payload provides a checksum-bound decoder;
# the committed patcher then restores the later PRZ mapping revision.
fetch_fec_file scripts/176_payload.py
for part in 00 01 02 03; do
  fetch_fec_file "scripts/176_payload_chunks/${part}.txt"
done
python3 scripts/176_payload.py --verify \
  2>&1 | tee "$OUT/logs/fec-payload-verification.log"
fetch_fec_file scripts/176_apply_a52_recorder_fec.py

python3 -m py_compile \
  scripts/176_apply_a52_recorder_fec.py \
  scripts/178_apply_a52_display_init_recorder_fec.py \
  tools/decode-a52-recorder-v3.py \
  scripts/38_repack_a52_p1_boot.py
python3 scripts/176_apply_a52_recorder_fec.py --self-test
python3 scripts/178_apply_a52_display_init_recorder_fec.py --self-test
python3 tools/decode-a52-recorder-v3.py --self-test

grep -Fq 'A52_RECORDER_V3_FEC' scripts/176_apply_a52_recorder_fec.py
grep -Fq 'persistent_ram_new(a52_diag_phys[bank]' scripts/176_apply_a52_recorder_fec.py
grep -Fq 'heap19-display-init-fec-single-map-v1' "$SINGLE_MAP"
grep -Fq 'A52_DIAG_TOTAL_SIZE' "$SINGLE_MAP"

mkdir -p source/extracted
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/${SOURCE_ARTIFACT_ID}/zip" \
  --output source/artifact.zip
printf '%s  %s\n' "$SOURCE_ARTIFACT_SHA256" source/artifact.zip | sha256sum -c -
unzip -q source/artifact.zip -d source/extracted
(cd source/extracted && sha256sum -c SHA256SUMS)
test -s source/extracted/stage/heap19-display-bindcore-source.patch
test -s source/extracted/config/final.config
test -s source/extracted/package/boot.img
test "$(tr -d '\r\n' < source/extracted/compile/make-return-code.txt)" = 0
grep -Fq '"persistent_profile": "heap19-bufops-display-bindcore-v1"' \
  source/extracted/final-audit.json

test -d gki/common/.git
test "$(git -C gki/common rev-parse HEAD)" = "$GKI_COMMON_SHA"
git -C gki/common reset --hard "$GKI_COMMON_SHA"
git -C gki/common clean -fd
BASE_PATCH="$PWD/source/extracted/stage/heap19-display-bindcore-source.patch"
git -C gki/common apply --check "$BASE_PATCH"
git -C gki/common apply "$BASE_PATCH"

git -C gki/common apply --check --verbose "$DISPLAY_PATCH" \
  2>&1 | tee "$OUT/logs/display-patch-check.log"
git -C gki/common apply "$DISPLAY_PATCH"

python3 scripts/176_apply_a52_recorder_fec.py \
  --gki gki/common --output "$OUT/stage" \
  2>&1 | tee "$OUT/logs/recorder-v3-stage.log"
python3 scripts/178_apply_a52_display_init_recorder_fec.py \
  --gki gki/common --output "$OUT/stage" \
  2>&1 | tee "$OUT/logs/single-map-stage.log"

REC=gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c
RAM=gki/common/fs/pstore/ram.c
MAIN=gki/common/init/main.c
DISPLAY=gki/common/drivers/a52_display/msm/dsi/dsi_display.c
PANEL=gki/common/drivers/a52_display/msm/dsi/dsi_panel.c
PWR=gki/common/drivers/a52_display/msm/dsi/dsi_pwr.c
CLK=gki/common/drivers/a52_display/msm/dsi/dsi_clk_manager.c
CTRL=gki/common/drivers/a52_display/msm/dsi/dsi_ctrl.c
PHY=gki/common/drivers/a52_display/msm/dsi/dsi_phy.c
DRM=gki/common/drivers/a52_display/msm/dsi/dsi_drm.c
SS=gki/common/drivers/a52_display/msm/samsung/ss_dsi_panel_common.c
SPEC=gki/common/drivers/a52_display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c

grep -Fq 'A52 black-screen failure-window recorder v3' "$REC"
grep -Fq 'A52_REC3_PROFILE "heap19-display-init-fec-single-map-v1"' "$REC"
grep -Fq 'A52_REC3_MESSAGE_LEN 128U' "$REC"
grep -Fq 'A52_RECORDER_V3_FEC' "$RAM"
grep -Fq 'A52_ACKFR_PARITY_BYTES 32U' "$RAM"
grep -Fq 'A52_DIAG_BANK_COUNT 3U' "$RAM"
grep -Fq 'A52_DIAG_TOTAL_SIZE' "$RAM"
grep -Fq 'a52_diag_map_all_banks' "$RAM"
grep -Fq 'persistent_ram_new(a52_diag_phys[0]' "$RAM"
grep -Fq 'A52 recorder v3 single-mapped %u banks' "$RAM"
! grep -Fq 'a52_diag_map_bank(unsigned int bank)' "$RAM"
grep -Fq 'a52_ackfr_record("BOOT phase=mm_init")' "$MAIN"

for marker in \
  'A52_DISPLAY_INIT_TRACE_PLAIN_V1' \
  'DISP MODE m=%u h=%u v=%u' \
  'DISP BIND start' \
  'DISP PREP start' \
  'DISP ENABLE start'; do grep -Fq "$marker" "$DISPLAY"; done
for marker in \
  'A52_PANEL_INIT_TRACE_PLAIN_V1' \
  'DISP RESET step=' \
  'DISP PIN state=' \
  'DISP POWER start' \
  'DISP CMDSET start' \
  'DISP BL p='; do grep -Fq "$marker" "$PANEL"; done
grep -Fq 'A52_PWR_TRACE_PLAIN_V1' "$PWR"
grep -Fq 'A52_CLK_TRACE_PLAIN_V1' "$CLK"
grep -Fq 'A52_DSI_CMD_TRACE_PLAIN_V1' "$CTRL"
grep -Fq 'A52_PHY_TRACE_PLAIN_V1' "$PHY"
grep -Fq 'A52_DRM_DISPLAY_TRACE_PLAIN_V1' "$DRM"
grep -Fq 'A52_SAMSUNG_TRACE_PLAIN_V1' "$SS"
grep -Fq 'A52_PANEL_SPECIFIC_TRACE_PLAIN_V1' "$SPEC"

git -C gki/common diff --check
git -C gki/common add -N .
git -C gki/common diff --binary --no-ext-diff \
  > "$OUT/stage/display-init-recorder-fec-source.patch"
test -s "$OUT/stage/display-init-recorder-fec-source.patch"
cp "$DISPLAY_PATCH" "$OUT/stage/"

cp source/extracted/config/final.config "$BUILD/.config"
CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C gki/common O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
grep -Fxq 'CONFIG_PSTORE_RAM=y' "$BUILD/.config"
grep -Fxq 'CONFIG_REED_SOLOMON=y' "$BUILD/.config"
grep -Fxq 'CONFIG_REED_SOLOMON_ENC8=y' "$BUILD/.config"
grep -Fxq 'CONFIG_REED_SOLOMON_DEC8=y' "$BUILD/.config"

set +e
make -k -C gki/common O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 -j4 \
  KCFLAGS=-Wno-error=frame-larger-than Image \
  > "$OUT/logs/compile.log" 2>&1
rc=$?
set -e
printf '%s\n' "$rc" > "$OUT/compile/make-return-code.txt"
if [ "$rc" -ne 0 ]; then
  grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/compile.log" | tail -n 300 || true
  tail -n 500 "$OUT/logs/compile.log" || true
  exit "$rc"
fi

test -s "$BUILD/arch/arm64/boot/Image"
cp "$BUILD/arch/arm64/boot/Image" "$OUT/compile/Image"
gzip -n -9 -c "$OUT/compile/Image" > "$OUT/package/Image.gz"
gzip -t "$OUT/package/Image.gz"
for object in \
  'CC      fs/pstore/ram.o' \
  'CC      drivers/a52_secure/a52_ack_secure_flight_recorder.o' \
  'CC      drivers/a52_display/msm/dsi/dsi_display.o' \
  'CC      drivers/a52_display/msm/dsi/dsi_panel.o' \
  'CC      drivers/a52_display/msm/dsi/dsi_ctrl.o'; do
  grep -Fq "$object" "$OUT/logs/compile.log"
done
for marker in \
  'heap19-display-init-fec-single-map-v1' \
  'BOOT recorder=v3 profile=%s copies=3 rs=32 crc32c=1 slots=1023' \
  'A52 recorder v3 single-mapped %u banks, slots=%u, RS parity=%u' \
  'DISP DSI i=%d t=%02x' \
  'DISP VREG+ done i=%d' \
  'DISP RESET step=%d' \
  'DISP MODE m=%u h=%u v=%u' \
  'DISP SS_CMD start'; do
  grep -aFq "$marker" "$OUT/compile/Image"
done
if grep -nE '(^|: )(fatal error|error): |undefined reference to' \
    "$OUT/logs/compile.log" > "$OUT/logs/compiler-errors.txt"; then
  cat "$OUT/logs/compiler-errors.txt"
  exit 1
fi
grep -Fq 'OBJCOPY arch/arm64/boot/Image' "$OUT/logs/compile.log"

python3 scripts/38_repack_a52_p1_boot.py \
  --source source/extracted/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
cp tools/decode-a52-recorder-v3.py "$OUT/tools/"

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-display-init-recorder-fec')
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = {
    'status': 'a52-display-init-recorder-fec-boot-audited',
    'flashable_candidate': True,
    'hardware_validated': False,
    'base_profile': 'heap19-bufops-display-bindcore-v1',
    'persistent_profile': 'heap19-display-init-fec-single-map-v1',
    'source_artifact_id': 8681171875,
    'display_probe_set': 'exact-recorder-177-probes',
    'display_control_flow_changed': False,
    'refgen_logic_changed': False,
    'secure_memory_logic_changed': False,
    'recorder': {
        'format': 'fixed-binary-v3',
        'record_bytes': 256,
        'protected_data_bytes': 208,
        'reed_solomon_parity_bytes_per_copy': 32,
        'unknown_symbol_correction_capacity_per_copy': 16,
        'crc': 'CRC32C',
        'commit_footer_bytes': 16,
        'copies': 3,
        'physical_bank_spacing_bytes': 262144,
        'mapping': 'one-contiguous-768KiB-vmap-split-into-three-banks',
        'stale_bank_policy': 'zap-and-zero-on-initialization',
    },
    'image_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
    'boot_sha256': hashlib.sha256(boot.read_bytes()).hexdigest(),
    'boot_bytes': boot.stat().st_size,
    'dtb_preserved': repack['invariants']['dtb_preserved'],
    'ramdisk_preserved': repack['invariants']['ramdisk_preserved'],
    'recovery_dtbo_preserved': repack['invariants']['recovery_dtbo_preserved'],
}
assert audit['dtb_preserved'] is True
assert audit['ramdisk_preserved'] is True
assert audit['recovery_dtbo_preserved'] is True
(root / 'final-audit.json').write_text(json.dumps(audit, indent=2, sort_keys=True) + '\n')
PY

cat > "$OUT/README-FIRST.txt" <<'EOF_README'
A52 protected display initialization recorder

This candidate keeps the exact display initialization probes from recorder 177
and replaces only the persistent recorder backend.

Every event is stored as a fixed 256-byte binary record with CRC32C, 32 bytes
of Reed-Solomon parity, a final commit footer, and three copies in physically
separated 256 KiB banks. The three banks share one contiguous persistent-RAM
mapping to avoid partial per-bank mapping failures.

After the failed boot, collect the untouched raw 1 MiB RAMOOPS ZIP and decode:

  python3 tools/decode-a52-recorder-v3.py RAW_OR_ARCHIVE \
    --output decoded-display-init-fec

This build is not hardware validated until flashed and captured.
EOF_README

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
