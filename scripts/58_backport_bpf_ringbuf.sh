#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GENERATED="$SCRIPT_DIR/.generated-58-backport-bpf-ringbuf.sh"
PINNED_IMPLEMENTATION=d272a8c9ba39602d2f9b20f6f559610ee16813f5
ANDROID_COMMON_REPO=https://android.googlesource.com/kernel/common
ARCHIVED_REF=refs/heads/deprecated/android-4.19-stable
ARCHIVED_HEAD=a8bf86a0e0fa05070897a210d706d5c4d83c26ac
RINGBUF_INTRO=457f44363a8894135c85b7a9afd2bd8196db24ab
RAW_URL="https://raw.githubusercontent.com/GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/${PINNED_IMPLEMENTATION}/scripts/58_backport_bpf_ringbuf.sh"

cleanup() {
  rm -f "$GENERATED"
}
trap cleanup EXIT

# Verify the archived Android 4.19 branch still resolves to the reviewed head.
resolved=$(git ls-remote "$ANDROID_COMMON_REPO" "$ARCHIVED_REF" | awk 'NR==1 {print $1}')
test "$resolved" = "$ARCHIVED_HEAD" || {
  echo "ERROR: archived Android 4.19 head changed: ${resolved:-missing}" >&2
  exit 1
}

curl --fail --location --retry 3 --silent --show-error \
  "$RAW_URL" --output "$GENERATED"

# Gitiles path-history requests for the archived branch are rate-limited and
# occasionally return 401/429. The Android common mirror confirms that the
# feature entered its history as the original immutable upstream commit, so
# replace only the discovery block and keep the reviewed patch/apply logic.
python3 - "$GENERATED" "$RINGBUF_INTRO" "$ARCHIVED_HEAD" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
intro = sys.argv[2]
archived_head = sys.argv[3]
text = path.read_text()
start_marker = 'info "Finding the BPF ring-buffer introduction on Android\'s 4.19 branch"\n'
end_marker = 'cp "$INTRO_META" "$ARTIFACTS_DIR/android-4.19-ringbuf-introduction.json"\n'
start = text.index(start_marker)
end = text.index(end_marker, start) + len(end_marker)
replacement = f'''info "Using the pinned Android 4.19 BPF ring-buffer introduction"
INTRO_META="$WORK/android-4.19-ringbuf-introduction.json"
INTRO_COMMIT={intro}
INTRO_SUBJECT='bpf: Implement BPF ring buffer and verifier support for it'
python3 - "$INTRO_META" "$INTRO_COMMIT" "$INTRO_SUBJECT" <<'PYMETA'
import json
import sys

out, commit, subject = sys.argv[1:]
with open(out, "w", encoding="utf-8") as handle:
    json.dump({{
        "commit": commit,
        "subject": subject,
        "discovery": "pinned-android-common-history",
    }}, handle, indent=2)
print(json.dumps({{"commit": commit, "subject": subject}}))
PYMETA
cp "$INTRO_META" "$ARTIFACTS_DIR/android-4.19-ringbuf-introduction.json"
'''
text = text[:start] + replacement + text[end:]
text = text.replace('refs/heads/android-4.19-stable', archived_head)
path.write_text(text)
PY
chmod +x "$GENERATED"

mkdir -p "$PROJECT_DIR/artifacts"
{
  echo "archived_ref=$ARCHIVED_REF"
  echo "archived_head=$ARCHIVED_HEAD"
  echo "ringbuf_intro=$RINGBUF_INTRO"
} > "$PROJECT_DIR/artifacts/android-4.19-archived-ref.txt"
set -o pipefail
"$GENERATED" 2>&1 | tee "$PROJECT_DIR/artifacts/bpf-ringbuf-backport-step.log"
