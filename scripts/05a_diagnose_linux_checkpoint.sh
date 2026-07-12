#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

FROM_VERSION=${1:?usage: $0 FROM_VERSION TARGET_VERSION}
TARGET_VERSION=${2:?usage: $0 FROM_VERSION TARGET_VERSION}
FROM_TAG="v$FROM_VERSION"
TO_TAG="v$TARGET_VERSION"
STABLE_DIR="$WORKSPACE/linux-stable-checkpoint-$TARGET_VERSION"
PATCH_FILE="$ARTIFACTS_DIR/linux-$FROM_VERSION-to-$TARGET_VERSION.patch"
APPLY_LOG="$LOG_DIR/apply-$FROM_TAG-to-$TO_TAG.log"
REPORT="$ARTIFACTS_DIR/checkpoint-$FROM_VERSION-to-$TARGET_VERSION.txt"
REJECT_LIST="$ARTIFACTS_DIR/reject-files-$TO_TAG.txt"
REJECT_ARCHIVE="$ARTIFACTS_DIR/reject-context-$TO_TAG.tar.gz"
OVERLAP_LIST="$ARTIFACTS_DIR/vendor-overlap-files-$TO_TAG.txt"
OVERLAP_ARCHIVE="$ARTIFACTS_DIR/vendor-overlap-context-$TO_TAG.tar.gz"

mkdir -p "$ARTIFACTS_DIR" "$LOG_DIR"
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
git -C "$STABLE_DIR" fetch --quiet --depth=1000 origin "refs/tags/$TO_TAG:refs/tags/$TO_TAG"
git -C "$STABLE_DIR" fetch --quiet --depth=1 origin "refs/tags/$FROM_TAG:refs/tags/$FROM_TAG"

