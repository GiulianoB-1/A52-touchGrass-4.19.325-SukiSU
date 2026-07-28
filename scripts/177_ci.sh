#!/usr/bin/env bash
set -Eeuo pipefail

OUT="$PWD/artifacts/a52xq-display-init-recorder-plain"
BUILD="$PWD/workspace/gki-display-init-recorder-plain-out"
PATCH="$PWD/patches/177-a52-display-init-recorder-plain.patch"
DECODER="$PWD/tools/decode-a52-display-init-trace.py"
mkdir -p "$OUT"/{logs,stage,config,compile,package,tools} "$BUILD"
trap 'rc=$?; printf "line=%s\ncommand=%s\nreturn_code=%s\n" "$LINENO" "$BASH_COMMAND" "$rc" > "$OUT/logs/ci-failure.txt"; exit "$rc"' ERR

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GKI_COMMON_SHA:?GKI_COMMON_SHA is required}"
: "${SOURCE_ARTIFACT_ID:?SOURCE_ARTIFACT_ID is required}"
: "${SOURCE_ARTIFACT_SHA256:?SOURCE_ARTIFACT_SHA256 is required}"
test "${GKI_CACHE_HIT:-false}" = true

test -s "$PATCH"
test -s "$DECODER"
python3 -m py_compile "$DECODER" scripts/38_repack_a52_p1_boot.py
python3 "$DECODER" --self-test

# This patch must remain plain-text instrumentation only. It may not add a new
# mapping, a binary recorder, checksums, voting, or error-correction code.
! grep -Eiq 'reed[ -]?solomon|encode_rs|decode_rs|A52_REC3|crc32c|majority[ -]?vote|persistent_ram_new|ioremap' "$PATCH"
grep -Fq 'heap19-display-init-trace-plain-v1' "$PATCH"
grep -Fq 'A52_DSI_CMD_TRACE_PLAIN_V1' "$PATCH"
grep -Fq 'plain text, no correction or binary format' "$PATCH"

sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc || true
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git curl ca-certificates unzip make gcc g++ python3 bc bison flex \
  clang lld llvm gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
  libssl-dev libelf-dev rsync cpio gzip lz4 file dwarves

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
BASE_PATCH="$PWD/source/extracted/stage/heap19-display-bindcore-source.patch"
git -C gki/common apply --check "$BASE_PATCH"
git -C gki/common apply "$BASE_PATCH"

# Hash the persistent backend and unrelated sensitive paths before the trace
# patch. They must be byte-identical afterward.
sha256sum \
  gki/common/fs/pstore/ram.c \
  gki/common/init/main.c \
  gki/common/drivers/soc/qcom/qseecom.c \
  > "$OUT/stage/pre-instrumentation-invariants.sha256"

git -C gki/common apply --check "$PATCH"
git -C gki/common apply "$PATCH"
sha256sum -c "$OUT/stage/pre-instrumentation-invariants.sha256"

git -C gki/common diff --check

REC=gki/common/drivers/a52_secure/a52_ack_secure_flight_recorder.c
DISPLAY=gki/common/techpack/display/msm/dsi/dsi_display.c
PANEL=gki/common/techpack/display/msm/dsi/dsi_panel.c
PWR=gki/common/techpack/display/msm/dsi/dsi_pwr.c
CLK=gki/common/techpack/display/msm/dsi/dsi_clk_manager.c
CTRL=gki/common/techpack/display/msm/dsi/dsi_ctrl.c
PHY=gki/common/techpack/display/msm/dsi/dsi_phy.c
DRM=gki/common/techpack/display/msm/dsi/dsi_drm.c
SS=gki/common/techpack/display/msm/samsung/ss_dsi_panel_common.c
SPEC=gki/common/techpack/display/msm/samsung/S6E3FC3_AMS646YD01/ss_dsi_panel_S6E3FC3_AMS646YD01.c

# Preserve the proven recorder implementation and capacity.
grep -Fq '#define A52_USR2_CAPACITY 768U' "$REC"
grep -Fq 'profile=heap19-display-init-trace-plain-v1' "$REC"
grep -Fq 'a52_ackfr_ramoops_write(line, len, A52_USR2_BANK_BOTH)' "$REC"
! grep -Eiq 'reed[ -]?solomon|crc32c|A52_REC3|majority' "$REC"

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
grep -Fq 'DISP VREG+ done' "$PWR"
grep -Fq 'A52_CLK_TRACE_PLAIN_V1' "$CLK"
grep -Fq 'DISP CLK byte' "$CLK"
grep -Fq 'A52_DSI_CMD_TRACE_PLAIN_V1' "$CTRL"
grep -Fq 'DISP DSI i=%d' "$CTRL"
grep -Fq 'DISP HOST i=%d' "$CTRL"
grep -Fq 'A52_PHY_TRACE_PLAIN_V1' "$PHY"
grep -Fq 'A52_DRM_DISPLAY_TRACE_PLAIN_V1' "$DRM"
grep -Fq 'A52_SAMSUNG_TRACE_PLAIN_V1' "$SS"
grep -Fq 'A52_PANEL_SPECIFIC_TRACE_PLAIN_V1' "$SPEC"

