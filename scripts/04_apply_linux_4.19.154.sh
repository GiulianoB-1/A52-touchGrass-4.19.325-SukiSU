#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

FROM_TAG=v4.19.153
TO_TAG=v4.19.154
FROM_VERSION=${FROM_TAG#v}
TARGET_VERSION=${TO_TAG#v}
STABLE_DIR="$WORKSPACE/linux-stable-$TARGET_VERSION"
PATCH_FILE="$ARTIFACTS_DIR/linux-$FROM_VERSION-to-$TARGET_VERSION.patch"
APPLY_LOG="$LOG_DIR/apply-$TO_TAG.log"
REPORT="$ARTIFACTS_DIR/update-$TO_TAG.txt"
REJECT_LIST="$ARTIFACTS_DIR/reject-files-$TO_TAG.txt"
REJECT_ARCHIVE="$ARTIFACTS_DIR/reject-files-$TO_TAG.tar.gz"

cleanup_report() {
  git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/source-status-$TO_TAG.txt" 2>/dev/null || true
  git -C "$KERNEL_DIR" diff --stat > "$ARTIFACTS_DIR/source-diff-$TO_TAG.stat.txt" 2>/dev/null || true
  git -C "$KERNEL_DIR" diff --binary > "$ARTIFACTS_DIR/source-diff-$TO_TAG.patch" 2>/dev/null || true
  find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$REJECT_LIST" 2>/dev/null || true
  if test -s "$REJECT_LIST"; then
    tar -C "$KERNEL_DIR" -czf "$REJECT_ARCHIVE" -T "$REJECT_LIST" 2>/dev/null || true
  fi
}
trap cleanup_report EXIT

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Unexpected touchGrass base commit"
test "$(kernel_version)" = "$FROM_VERSION" || fail "Expected Linux $FROM_VERSION before applying $TO_TAG"
if find "$KERNEL_DIR" -type f -name '*.rej' -print -quit | grep -q .; then
  fail "Pre-existing reject files must be resolved before applying $TO_TAG"
fi

info "Fetching official Linux stable tags $FROM_TAG and $TO_TAG"
rm -rf "$STABLE_DIR"
git init -q "$STABLE_DIR"
git -C "$STABLE_DIR" remote add origin "$LINUX_STABLE_REPO"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$FROM_TAG:refs/tags/$FROM_TAG"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$TO_TAG:refs/tags/$TO_TAG"

from_sha=$(git -C "$STABLE_DIR" rev-parse "$FROM_TAG^{commit}")
to_sha=$(git -C "$STABLE_DIR" rev-parse "$TO_TAG^{commit}")
test "$from_sha" = "79524e8c64bda80bb35ab490177d0e6813bf112c" || fail "Unexpected $FROM_TAG commit: $from_sha"

stable_version=$(git -C "$STABLE_DIR" show "$TO_TAG:Makefile" | awk '
  $1=="VERSION" {v=$3}
  $1=="PATCHLEVEL" {p=$3}
  $1=="SUBLEVEL" {s=$3}
  END {printf "%s.%s.%s", v, p, s}
')
test "$stable_version" = "$TARGET_VERSION" || fail "$TO_TAG reports Linux $stable_version"

info "Generating official incremental stable delta $FROM_TAG -> $TO_TAG"
git -C "$STABLE_DIR" diff --binary --full-index --no-renames "$FROM_TAG" "$TO_TAG" > "$PATCH_FILE"
test -s "$PATCH_FILE" || fail "Generated stable patch is empty"
git -C "$STABLE_DIR" diff --name-only "$FROM_TAG" "$TO_TAG" | sort > "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt"
git -C "$STABLE_DIR" diff --stat "$FROM_TAG" "$TO_TAG" > "$ARTIFACTS_DIR/upstream-diff-$TO_TAG.stat.txt"
sha256sum "$PATCH_FILE" > "$PATCH_FILE.sha256"

info "Applying Linux $TARGET_VERSION with reject preservation"
set +e
git -C "$KERNEL_DIR" apply --reject --whitespace=nowarn "$PATCH_FILE" > "$APPLY_LOG" 2>&1
apply_rc=$?
set -e

find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$REJECT_LIST"
reject_count=$(wc -l < "$REJECT_LIST")
current_version=$(kernel_version)

{
  printf 'from_tag=%s\n' "$FROM_TAG"
  printf 'from_commit=%s\n' "$from_sha"
  printf 'to_tag=%s\n' "$TO_TAG"
  printf 'to_commit=%s\n' "$to_sha"
  printf 'upstream_commit_count=119\n'
  printf 'changed_files=%s\n' "$(wc -l < "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt")"
  printf 'patch_bytes=%s\n' "$(wc -c < "$PATCH_FILE")"
  printf 'apply_exit=%s\n' "$apply_rc"
  printf 'reject_count=%s\n' "$reject_count"
  printf 'reported_kernel_version=%s\n' "$current_version"
} | tee "$REPORT"

if test "$apply_rc" -ne 0 || test "$reject_count" -ne 0; then
  printf 'result=conflicts-require-resolution\n' >> "$REPORT"
  fail "Linux $TARGET_VERSION update produced $reject_count reject files; inspect the uploaded artifact"
fi

test "$current_version" = "$TARGET_VERSION" || fail "Applied tree reports Linux $current_version instead of $TARGET_VERSION"
git -C "$KERNEL_DIR" diff --check
printf 'result=clean-apply\n' >> "$REPORT"
info "Official Linux stable update applied cleanly: $FROM_VERSION -> $TARGET_VERSION"