from_sha=$(git -C "$STABLE_DIR" rev-parse "$FROM_TAG^{commit}")
to_sha=$(git -C "$STABLE_DIR" rev-parse "$TO_TAG^{commit}")
stable_version=$(git -C "$STABLE_DIR" show "$TO_TAG:Makefile" | awk '
  $1=="VERSION" {v=$3}
  $1=="PATCHLEVEL" {p=$3}
  $1=="SUBLEVEL" {s=$3}
  END {printf "%s.%s.%s", v, p, s}
')
test "$stable_version" = "$TARGET_VERSION" || fail "$TO_TAG reports Linux $stable_version"

info "Generating official stable delta $FROM_TAG -> $TO_TAG"
git -C "$STABLE_DIR" diff --binary --full-index --no-renames "$FROM_TAG" "$TO_TAG" > "$PATCH_FILE"
test -s "$PATCH_FILE" || fail "Generated stable patch is empty"
git -C "$STABLE_DIR" diff --name-only "$FROM_TAG" "$TO_TAG" | sort > "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt"
git -C "$STABLE_DIR" diff --stat "$FROM_TAG" "$TO_TAG" > "$ARTIFACTS_DIR/upstream-diff-$TO_TAG.stat.txt"
git -C "$STABLE_DIR" rev-list --count "$FROM_TAG..$TO_TAG" > "$ARTIFACTS_DIR/upstream-commit-count-$TO_TAG.txt"
sha256sum "$PATCH_FILE" > "$PATCH_FILE.sha256"

info "Auditing every upstream-changed path already modified by touchGrass/vendor"
python3 - "$KERNEL_DIR" "$STABLE_DIR" "$FROM_TAG" "$TO_TAG" \
  "$OVERLAP_LIST" "$OVERLAP_ARCHIVE" <<'PY'
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tarfile

kernel = Path(sys.argv[1])
stable = Path(sys.argv[2])
from_tag = sys.argv[3]
to_tag = sys.argv[4]
overlap_list = Path(sys.argv[5])
overlap_archive = Path(sys.argv[6])
stage = overlap_archive.parent / (overlap_archive.stem + "-stage")
if stage.exists():
    shutil.rmtree(stage)
stage.mkdir(parents=True)

def run(*args, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout

def tree_entry(tag, rel):
    raw = run("git", "-C", str(stable), "ls-tree", "-z", tag, "--", rel)
    if not raw:
        return ("missing", b"")
    meta, name = raw.split(b"\t", 1)
    mode, kind, oid = meta.decode().split()
    if kind != "blob":
        return (f"{kind}:{mode}", oid.encode())
    data = run("git", "-C", str(stable), "cat-file", "blob", oid)
    if mode == "120000":
        return ("symlink", data)
    return (f"file:{mode}", data)

def work_entry(rel):
    path = kernel / rel
    if path.is_symlink():
        return ("symlink", os.readlink(path).encode())
    if path.is_file():
        mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        return (f"file:{mode}", path.read_bytes())
    if path.exists():
        return ("other", b"")
    return ("missing", b"")

def save(side, rel, entry):
    kind, data = entry
    dst = stage / side / rel
    if kind == "missing":
        marker = Path(str(dst) + ".missing")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("missing\n")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    Path(str(dst) + ".type").write_text(kind + "\n")

changed = run("git", "-C", str(stable), "diff", "--name-only", "-z",
              "--no-renames", from_tag, to_tag)
paths = [p.decode("utf-8", "surrogateescape") for p in changed.split(b"\0") if p]
overlaps = []
for rel in paths:
    base = tree_entry(from_tag, rel)
    vendor = work_entry(rel)
    if vendor != base:
        overlaps.append(rel)
        save("base", rel, base)
        save("vendor", rel, vendor)
        save("target", rel, tree_entry(to_tag, rel))

overlap_list.write_text("".join(f"{p}\n" for p in overlaps))
(stage / "README.txt").write_text(
    "base/ = official starting tag; vendor/ = touchGrass checkpoint before update; "
    "target/ = official target tag. Every listed path requires semantic review.\n"
)
with tarfile.open(overlap_archive, "w:gz") as tf:
    for child in sorted(stage.iterdir()):
        tf.add(child, arcname=child.name)
shutil.rmtree(stage)
PY
overlap_count=$(wc -l < "$OVERLAP_LIST")

info "Applying checkpoint with reject preservation and no automatic conflict decisions"
set +e
git -C "$KERNEL_DIR" apply --reject --whitespace=nowarn "$PATCH_FILE" > "$APPLY_LOG" 2>&1
apply_rc=$?
set -e

find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$REJECT_LIST"
reject_count=$(wc -l < "$REJECT_LIST")
reported_version=$(kernel_version)

git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/source-status-$TO_TAG.txt" || true
git -C "$KERNEL_DIR" diff --stat > "$ARTIFACTS_DIR/source-diff-$TO_TAG.stat.txt" || true
git -C "$KERNEL_DIR" diff --check > "$ARTIFACTS_DIR/source-diff-check-$TO_TAG.txt" 2>&1 || true

if test "$reject_count" -gt 0; then
  context_list="$ARTIFACTS_DIR/reject-context-$TO_TAG.list"
  : > "$context_list"
  while IFS= read -r reject; do
    printf '%s\n' "$reject" >> "$context_list"
    printf '%s\n' "${reject%.rej}" >> "$context_list"
  done < "$REJECT_LIST"
  sort -u -o "$context_list" "$context_list"
  tar --ignore-failed-read -C "$KERNEL_DIR" -czf "$REJECT_ARCHIVE" -T "$context_list"
fi

{
  printf 'from_tag=%s\n' "$FROM_TAG"
  printf 'from_commit=%s\n' "$from_sha"
  printf 'to_tag=%s\n' "$TO_TAG"
  printf 'to_commit=%s\n' "$to_sha"
  printf 'upstream_commit_count=%s\n' "$(cat "$ARTIFACTS_DIR/upstream-commit-count-$TO_TAG.txt")"
  printf 'changed_files=%s\n' "$(wc -l < "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt")"
  printf 'vendor_overlap_count=%s\n' "$overlap_count"
  printf 'patch_bytes=%s\n' "$(wc -c < "$PATCH_FILE")"
  printf 'apply_exit=%s\n' "$apply_rc"
  printf 'reject_count=%s\n' "$reject_count"
  printf 'reported_kernel_version=%s\n' "$reported_version"
  if test "$reject_count" -eq 0 && test "$reported_version" = "$TARGET_VERSION"; then
    printf 'result=clean-checkpoint-apply-pending-overlap-audit\n'
  else
    printf 'result=review-required\n'
  fi
  printf 'flashable=no\n'
} | tee "$REPORT"

rm -rf "$STABLE_DIR"
info "Checkpoint diagnostics complete: $FROM_VERSION -> $TARGET_VERSION"
