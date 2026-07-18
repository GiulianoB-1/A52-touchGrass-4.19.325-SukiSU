#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.206
ANDROID_COMMON_REPO=https://android.googlesource.com/kernel/common
ANDROID_COMMON_REF=refs/heads/android-4.19-stable
TORVALDS_REPO=https://github.com/torvalds/linux.git
WORK="$WORKSPACE/bpf-ringbuf-backport"
REPORT="$ARTIFACTS_DIR/bpf-ringbuf-backport.txt"
PATCH_DIR="$ARTIFACTS_DIR/bpf-ringbuf-patches"

[[ "$(kernel_version)" == "$TARGET_VERSION" ]] || fail "Expected Linux $TARGET_VERSION before BPF ring-buffer backport"
mkdir -p "$WORK" "$PATCH_DIR"
rm -rf "$WORK"/*

# The generated stable/vendor tree is intentionally a large uncommitted delta
# from the original touchGrass commit. Record it as a local baseline so git's
# three-way machinery can safely report real conflicts in later feature patches.
git -C "$KERNEL_DIR" config user.name 'touchGrass CI'
git -C "$KERNEL_DIR" config user.email 'touchgrass-ci@users.noreply.github.com'
git -C "$KERNEL_DIR" add -A
git -C "$KERNEL_DIR" commit -m 'Generated Linux 4.19.206 vendor baseline' >/dev/null
BASELINE_COMMIT=$(git -C "$KERNEL_DIR" rev-parse HEAD)

info "Finding the BPF ring-buffer introduction on Android's 4.19 branch"
INTRO_META="$WORK/android-4.19-ringbuf-introduction.json"
python3 - "$INTRO_META" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

out = sys.argv[1]
base = (
    "https://android.googlesource.com/kernel/common/+log/"
    "refs/heads/android-4.19-stable/kernel/bpf/ringbuf.c"
)
entries = []
token = None
seen = set()
for _ in range(100):
    query = {"format": "JSON", "n": "100"}
    if token:
        query["s"] = token
    url = base + "?" + urllib.parse.urlencode(query)
    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read().decode("utf-8")
    if raw.startswith(")]}'"):
        raw = raw.split("\n", 1)[1]
    payload = json.loads(raw)
    for row in payload.get("log", []):
        commit = row.get("commit")
        if commit and commit not in seen:
            entries.append(row)
            seen.add(commit)
    token = payload.get("next")
    if not token:
        break
else:
    raise SystemExit("Gitiles path history pagination did not terminate")

if not entries:
    raise SystemExit("No Android 4.19 ringbuf history was returned")
intro = entries[-1]
subject = (intro.get("message") or "").splitlines()[0]
result = {
    "commit": intro["commit"],
    "subject": subject,
    "history_entries": len(entries),
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(result, handle, indent=2)
print(json.dumps(result))
PY

INTRO_COMMIT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$INTRO_META")
INTRO_SUBJECT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["subject"])' "$INTRO_META")
cp "$INTRO_META" "$ARTIFACTS_DIR/android-4.19-ringbuf-introduction.json"

feature_paths=(
  include/linux/bpf.h
  include/linux/bpf_types.h
  include/linux/bpf_verifier.h
  include/uapi/linux/bpf.h
  kernel/bpf/Makefile
  kernel/bpf/helpers.c
  kernel/bpf/ringbuf.c
  kernel/bpf/syscall.c
  kernel/bpf/verifier.c
  kernel/trace/bpf_trace.c
)

fetch_commit_patch() {
  local repo="$1"
  local sha="$2"
  local name="$3"
  shift 3
  local donor="$WORK/donor-$name"
  local patch="$PATCH_DIR/$name.patch"
  rm -rf "$donor"
  git init -q "$donor"
  git -C "$donor" remote add origin "$repo"
  git -C "$donor" -c protocol.version=2 fetch --quiet --filter=blob:none --depth=2 origin "$sha"
  local resolved parent
  resolved=$(git -C "$donor" rev-parse FETCH_HEAD)
  parent=$(git -C "$donor" rev-parse "${resolved}^")
  git -C "$donor" diff --binary --full-index "$parent" "$resolved" -- "$@" > "$patch"
  test -s "$patch" || fail "$name produced an empty filtered patch"
  printf '%s\n' "$patch"
}

apply_patch_safely() {
  local patch="$1"
  local name="$2"
  if git -C "$KERNEL_DIR" apply --check "$patch"; then
    git -C "$KERNEL_DIR" apply --index "$patch"
    printf '%s=applied\n' "$name" >> "$REPORT"
  elif git -C "$KERNEL_DIR" apply --reverse --check "$patch"; then
    printf '%s=already-present\n' "$name" >> "$REPORT"
  else
    set +e
    git -C "$KERNEL_DIR" apply --3way --index "$patch" \
      > "$ARTIFACTS_DIR/${name}-threeway.log" 2>&1
    rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
      git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/${name}-conflicts.txt" || true
      git -C "$KERNEL_DIR" diff --name-only --diff-filter=U \
        > "$ARTIFACTS_DIR/${name}-unmerged-paths.txt" || true
      fail "$name does not apply cleanly to the Linux $TARGET_VERSION vendor tree"
    fi
    printf '%s=applied-threeway\n' "$name" >> "$REPORT"
  fi
  git -C "$KERNEL_DIR" add -A
  git -C "$KERNEL_DIR" commit -m "Backport $name" >/dev/null || true
}

: > "$REPORT"
{
  echo "target_kernel=$TARGET_VERSION"
  echo "baseline_commit=$BASELINE_COMMIT"
  echo "android_common_ref=$ANDROID_COMMON_REF"
  echo "android_ringbuf_intro_commit=$INTRO_COMMIT"
  echo "android_ringbuf_intro_subject=$INTRO_SUBJECT"
} >> "$REPORT"

intro_patch=$(fetch_commit_patch "$ANDROID_COMMON_REPO" "$INTRO_COMMIT" android-4.19-ringbuf-introduction "${feature_paths[@]}")
apply_patch_safely "$intro_patch" android-4.19-ringbuf-introduction

# Apply the small correctness/security follow-ups that are required for a safe
# implementation. Each is accepted as already present when Android's 4.19
# introduction commit incorporated it during backporting.
apply_upstream_fix() {
  local sha="$1"
  local name="$2"
  shift 2
  local patch
  patch=$(fetch_commit_patch "$TORVALDS_REPO" "$sha" "$name" "$@")
  apply_patch_safely "$patch" "$name"
}

apply_upstream_fix 517bbe1994a3cee29a35c730662277bb5daff582 ringbuf-power-of-two \
  kernel/bpf/ringbuf.c
apply_upstream_fix 744ea4e3885eccb6d332a06fae9eb7420a622c0f ringbuf-ptr-to-mem-spilling \
  include/linux/bpf_verifier.h kernel/bpf/verifier.c
apply_upstream_fix 4b81ccebaeee885ab1aa1438133f2991e3a2b6ea ringbuf-reservation-size-bound \
  kernel/bpf/ringbuf.c
apply_upstream_fix 5b029a32cfe4600f5e10e36b41778506b90fd4de ringbuf-helper-map-compatibility \
  kernel/bpf/verifier.c
apply_upstream_fix 35ab8c9085b0af847df7fac9571ccd26d9f0f513 ringbuf-null-pointer-arithmetic \
  kernel/bpf/verifier.c

info "Auditing the complete BPF ring-buffer ABI"
UAPI="$KERNEL_DIR/include/uapi/linux/bpf.h"
BPF_H="$KERNEL_DIR/include/linux/bpf.h"
BPF_TYPES="$KERNEL_DIR/include/linux/bpf_types.h"
BPF_VERIFIER_H="$KERNEL_DIR/include/linux/bpf_verifier.h"
RINGBUF="$KERNEL_DIR/kernel/bpf/ringbuf.c"
VERIFIER="$KERNEL_DIR/kernel/bpf/verifier.c"
MAKEFILE="$KERNEL_DIR/kernel/bpf/Makefile"

required_patterns=(
  "$UAPI:BPF_MAP_TYPE_RINGBUF"
  "$UAPI:FN(ringbuf_output)"
  "$UAPI:FN(ringbuf_reserve)"
  "$UAPI:FN(ringbuf_submit)"
  "$UAPI:FN(ringbuf_discard)"
  "$UAPI:FN(ringbuf_query)"
  "$BPF_H:bpf_ringbuf_output_proto"
  "$BPF_H:bpf_ringbuf_reserve_proto"
  "$BPF_H:bpf_ringbuf_submit_proto"
  "$BPF_H:bpf_ringbuf_discard_proto"
  "$BPF_H:bpf_ringbuf_query_proto"
  "$BPF_TYPES:BPF_MAP_TYPE_RINGBUF"
  "$BPF_VERIFIER_H:mem_size"
  "$RINGBUF:const struct bpf_map_ops ringbuf_map_ops"
  "$RINGBUF:len > rb->mask + 1"
  "$VERIFIER:PTR_TO_MEM"
  "$VERIFIER:BPF_FUNC_ringbuf_output"
  "$MAKEFILE:ringbuf.o"
)
for item in "${required_patterns[@]}"; do
  file=${item%%:*}
  pattern=${item#*:}
  grep -Fq "$pattern" "$file" || fail "Missing ring-buffer invariant in ${file#$KERNEL_DIR/}: $pattern"
done

git -C "$KERNEL_DIR" diff --check HEAD^..HEAD || true
git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/bpf-ringbuf-source-status.txt"
git -C "$KERNEL_DIR" log --oneline --decorate -8 > "$ARTIFACTS_DIR/bpf-ringbuf-local-commits.txt"
sha256sum "$PATCH_DIR"/*.patch > "$ARTIFACTS_DIR/bpf-ringbuf-patches.sha256"
{
  echo 'abi_audit=passed'
  echo 'map_type=BPF_MAP_TYPE_RINGBUF'
  echo 'helpers=output,reserve,submit,discard,query'
  echo 'verifier_dynamic_memory_tracking=present'
  echo 'security_followups=power-of-two,reservation-size,helper-map-compatibility,null-pointer-arithmetic'
  echo 'hardware_validated=no'
} >> "$REPORT"
cat "$REPORT"