# Ensure the instrumentation itself does not introduce extra hardware reads or
# a different recorder backend.
! git -C gki/common diff --unified=0 | grep '^+' | grep -Eq \
  'regulator_is_enabled|gpio_get_value|ss_panel_attach_get|persistent_ram_new|ioremap|encode_rs|decode_rs|crc32c'

git -C gki/common diff --binary --no-ext-diff > "$OUT/stage/display-init-recorder-plain-source.patch"
test -s "$OUT/stage/display-init-recorder-plain-source.patch"
cp "$PATCH" "$OUT/stage/"

python3 - <<'PY'
import json
from pathlib import Path
root = Path('artifacts/a52xq-display-init-recorder-plain')
report = {
    'status': 'a52-display-init-recorder-plain-staged',
    'hardware_validated': False,
    'base_profile': 'heap19-bufops-display-bindcore-v1',
    'persistent_profile': 'heap19-display-init-trace-plain-v1',
    'backend': 'unchanged-proven-a52usr2-dual-persistent-ram-text',
    'error_correction': False,
    'binary_records': False,
    'new_persistent_mapping': False,
    'recorder_capacity_changed': False,
    'display_control_flow_changed': False,
    'refgen_logic_changed': False,
    'secure_memory_logic_changed': False,
    'capture': {
        'probe_and_bind': True,
        'mode_and_bridge': True,
        'regulators': True,
        'pinctrl': True,
        'gpio_reset_sequence': True,
        'clocks_and_rates': True,
        'controller_host_config': True,
        'phy': True,
        'panel_command_sets': True,
        'samsung_command_names': True,
        'dsi_packet_metadata': True,
        'dsi_payload_prefix_bytes': 8,
        'dsi_payload_suffix_bytes': 4,
        'backlight': True,
        'timestamps': 'existing A52USR2 monotonic_ns',
    },
}
(root / 'stage/phase35-a52-display-init-recorder-report.json').write_text(
    json.dumps(report, indent=2, sort_keys=True) + '\n')
PY

cp source/extracted/config/final.config "$BUILD/.config"
CLANG="$(readlink -f "$(command -v clang)")"
export PATH="$(dirname "$CLANG"):$PATH"
export CROSS_COMPILE=aarch64-linux-gnu-
export CLANG_TRIPLE=aarch64-linux-gnu-
make -C gki/common O="$BUILD" ARCH=arm64 LLVM=1 LLVM_IAS=1 olddefconfig \
  > "$OUT/logs/olddefconfig.log" 2>&1
cp "$BUILD/.config" "$OUT/config/final.config"
grep -Fxq 'CONFIG_PSTORE_RAM=y' "$BUILD/.config"

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
  'CC      techpack/display/msm/dsi/dsi_display.o' \
  'CC      techpack/display/msm/dsi/dsi_panel.o' \
  'CC      techpack/display/msm/dsi/dsi_ctrl.o' \
  'CC      techpack/display/msm/dsi/dsi_pwr.o' \
  'CC      techpack/display/msm/dsi/dsi_clk_manager.o' \
  'CC      techpack/display/msm/dsi/dsi_phy.o'; do
  grep -Fq "$object" "$OUT/logs/compile.log"
done
for marker in \
  'heap19-display-init-trace-plain-v1' \
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

python3 scripts/38_repack_a52_p1_boot.py \
  --source source/extracted/package/boot.img \
  --kernel "$OUT/package/Image.gz" \
  --output "$OUT/package/boot.img" \
  --report "$OUT/package/repack-report.json"
cp "$DECODER" "$OUT/tools/"

python3 - <<'PY'
import hashlib
import json
from pathlib import Path
root = Path('artifacts/a52xq-display-init-recorder-plain')
stage = json.loads((root / 'stage/phase35-a52-display-init-recorder-report.json').read_text())
repack = json.loads((root / 'package/repack-report.json').read_text())
image = root / 'compile/Image'
boot = root / 'package/boot.img'
audit = {
    'status': 'a52-display-init-recorder-plain-boot-audited',
    'flashable_candidate': True,
    'hardware_validated': False,
    'persistent_profile': stage['persistent_profile'],
    'backend': stage['backend'],
    'error_correction': False,
    'binary_records': False,
    'new_persistent_mapping': False,
    'recorder_capacity_changed': False,
    'source_artifact_id': 8681171875,
    'source_binding_candidate_preserved': True,
    'display_control_flow_changed': False,
    'refgen_logic_changed': False,
    'secure_memory_logic_changed': False,
    'capture': stage['capture'],
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

cat > "$OUT/README-FIRST.txt" <<'EOF'
A52 plain-text display initialization recorder

This build starts from the last display-bindcore baseline and preserves its
proven A52USR2 dual text recorder. It does not add Reed-Solomon, CRC recovery,
majority voting, binary records, or a new persistent-RAM mapping.

It records display probe/bind, mode, regulators, pinctrl, reset GPIO sequence,
clock requests and rates, controller/PHY configuration, Samsung command-set
names, compact DSI packet metadata with the first 8 and last 4 TX bytes, and
backlight requests.

After the failed boot, collect the untouched raw RAMOOPS ZIP and decode:

  python3 tools/decode-a52-display-init-trace.py RAW_OR_ARCHIVE \
    --output decoded-display-init

This build is not hardware validated until flashed and captured.
EOF

(
  cd "$OUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | \
    xargs -0 sha256sum > SHA256SUMS
  sha256sum -c SHA256SUMS
)
