#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

FROM_TAG="$LINUX_STABLE_BASE_TAG"
TO_TAG="$LINUX_STABLE_TARGET_TAG"
TARGET_VERSION="${TO_TAG#v}"
STABLE_DIR="$WORKSPACE/linux-stable-$TARGET_VERSION"
PATCH_FILE="$WORKSPACE/linux-${FROM_TAG#v}-to-${TO_TAG#v}.patch"
APPLY_LOG="$LOG_DIR/apply-$TO_TAG-probe.log"
REPORT="$ARTIFACTS_DIR/linux-$TARGET_VERSION-rebase-probe.txt"
REJECT_LIST="$ARTIFACTS_DIR/linux-$TARGET_VERSION-rejects.txt"
REJECT_ARCHIVE="$ARTIFACTS_DIR/linux-$TARGET_VERSION-rejects.tar.gz"

cleanup_report() {
  git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/source-status-$TO_TAG.txt" 2>/dev/null || true
  git -C "$KERNEL_DIR" diff --stat > "$ARTIFACTS_DIR/source-diff-$TO_TAG.stat.txt" 2>/dev/null || true
  find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$REJECT_LIST" 2>/dev/null || true
  if test -s "$REJECT_LIST"; then
    tar -C "$KERNEL_DIR" -czf "$REJECT_ARCHIVE" -T "$REJECT_LIST" 2>/dev/null || true
  fi
}
trap cleanup_report EXIT

test -d "$KERNEL_DIR/.git" || fail "Run 01_prepare_source.sh first"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Source is not at the pinned touchGrass commit"
test "$(kernel_version)" = "$TOUCHGRASS_BASE_VERSION" || fail "Expected Linux $TOUCHGRASS_BASE_VERSION before the rebase probe"
test -z "$(git -C "$KERNEL_DIR" status --porcelain)" || fail "Source tree is not clean"

info "Fetching official Linux stable endpoints $FROM_TAG and $TO_TAG"
rm -rf "$STABLE_DIR" "$PATCH_FILE"
git init -q "$STABLE_DIR"
git -C "$STABLE_DIR" remote add origin "$LINUX_STABLE_REPO"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$FROM_TAG:refs/tags/$FROM_TAG"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$TO_TAG:refs/tags/$TO_TAG"

from_sha=$(git -C "$STABLE_DIR" rev-parse "$FROM_TAG^{commit}")
to_sha=$(git -C "$STABLE_DIR" rev-parse "$TO_TAG^{commit}")
test "$from_sha" = "$LINUX_STABLE_BASE_COMMIT" || fail "Unexpected $FROM_TAG commit: $from_sha"
test "$to_sha" = "$LINUX_STABLE_TARGET_COMMIT" || fail "Unexpected $TO_TAG commit: $to_sha"

info "Generating the complete official stable delta"
git -C "$STABLE_DIR" diff --binary --full-index --no-renames "$FROM_TAG" "$TO_TAG" > "$PATCH_FILE"
test -s "$PATCH_FILE" || fail "Generated stable patch is empty"
git -C "$STABLE_DIR" diff --name-only "$FROM_TAG" "$TO_TAG" | sort > "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt"
git -C "$STABLE_DIR" diff --stat "$FROM_TAG" "$TO_TAG" > "$ARTIFACTS_DIR/upstream-diff-$TO_TAG.stat.txt"
sha256sum "$PATCH_FILE" > "$ARTIFACTS_DIR/linux-$TARGET_VERSION-patch.sha256"

set +e
git -C "$KERNEL_DIR" apply --check "$PATCH_FILE" > "$LOG_DIR/check-$TO_TAG-probe.log" 2>&1
check_rc=$?
set -e

info "Applying the full stable delta with reject preservation"
set +e
git -C "$KERNEL_DIR" apply --reject --whitespace=nowarn "$PATCH_FILE" > "$APPLY_LOG" 2>&1
apply_rc=$?
set -e

find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$REJECT_LIST"
reject_count=$(wc -l < "$REJECT_LIST")
current_version=$(kernel_version)

{
  printf 'source_repository=%s\n' "$TOUCHGRASS_REPO"
  printf 'source_commit=%s\n' "$TOUCHGRASS_COMMIT"
  printf 'from_tag=%s\n' "$FROM_TAG"
  printf 'from_commit=%s\n' "$from_sha"
  printf 'to_tag=%s\n' "$TO_TAG"
  printf 'to_commit=%s\n' "$to_sha"
  printf 'changed_files=%s\n' "$(wc -l < "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt")"
  printf 'patch_bytes=%s\n' "$(wc -c < "$PATCH_FILE")"
  printf 'check_exit=%s\n' "$check_rc"
  printf 'apply_exit=%s\n' "$apply_rc"
  printf 'reject_count=%s\n' "$reject_count"
  printf 'reported_kernel_version=%s\n' "$current_version"
} | tee "$REPORT"

if test "$apply_rc" -ne 0 || test "$reject_count" -ne 0; then
  printf 'result=conflicts-require-resolution\n' >> "$REPORT"
  fail "Linux $TARGET_VERSION rebase probe found $reject_count reject files; inspect the uploaded report and reject archive"
fi

test "$current_version" = "$TARGET_VERSION" || fail "Applied tree reports Linux $current_version instead of $TARGET_VERSION"
printf 'result=clean-apply\n' >> "$REPORT"
info "Linux stable delta applied cleanly: $TOUCHGRASS_BASE_VERSION -> $TARGET_VERSION"
