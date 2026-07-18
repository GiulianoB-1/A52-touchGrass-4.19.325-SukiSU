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

resolved=$(git ls-remote "$ANDROID_COMMON_REPO" "$ARCHIVED_REF" | awk 'NR==1 {print $1}')
test "$resolved" = "$ARCHIVED_HEAD" || {
  echo "ERROR: archived Android 4.19 head changed: ${resolved:-missing}" >&2
  exit 1
}

curl --fail --location --retry 3 --silent --show-error \
  "$RAW_URL" --output "$GENERATED"

python3 - "$GENERATED" "$RINGBUF_INTRO" "$ARCHIVED_HEAD" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
intro = sys.argv[2]
archived_head = sys.argv[3]
text = path.read_text()

# Replace unreliable Gitiles history discovery with the verified immutable
# introduction commit from Android common's archived 4.19 history.
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

# git apply --3way needs the exact preimage blobs named in each full-index
# patch. Write those blobs directly into the target object database instead of
# fetching unrelated history into the shallow vendor repository.
function_anchor = '''}

apply_patch_safely() {
'''
if text.count(function_anchor) != 1:
    raise SystemExit('fetch/apply function boundary mismatch')
blob_helper = r'''}

inject_donor_blobs() {
  local donor="$1"
  shift
  local resolved parent tree rel expected actual count=0
  resolved=$(git -C "$donor" rev-parse FETCH_HEAD)
  parent=$(git -C "$donor" rev-parse "${resolved}^")
  for tree in "$parent" "$resolved"; do
    for rel in "$@"; do
      if ! git -C "$donor" cat-file -e "$tree:$rel" 2>/dev/null; then
        continue
      fi
      expected=$(git -C "$donor" rev-parse "$tree:$rel")
      actual=$(git -C "$donor" show "$tree:$rel" | git -C "$KERNEL_DIR" hash-object -w --stdin)
      [[ "$actual" == "$expected" ]] || fail "Donor blob hash mismatch for $tree:$rel"
      count=$((count + 1))
    done
  done
  printf 'donor=%s\nresolved=%s\nparent=%s\nblobs_written=%s\n' \
    "$donor" "$resolved" "$parent" "$count" \
    > "$ARTIFACTS_DIR/$(basename "$donor")-blobs.txt"
}

apply_patch_safely() {
'''
text = text.replace(function_anchor, blob_helper, 1)

# Preserve complete stage data for any genuine source conflict.
old_failure = r'''    if [[ $rc -ne 0 ]]; then
      git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/${name}-conflicts.txt" || true
      git -C "$KERNEL_DIR" diff --name-only --diff-filter=U \
        > "$ARTIFACTS_DIR/${name}-unmerged-paths.txt" || true
      fail "$name does not apply cleanly to the Linux $TARGET_VERSION vendor tree"
    fi
'''
new_failure = r'''    if [[ $rc -ne 0 ]]; then
      local unmerged="$ARTIFACTS_DIR/${name}-unmerged-paths.txt"
      local stage_dir="$ARTIFACTS_DIR/${name}-merge-stages"
      git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/${name}-conflicts.txt" || true
      git -C "$KERNEL_DIR" diff --name-only --diff-filter=U > "$unmerged" || true
      mkdir -p "$stage_dir"
      while IFS= read -r rel; do
        [[ -n "$rel" ]] || continue
        safe=${rel//\//__}
        cp "$KERNEL_DIR/$rel" "$stage_dir/${safe}.worktree" 2>/dev/null || true
        git -C "$KERNEL_DIR" show ":1:$rel" > "$stage_dir/${safe}.base" 2>/dev/null || true
        git -C "$KERNEL_DIR" show ":2:$rel" > "$stage_dir/${safe}.vendor" 2>/dev/null || true
        git -C "$KERNEL_DIR" show ":3:$rel" > "$stage_dir/${safe}.donor" 2>/dev/null || true
      done < "$unmerged"
      tar -C "$stage_dir" -czf "$ARTIFACTS_DIR/${name}-merge-stages.tar.gz" . 2>/dev/null || true

      if [[ "$name" == android-4.19-ringbuf-introduction ]]; then
        info "Resolving Android ring-buffer conflicts against Samsung BPF layout"
        python3 "$PROJECT_DIR/scripts/58_resolve_ringbuf_vendor_conflicts.py" "$KERNEL_DIR" \
          | tee "$ARTIFACTS_DIR/${name}-vendor-resolution.log"
        git -C "$KERNEL_DIR" add -A
        git -C "$KERNEL_DIR" diff --name-only --diff-filter=U > "$unmerged"
        [[ ! -s "$unmerged" ]] || \
          fail "$name vendor resolver left unmerged paths"
      else
        fail "$name does not apply cleanly to the Linux $TARGET_VERSION vendor tree"
      fi
    fi
'''
if text.count(old_failure) != 1:
    raise SystemExit('three-way failure block mismatch')
