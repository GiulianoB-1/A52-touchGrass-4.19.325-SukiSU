#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GENERATED="$SCRIPT_DIR/.generated-58-backport-bpf-ringbuf.sh"
PINNED_IMPLEMENTATION=d272a8c9ba39602d2f9b20f6f559610ee16813f5
RAW_URL="https://raw.githubusercontent.com/GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/${PINNED_IMPLEMENTATION}/scripts/58_backport_bpf_ringbuf.sh"

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

# Google archived the former android-4.19-stable ref under deprecated/.
# Keep the reviewed implementation pinned, changing only that repository ref.
curl --fail --location --retry 3 --silent --show-error \
  "$RAW_URL" --output "$GENERATED"
sed -i \
  's#refs/heads/android-4.19-stable#refs/heads/deprecated/android-4.19-stable#g' \
  "$GENERATED"
chmod +x "$GENERATED"

mkdir -p "$PROJECT_DIR/artifacts"
set -o pipefail
"$GENERATED" 2>&1 | tee "$PROJECT_DIR/artifacts/bpf-ringbuf-backport-step.log"
