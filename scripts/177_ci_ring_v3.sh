#!/usr/bin/env bash
set -Eeuo pipefail

OUT="$PWD/artifacts/a52xq-display-recorder-fec-prz-ring-v3"
BUILD="$PWD/workspace/gki-display-recorder-fec-prz-ring-v3-out"
mkdir -p "$OUT"/{logs,stage,config,compile,package,tools} "$BUILD"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GKI_COMMON_SHA:?GKI_COMMON_SHA is required}"
: "${SOURCE_ARTIFACT_ID:?SOURCE_ARTIFACT_ID is required}"
: "${SOURCE_ARTIFACT_SHA256:?SOURCE_ARTIFACT_SHA256 is required}"
test "${GKI_CACHE_HIT:-false}" = true

sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc || true
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git curl ca-certificates unzip make gcc g++ python3 bc bison flex \
  clang lld llvm gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
  libssl-dev libelf-dev rsync cpio gzip lz4 file dwarves

python3 scripts/176_payload.py --verify
# The verified legacy payload reconstructs the common build tools. Replace only
# the recorder patcher and matching decoder with the audited ring-v3 payload.
python3 scripts/177_materialize_ring_v3.py --root "$PWD"
python3 -m py_compile \
  scripts/176_apply_a52_recorder_fec.py \
  tools/decode-a52-recorder-v3.py \
  scripts/38_repack_a52_p1_boot.py
python3 scripts/176_apply_a52_recorder_fec.py --self-test
python3 tools/decode-a52-recorder-v3.py --self-test

mkdir -p source/extracted
curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/${SOURCE_ARTIFACT_ID}/zip" \
  --output source/artifact.zip
printf '%s  %s\n' "${SOURCE_ARTIFACT_SHA256}" source/artifact.zip | sha256sum -c -
unzip -q source/artifact.zip -d source/extracted
(cd source/extracted && sha256sum -c SHA256SUMS)
test -s source/extracted/stage/heap19-display-bindcore-source.patch
test -s source/extracted/config/final.config
test -s source/extracted/package/boot.img
test "$(tr -d '\r\n' < source/extracted/compile/make-return-code.txt)" = 0
grep -Fq '"persistent_profile": "heap19-bufops-display-bindcore-v1"' \
  source/extracted/final-audit.json

test -d gki/common/.git
test "$(git -C gki/common rev-parse HEAD)" = "${GKI_COMMON_SHA}"
git -C gki/common reset --hard "${GKI_COMMON_SHA}"
git -C gki/common clean -fd
SOURCE_PATCH="$PWD/source/extracted/stage/heap19-display-bindcore-source.patch"
git -C gki/common apply --check "$SOURCE_PATCH"
git -C gki/common apply "$SOURCE_PATCH"

python3 scripts/176_apply_a52_recorder_fec.py \
  --gki gki/common --output "$OUT/stage" \
  2>&1 | tee "$OUT/logs/recorder-fec-stage.log"

REPORT="$OUT/stage/phase34-a52-recorder-fec-report.json"
grep -Fq '"status": "a52-recorder-v3-fec-staged"' "$REPORT"
grep -Fq '"persistent_profile": "display-bindcore-fec-prz-ring-v3"' "$REPORT"
grep -Fq '"mapping_backend": "persistent_ram_new-plus-persistent_ram_write-ring"' "$REPORT"
grep -Fq '"reed_solomon_parity_bytes": 32' "$REPORT"
grep -Fq '"copies": 3' "$REPORT"
grep -Fq '"unknown_symbol_correction_capacity_per_copy": 16' "$REPORT"

REC=gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c
RAM=gki/common/fs/pstore/ram.c
MAIN=gki/common/init/main.c
grep -Fq 'A52 black-screen failure-window recorder v3' "$REC"
grep -Fq 'A52_REC3_PROFILE "display-bindcore-fec-prz-ring-v3"' "$REC"
grep -Fq 'A52_REC3_MESSAGE_LEN 128U' "$REC"
grep -Fq 'A52_RECORDER_V3_FEC' "$RAM"
grep -Fq 'A52_ACKFR_PARITY_BYTES 32U' "$RAM"
grep -Fq 'A52_DIAG_BANK_COUNT 3U' "$RAM"
grep -Fq 'persistent_ram_new(a52_diag_phys[bank]' "$RAM"
grep -Fq 'persistent_ram_write(a52_diag_prz[bank], codeword' "$RAM"
grep -Fq 'persistent_ram_write(a52_diag_prz[bank], &footer' "$RAM"
! grep -Fq 'a52_diag_prz[bank]->vaddr' "$RAM"
! grep -Fq 'memcpy_toio(destination' "$RAM"
! grep -Fq 'ioremap_wc(a52_diag_phys[bank]' "$RAM"
grep -Fq 'a52_ackfr_record("BOOT phase=mm_init")' "$MAIN"
! grep -Fq 'A52USR2 BOOT_EARLY' "$MAIN"

git -C gki/common diff --check
git -C gki/common add -N .
git -C gki/common diff --binary --no-ext-diff \
  > "$OUT/stage/display-recorder-fec-prz-ring-v3-source.patch"
test -s "$OUT/stage/display-recorder-fec-prz-ring-v3-source.patch"

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
grep -Fq 'CC      fs/pstore/ram.o' "$OUT/logs/compile.log"
grep -Fq 'CC      drivers/a52_secure/a52_ack_secure_flight_recorder.o' \
  "$OUT/logs/compile.log"
for marker in \
  'display-bindcore-fec-prz-ring-v3' \
  'BOOT recorder=v3 profile=%s copies=3 rs=32 crc32c=1 ring=1' \
  'A52 recorder v3 ring mapped %u banks, RS parity=%u' \
  'DISP bind audit=start' \
  'DISP bind reg=msm_drm rc=%d' \
  'REFGEN probe ready initial_enabled=%d'; do
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

root = Path('artifacts/a52xq-display-recorder-fec-prz-ring-v3')
stage = json.loads((root / 'stage/phase34-a52-recorder-fec-report.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = {
    'status': 'a52-display-recorder-fec-prz-ring-v3-boot-audited',
    'flashable_candidate': True,
    'hardware_validated': False,
    'persistent_profile': 'display-bindcore-fec-prz-ring-v3',
    'mapping_backend': stage['mapping_backend'],
    'source_artifact_id': 8681171875,
    'source_binding_candidate_preserved': True,
    'display_control_flow_changed': False,
    'refgen_logic_changed': False,
    'secure_memory_logic_changed': False,
    'recorder': stage['record_format'],
    'scope_filter': stage['scope_filter'],
    'heartbeat': stage['heartbeat'],
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
A52 display recorder FEC persistent-RAM ring candidate

This revision preserves the recorder-v3 256-byte format, CRC32C,
32-byte Reed-Solomon parity, final commit footer, and three physical copies.

Each bank is owned by persistent_ram_new(). Records are written only through
persistent_ram_write(): the 240-byte protected codeword first and the 16-byte
commit footer second. The matching decoder reconstructs each persistent-RAM
circular stream before majority voting and Reed-Solomon correction.

After a failed boot, collect the untouched raw 1 MiB RAMOOPS image and decode:

  python3 tools/decode-a52-recorder-v3.py RAW_OR_ARCHIVE \
    --output decoded-recorder-v3

This build is not hardware validated until flashed and captured.
EOF_README

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
