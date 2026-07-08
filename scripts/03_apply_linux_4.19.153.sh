#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

FROM_TAG=v4.19.152
TO_TAG=v4.19.153
TARGET_VERSION=4.19.153
STABLE_DIR="$WORKSPACE/linux-stable"
PATCH_FILE="$ARTIFACTS_DIR/linux-${FROM_TAG#v}-to-${TO_TAG#v}.patch"
CHECK_LOG="$LOG_DIR/apply-${TO_TAG}.check.log"
APPLY_LOG="$LOG_DIR/apply-${TO_TAG}.log"

cleanup_report() {
  git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/source-status-${TO_TAG}.txt" || true
  git -C "$KERNEL_DIR" diff --stat > "$ARTIFACTS_DIR/source-diff-${TO_TAG}.stat.txt" || true
  git -C "$KERNEL_DIR" diff --binary > "$ARTIFACTS_DIR/source-diff-${TO_TAG}.patch" || true
  find "$KERNEL_DIR" -type f -name '*.rej' -print | sort > "$ARTIFACTS_DIR/reject-files-${TO_TAG}.txt" || true
}
trap cleanup_report EXIT

test -d "$KERNEL_DIR/.git" || fail "Run 01_prepare_source.sh first"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Source is not at the pinned touchGrass commit"
test "$(kernel_version)" = "$TOUCHGRASS_BASE_VERSION" || fail "Expected Linux $TOUCHGRASS_BASE_VERSION before applying $TO_TAG"
test -z "$(git -C "$KERNEL_DIR" status --porcelain)" || fail "Source tree is not clean"

info "Fetching official stable trees $FROM_TAG and $TO_TAG"
rm -rf "$STABLE_DIR"
git init -q "$STABLE_DIR"
git -C "$STABLE_DIR" remote add origin "$LINUX_STABLE_REPO"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$FROM_TAG:refs/tags/$FROM_TAG"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$TO_TAG:refs/tags/$TO_TAG"

from_sha=$(git -C "$STABLE_DIR" rev-parse "$FROM_TAG^{commit}")
to_sha=$(git -C "$STABLE_DIR" rev-parse "$TO_TAG^{commit}")

test "$from_sha" = "$LINUX_STABLE_BASE_COMMIT" || fail "Unexpected $FROM_TAG commit: $from_sha"
test "$to_sha" = "79524e8c64bda80bb35ab490177d0e6813bf112c" || fail "Unexpected $TO_TAG commit: $to_sha"

info "Generating the official incremental stable patch"
git -C "$STABLE_DIR" diff --binary --full-index --no-renames "$FROM_TAG" "$TO_TAG" > "$PATCH_FILE"
test -s "$PATCH_FILE" || fail "Generated patch is empty"

git -C "$STABLE_DIR" diff --name-only "$FROM_TAG" "$TO_TAG" | sort > "$ARTIFACTS_DIR/upstream-files-${TO_TAG}.txt"
git -C "$STABLE_DIR" diff --stat "$FROM_TAG" "$TO_TAG" > "$ARTIFACTS_DIR/upstream-diff-${TO_TAG}.stat.txt"
sha256sum "$PATCH_FILE" > "$PATCH_FILE.sha256"

{
  printf 'from_tag=%s\n' "$FROM_TAG"
  printf 'from_commit=%s\n' "$from_sha"
  printf 'to_tag=%s\n' "$TO_TAG"
  printf 'to_commit=%s\n' "$to_sha"
  printf 'changed_files=%s\n' "$(wc -l < "$ARTIFACTS_DIR/upstream-files-${TO_TAG}.txt")"
  printf 'patch_bytes=%s\n' "$(wc -c < "$PATCH_FILE")"
} | tee "$ARTIFACTS_DIR/update-metadata-${TO_TAG}.txt"

info "Checking whether the stable patch applies cleanly to touchGrass"
set +e
git -C "$KERNEL_DIR" apply --check --whitespace=nowarn "$PATCH_FILE" > "$CHECK_LOG" 2>&1
check_rc=$?
set -e

if [ "$check_rc" -ne 0 ]; then
  info "Clean apply check failed; generating reject files for review"
  set +e
  git -C "$KERNEL_DIR" apply --reject --whitespace=nowarn "$PATCH_FILE" > "$APPLY_LOG" 2>&1
  apply_rc=$?
  set -e
  printf 'check_exit=%s\nreject_apply_exit=%s\n' "$check_rc" "$apply_rc" > "$ARTIFACTS_DIR/apply-result-${TO_TAG}.txt"
  fail "The $FROM_TAG to $TO_TAG patch has conflicts. Review the uploaded reject report."
fi

git -C "$KERNEL_DIR" apply --whitespace=nowarn "$PATCH_FILE" > "$APPLY_LOG" 2>&1
actual_version=$(kernel_version)
test "$actual_version" = "$TARGET_VERSION" || fail "Patch applied but Makefile reports $actual_version"

printf 'check_exit=0\napply_exit=0\nkernel_version=%s\n' "$actual_version" > "$ARTIFACTS_DIR/apply-result-${TO_TAG}.txt"
info "Official stable update applied cleanly: $TOUCHGRASS_BASE_VERSION -> $TARGET_VERSION"
