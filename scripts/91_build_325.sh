#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/08_build_resukisu_safe_checkpoint.sh"
GENERATED="$SCRIPT_DIR/.generated-resukisu-susfs-manual-detection-checkpoint.sh"

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

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

bash "$GENERATED" 4.19.325 susfs

# The legacy safe-checkpoint builder intentionally publishes its verified
# image with the historical "manual-core" label. Phase91 uses a different
# workflow label for auditing and packaging, so publish canonical aliases
# for both the Image and the exact final kernel config without changing the
# proven build helper itself.
: "${LABEL:?Phase91 LABEL environment variable is required}"
SOURCE_IMAGE="$SCRIPT_DIR/../artifacts/Image-touchgrass-4.19.325-resukisu-v4.1.0-susfs-v1.4.2-manual-core"
PHASE91_IMAGE="$SCRIPT_DIR/../artifacts/Image-${LABEL}"
FINAL_CONFIG="$SCRIPT_DIR/../workspace/touchgrass-a52xq/out/.config"
PHASE91_CONFIG="$SCRIPT_DIR/../artifacts/config-${LABEL}"

test -s "$SOURCE_IMAGE"
test -s "$FINAL_CONFIG"
cp -f "$SOURCE_IMAGE" "$PHASE91_IMAGE"
cp -f "$FINAL_CONFIG" "$PHASE91_CONFIG"
test -s "$PHASE91_IMAGE"
test -s "$PHASE91_CONFIG"

sha256sum "$PHASE91_IMAGE"
sha256sum "$PHASE91_CONFIG"
echo "phase91_image=$PHASE91_IMAGE"
echo "phase91_config=$PHASE91_CONFIG"
