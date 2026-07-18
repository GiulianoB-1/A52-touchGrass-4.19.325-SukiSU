#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.206
FIX_COMMIT=75c9b1955b7e1f0a959b70f9d631a93634d742e5
ANDROID_COMMON_REPO=https://android.googlesource.com/kernel/common
WORK="$WORKSPACE/bpf-ringbuf-overrun-fix"
PATCH="$ARTIFACTS_DIR/bpf-ringbuf-patches/ringbuf-overrunning-reservations.patch"
RINGBUF="$KERNEL_DIR/kernel/bpf/ringbuf.c"

[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Expected Linux $TARGET_VERSION"
[[ -s "$RINGBUF" ]] || fail "BPF ring-buffer implementation is missing"
mkdir -p "$WORK" "$(dirname "$PATCH")"
rm -rf "$WORK"/*

git init -q "$WORK/donor"
git -C "$WORK/donor" remote add origin "$ANDROID_COMMON_REPO"
git -C "$WORK/donor" -c protocol.version=2 fetch --quiet --filter=blob:none --depth=2 origin "$FIX_COMMIT"
resolved=$(git -C "$WORK/donor" rev-parse FETCH_HEAD)
parent=$(git -C "$WORK/donor" rev-parse "${resolved}^")
git -C "$WORK/donor" diff --binary --full-index "$parent" "$resolved" -- kernel/bpf/ringbuf.c > "$PATCH"
test -s "$PATCH" || fail "Android ring-buffer overrun fix patch is empty"

if git -C "$KERNEL_DIR" apply --check "$PATCH"; then
  git -C "$KERNEL_DIR" apply --index "$PATCH"
  result=applied
elif git -C "$KERNEL_DIR" apply --reverse --check "$PATCH"; then
  result=already-present
else
  set +e
  git -C "$KERNEL_DIR" apply --3way --index "$PATCH" \
    > "$ARTIFACTS_DIR/ringbuf-overrunning-reservations-threeway.log" 2>&1
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/ringbuf-overrunning-reservations-conflicts.txt" || true
    git -C "$KERNEL_DIR" diff --name-only --diff-filter=U > "$ARTIFACTS_DIR/ringbuf-overrunning-reservations-unmerged-paths.txt" || true
    fail "Android 4.19 ring-buffer overrun fix does not apply cleanly"
  fi
  result=applied-threeway
fi

grep -Fq 'unsigned long pending_pos;' "$RINGBUF" || fail "pending_pos tracking is missing"
grep -Fq 'new_prod_pos - pend_pos' "$RINGBUF" || fail "oldest-pending-record bound is missing"
grep -Fq 'BPF_RINGBUF_BUSY_BIT' "$RINGBUF" || fail "ring-buffer busy-record tracking is missing"

git -C "$KERNEL_DIR" add -A
git -C "$KERNEL_DIR" commit -m 'Backport ringbuf overrunning-reservations fix' >/dev/null || true
{
  echo "ringbuf_overrun_fix_commit=$FIX_COMMIT"
  echo "ringbuf_overrun_fix_result=$result"
  echo 'pending_record_span_check=present'
} | tee "$ARTIFACTS_DIR/bpf-ringbuf-overrun-fix.txt"