text = text.replace(old_failure, new_failure, 1)

# The touchGrass source snapshot carries executable bits on many source files.
# Normalize only the feature paths before applying upstream patches.
intro_anchor = '''intro_patch=$(fetch_commit_patch "$ANDROID_COMMON_REPO" "$INTRO_COMMIT" android-4.19-ringbuf-introduction "${feature_paths[@]}")
apply_patch_safely "$intro_patch" android-4.19-ringbuf-introduction
'''
if text.count(intro_anchor) != 1:
    raise SystemExit('introduction apply anchor mismatch')
intro_replacement = r'''normalized_mode_paths=()
for rel in "${feature_paths[@]}"; do
  if [[ -f "$KERNEL_DIR/$rel" ]]; then
    chmod 0644 "$KERNEL_DIR/$rel"
    normalized_mode_paths+=("$rel")
  fi
done
if (( ${#normalized_mode_paths[@]} )); then
  git -C "$KERNEL_DIR" add --chmod=-x -- "${normalized_mode_paths[@]}"
  git -C "$KERNEL_DIR" commit -m 'Normalize BPF source modes before ringbuf merge' >/dev/null || true
fi
printf 'normalized_source_modes=%s\n' "${#normalized_mode_paths[@]}" \
  > "$ARTIFACTS_DIR/bpf-ringbuf-mode-normalization.txt"

intro_patch=$(fetch_commit_patch "$ANDROID_COMMON_REPO" "$INTRO_COMMIT" android-4.19-ringbuf-introduction "${feature_paths[@]}")
inject_donor_blobs "$WORK/donor-android-4.19-ringbuf-introduction" "${feature_paths[@]}"
apply_patch_safely "$intro_patch" android-4.19-ringbuf-introduction
python3 "$PROJECT_DIR/scripts/58_apply_vendor_ringbuf_followups.py" "$KERNEL_DIR" \
  | tee "$ARTIFACTS_DIR/bpf-ringbuf-vendor-followups.log"
git -C "$KERNEL_DIR" add -A
git -C "$KERNEL_DIR" commit -m 'Integrate vendor-safe ringbuf follow-up fixes' >/dev/null || true
'''
text = text.replace(intro_anchor, intro_replacement, 1)

# Hydrate preimage blobs for every correctness/security follow-up as well.
fix_anchor = '''  patch=$(fetch_commit_patch "$TORVALDS_REPO" "$sha" "$name" "$@")
  apply_patch_safely "$patch" "$name"
'''
fix_replacement = '''  case "$name" in
    ringbuf-power-of-two)
      marker='!is_power_of_2(attr->max_entries)'
      file="$KERNEL_DIR/kernel/bpf/ringbuf.c"
      ;;
    ringbuf-ptr-to-mem-spilling)
      marker='ringbuf PTR_TO_MEM spill support'
      file="$KERNEL_DIR/kernel/bpf/verifier.c"
      ;;
    ringbuf-reservation-size-bound)
      marker='reject reservation larger than ringbuf'
      file="$KERNEL_DIR/kernel/bpf/ringbuf.c"
      ;;
    ringbuf-helper-map-compatibility)
      marker='ringbuf helper-to-map compatibility'
      file="$KERNEL_DIR/kernel/bpf/verifier.c"
      ;;
    ringbuf-null-pointer-arithmetic)
      marker='reject ringbuf nullable pointer arithmetic'
      file="$KERNEL_DIR/kernel/bpf/verifier.c"
      ;;
    *)
      marker=
      file=
      ;;
  esac
  if [[ -n "$marker" ]] && grep -Fq "$marker" "$file"; then
    printf '%s=already-present-vendor\n' "$name" >> "$REPORT"
    return
  fi
  patch=$(fetch_commit_patch "$TORVALDS_REPO" "$sha" "$name" "$@")
  inject_donor_blobs "$WORK/donor-$name" "$@"
  apply_patch_safely "$patch" "$name"
'''
if text.count(fix_anchor) != 1:
    raise SystemExit('follow-up apply anchor mismatch')
text = text.replace(fix_anchor, fix_replacement, 1)

path.write_text(text)
PY
chmod +x "$GENERATED"

mkdir -p "$PROJECT_DIR/artifacts"
{
  echo "archived_ref=$ARCHIVED_REF"
  echo "archived_head=$ARCHIVED_HEAD"
  echo "ringbuf_intro=$RINGBUF_INTRO"
  echo 'donor_blob_strategy=hash-object-no-history-mutation'
} > "$PROJECT_DIR/artifacts/android-4.19-archived-ref.txt"
set -o pipefail
"$GENERATED" 2>&1 | tee "$PROJECT_DIR/artifacts/bpf-ringbuf-backport-step.log"
