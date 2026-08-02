#!/usr/bin/env bash
set -Eeuo pipefail

OUT="$PWD/artifacts/a52xq-smmu-display-contracts"
ZIP="$PWD/workspace/phase206-success.zip"
ARTIFACT_ID=8830356785
ARTIFACT_SHA256=f5e5c51cee21b1548aa19660f57e7b3f5cf05abee80035c7915d42d891d322e4

rm -rf "$OUT" "$ZIP"
mkdir -p "$OUT" "$PWD/workspace"

curl --fail --location --retry 5 --retry-all-errors --silent --show-error \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/artifacts/${ARTIFACT_ID}/zip" \
  --output "$ZIP"

printf '%s  %s\n' "$ARTIFACT_SHA256" "$ZIP" | sha256sum -c -
unzip -q "$ZIP" -d "$OUT"
(
  cd "$OUT"
  sha256sum -c SHA256SUMS
)

printf '%s\n' 'Phase 206 inherited artifact verification: PASS'
