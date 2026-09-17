#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/08_build_resukisu_safe_checkpoint.sh"
GENERATED="$SCRIPT_DIR/.phase101-generated-resukisu-susfs.sh"

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

TARGET_VERSION="$(make -s -C "$SCRIPT_DIR/../workspace/touchgrass-a52xq" kernelversion)"
: "${TARGET_VERSION:?failed to determine candidate kernel version}"
: "${LABEL:?Phase101 LABEL environment variable is required}"

if [ "$TARGET_VERSION" != "4.19.235" ]; then
  echo "Expected Phase101 candidate 4.19.235, got $TARGET_VERSION" >&2
  exit 1
fi

# Phase101 runs only after the prepared Phase88 tree has been validated and,
# on a cache miss, quiesced so the complete Git object database is safe to archive.
# Repair only the two 4.19.235 vendor/upstream merge shapes discovered by the
# first full compile: pm_show_wakelocks() and the FUSE private inode-state bit.
bash "$SCRIPT_DIR/101_fix_419235_compile.sh"

python3 - "$SOURCE" "$GENERATED" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1])
out = Path(sys.argv[2])
text = source.read_text()
anchor = 'bash "$GENERATED"\n'
replacement = '"$SCRIPT_DIR/56_patch_and_run_resukisu_susfs_manual_core.sh" "$GENERATED"\n'
if text.count(anchor) != 1:
    raise SystemExit('safe checkpoint final execution anchor mismatch')
out.write_text(text.replace(anchor, replacement, 1))
out.chmod(0o755)
PY

bash "$GENERATED" "$TARGET_VERSION" susfs

SOURCE_IMAGE="$SCRIPT_DIR/../artifacts/Image-touchgrass-${TARGET_VERSION}-resukisu-v4.1.0-susfs-v1.4.2-manual-core"
CANON_IMAGE="$SCRIPT_DIR/../artifacts/Image-${LABEL}"
FINAL_CONFIG="$SCRIPT_DIR/../workspace/touchgrass-a52xq/out/.config"
CANON_CONFIG="$SCRIPT_DIR/../artifacts/config-${LABEL}"

test -s "$SOURCE_IMAGE"
test -s "$FINAL_CONFIG"
cp -f "$SOURCE_IMAGE" "$CANON_IMAGE"
cp -f "$FINAL_CONFIG" "$CANON_CONFIG"

sha256sum "$CANON_IMAGE"
sha256sum "$CANON_CONFIG"
echo "phase101_kernelversion=$TARGET_VERSION"
echo "phase101_image=$CANON_IMAGE"
echo "phase101_config=$CANON_CONFIG"
