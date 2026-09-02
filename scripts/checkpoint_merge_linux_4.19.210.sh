#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

FROM_TAG=v4.19.200
TO_TAG=v4.19.210
FROM_VERSION=${FROM_TAG#v}
TARGET_VERSION=${TO_TAG#v}
FROM_COMMIT=53bd76690e27f37c9df221a651a52cea04214da9
TARGET_COMMIT=e34184f53363f6bb873c2fe0ce1a08ed7d16e94a
STABLE_DIR="$WORKSPACE/linux-stable-direct-$TARGET_VERSION"
BASE_TREE="$WORKSPACE/linux-base-$FROM_VERSION"
THEIRS_TREE="$WORKSPACE/linux-theirs-$TARGET_VERSION"
STATUS_FILE="$ARTIFACTS_DIR/direct-merge-name-status-$TO_TAG.zlist"
CONFLICT_LIST="$ARTIFACTS_DIR/direct-merge-conflicts-$TO_TAG.txt"
CONFLICT_ARCHIVE="$ARTIFACTS_DIR/direct-merge-conflicts-$TO_TAG.tar.gz"
POLICY_LOG="$ARTIFACTS_DIR/direct-merge-policy-$TO_TAG.tsv"
POLICY_REPORT="$ARTIFACTS_DIR/direct-merge-policy-raw-$TO_TAG.txt"
REPORT="$ARTIFACTS_DIR/direct-merge-$TO_TAG.txt"
POLICY_SCRIPT="$(dirname "$0")/05_merge_linux_4.19.325.py"

cleanup_report() {
  mkdir -p "$ARTIFACTS_DIR"
  git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/source-status-$TO_TAG.txt" 2>/dev/null || true
  git -C "$KERNEL_DIR" diff --stat > "$ARTIFACTS_DIR/source-diff-$TO_TAG.stat.txt" 2>/dev/null || true
  if test -s "$CONFLICT_LIST"; then
    tar --ignore-failed-read -C "$KERNEL_DIR" -czf "$CONFLICT_ARCHIVE" -T "$CONFLICT_LIST" 2>/dev/null || true
  fi
}
trap cleanup_report EXIT

mkdir -p "$ARTIFACTS_DIR" "$LOG_DIR"
test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = "$TOUCHGRASS_COMMIT" || fail "Unexpected touchGrass base commit"
test "$(kernel_version)" = "$FROM_VERSION" || fail "Expected Linux $FROM_VERSION before direct merge"
test -f "$POLICY_SCRIPT" || fail "Direct merge policy resolver is missing"
if find "$KERNEL_DIR" -type f -name '*.rej' -print -quit | grep -q .; then
  fail "Pre-existing reject files must be resolved before the direct merge"
fi

info "Fetching official Linux stable endpoints $FROM_TAG and $TO_TAG"
rm -rf "$STABLE_DIR" "$BASE_TREE" "$THEIRS_TREE"
git init -q "$STABLE_DIR"
git -C "$STABLE_DIR" remote add origin "$LINUX_STABLE_REPO"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$FROM_TAG:refs/tags/$FROM_TAG"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$TO_TAG:refs/tags/$TO_TAG"

from_sha=$(git -C "$STABLE_DIR" rev-parse "$FROM_TAG^{commit}")
to_sha=$(git -C "$STABLE_DIR" rev-parse "$TO_TAG^{commit}")
test "$from_sha" = "$FROM_COMMIT" || fail "Unexpected $FROM_TAG commit: $from_sha"
test "$to_sha" = "$TARGET_COMMIT" || fail "Unexpected $TO_TAG commit: $to_sha"

mkdir -p "$BASE_TREE" "$THEIRS_TREE"
git -C "$STABLE_DIR" archive "$FROM_TAG" | tar -x -C "$BASE_TREE"
git -C "$STABLE_DIR" archive "$TO_TAG" | tar -x -C "$THEIRS_TREE"
git -C "$STABLE_DIR" diff --name-status -z --no-renames "$FROM_TAG" "$TO_TAG" > "$STATUS_FILE"
git -C "$STABLE_DIR" diff --name-only "$FROM_TAG" "$TO_TAG" | sort > "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt"
git -C "$STABLE_DIR" diff --stat "$FROM_TAG" "$TO_TAG" > "$ARTIFACTS_DIR/upstream-diff-$TO_TAG.stat.txt"
git -C "$STABLE_DIR" rev-list --count "$FROM_TAG..$TO_TAG" > "$ARTIFACTS_DIR/upstream-commit-count-$TO_TAG.txt"

info "Three-way merging Linux $TARGET_VERSION without forced subsystem replacement"
python3 "$POLICY_SCRIPT" \
  "$KERNEL_DIR" "$BASE_TREE" "$THEIRS_TREE" "$STATUS_FILE" \
  "$CONFLICT_LIST" "$POLICY_REPORT" "$POLICY_LOG" "$from_sha" "$to_sha"

current_version=$(kernel_version)
test "$current_version" = "$TARGET_VERSION" || fail "Merged tree reports Linux $current_version instead of $TARGET_VERSION"

python3 - "$KERNEL_DIR" "$ARTIFACTS_DIR/remaining-conflict-markers-$TO_TAG.txt" <<'PY'
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
raw = subprocess.check_output(["git", "-C", str(root), "diff", "--name-only", "-z"])
paths = [x.decode("utf-8", "surrogateescape") for x in raw.split(b"\0") if x]
markers = (b"<<<<<<< ", b"||||||| ", b">>>>>>> ")
hits = []
for rel in paths:
    path = root / rel
    if not path.is_file() or path.is_symlink():
        continue
    for line_no, line in enumerate(path.read_bytes().splitlines(), 1):
        if line.startswith(markers):
            hits.append(f"{rel}:{line_no}:{line.decode(errors='replace')}\n")
report.write_text("".join(hits))
if hits:
    raise SystemExit(f"{len(hits)} conflict markers remain after policy resolution")
PY

normalization_report="$ARTIFACTS_DIR/upstream-whitespace-normalization-$TO_TAG.txt"
python3 - "$KERNEL_DIR/drivers/tty/synclink_gt.c" "$normalization_report" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
report = Path(sys.argv[2])
rows = []
if path.exists():
    text = path.read_text()
    replacements = (
        ("\t \tset_gtsignals(info);", "\t\tset_gtsignals(info);", "set_gtsignals"),
        (" \tget_gtsignals(info);", "\tget_gtsignals(info);", "get_gtsignals"),
    )
    for old, new, label in replacements:
        count = text.count(old)
        if count > 1:
            raise SystemExit(f"{label}: expected at most one whitespace defect, found {count}")
        if count == 1:
            text = text.replace(old, new, 1)
            rows.append(f"{label}=normalized\n")
    path.write_text(text)
report.write_text("".join(rows))
PY

git -C "$KERNEL_DIR" diff --check > "$ARTIFACTS_DIR/direct-merge-diff-check-$TO_TAG.txt" 2>&1

conflict_count=$(wc -l < "$CONFLICT_LIST")
policy_ours=$(awk -F '\t' '$2=="ours" {n++} END {print n+0}' "$POLICY_LOG")
policy_theirs=$(awk -F '\t' '$2=="theirs" {n++} END {print n+0}' "$POLICY_LOG")
normalization_count=$(wc -l < "$normalization_report")
{
  printf 'from_tag=%s\n' "$FROM_TAG"
  printf 'from_commit=%s\n' "$from_sha"
  printf 'to_tag=%s\n' "$TO_TAG"
  printf 'to_commit=%s\n' "$to_sha"
  printf 'upstream_commits=%s\n' "$(cat "$ARTIFACTS_DIR/upstream-commit-count-$TO_TAG.txt")"
  printf 'upstream_changed_files=%s\n' "$(wc -l < "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt")"
  printf 'conflict_paths=%s\n' "$conflict_count"
  printf 'policy_vendor_preserved=%s\n' "$policy_ours"
  printf 'policy_upstream_selected=%s\n' "$policy_theirs"
  printf 'forced_link_closure_files=0\n'
  printf 'upstream_whitespace_normalizations=%s\n' "$normalization_count"
  printf 'remaining_conflict_markers=0\n'
  printf 'result=three-way-merge-ready-for-compile-test\n'
  printf 'flashable=no\n'
} | tee "$REPORT"

rm -rf "$STABLE_DIR" "$BASE_TREE" "$THEIRS_TREE"
info "Direct Linux stable merge completed: $FROM_VERSION -> $TARGET_VERSION"
