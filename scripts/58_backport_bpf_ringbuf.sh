#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GENERATED="$SCRIPT_DIR/.generated-58-backport-bpf-ringbuf.sh"
PINNED_IMPLEMENTATION=d272a8c9ba39602d2f9b20f6f559610ee16813f5
ANDROID_COMMON_REPO=https://android.googlesource.com/kernel/common
ARCHIVED_REF=refs/heads/deprecated/android-4.19-stable
ARCHIVED_HEAD=a8bf86a0e0fa05070897a210d706d5c4d83c26ac
RAW_URL="https://raw.githubusercontent.com/GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/${PINNED_IMPLEMENTATION}/scripts/58_backport_bpf_ringbuf.sh"

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

# Verify the archived Android 4.19 branch still resolves to the reviewed head,
# then use the immutable SHA in the Gitiles path-history URL. Gitiles returns
# HTTP 401 when the slash-containing deprecated ref is embedded directly.
resolved=$(git ls-remote "$ANDROID_COMMON_REPO" "$ARCHIVED_REF" | awk 'NR==1 {print $1}')
test "$resolved" = "$ARCHIVED_HEAD" || {
  echo "ERROR: archived Android 4.19 head changed: ${resolved:-missing}" >&2
  exit 1
}

curl --fail --location --retry 3 --silent --show-error \
  "$RAW_URL" --output "$GENERATED"
sed -i \
  "s#refs/heads/android-4.19-stable#${ARCHIVED_HEAD}#g" \
  "$GENERATED"
chmod +x "$GENERATED"

mkdir -p "$PROJECT_DIR/artifacts"
{
  echo "archived_ref=$ARCHIVED_REF"
  echo "archived_head=$ARCHIVED_HEAD"
} > "$PROJECT_DIR/artifacts/android-4.19-archived-ref.txt"
set -o pipefail
"$GENERATED" 2>&1 | tee "$PROJECT_DIR/artifacts/bpf-ringbuf-backport-step.log"
