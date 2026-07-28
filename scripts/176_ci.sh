#!/usr/bin/env bash
set -Eeuo pipefail

OUT="$PWD/artifacts/a52xq-display-recorder-fec"
mkdir -p "$OUT/logs"

# Execute the last full CI implementation under xtrace so any early failure is
# preserved in the failed-diagnostics artifact. The known commit contains the
# complete recorder build script before this diagnostic wrapper was installed.
SOURCE_COMMIT=e04c9701a23e5d3eaa43196125a7d91c77c1d009

git fetch --no-tags --depth=1 origin "$SOURCE_COMMIT"
git show "$SOURCE_COMMIT:scripts/176_ci.sh" > /tmp/a52-176-ci-original.sh
chmod +x /tmp/a52-176-ci-original.sh

set +e
PS4='+ line=${LINENO} command=' bash -x /tmp/a52-176-ci-original.sh \
  > >(tee "$OUT/logs/ci-xtrace.log") \
  2> >(tee "$OUT/logs/ci-xtrace.stderr.log" >&2)
rc=$?
set -e

printf 'source_commit=%s\nreturn_code=%s\n' "$SOURCE_COMMIT" "$rc" \
  > "$OUT/logs/ci-wrapper-result.txt"
exit "$rc"
