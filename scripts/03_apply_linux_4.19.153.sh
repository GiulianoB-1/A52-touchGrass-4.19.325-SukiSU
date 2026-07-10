#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

FROM_TAG=v4.19.152
TO_TAG=v4.19.153
TARGET_VERSION=4.19.153
STABLE_DIR="$WORKSPACE/linux-stable"
PATCH_FILE="$ARTIFACTS_DIR/linux-${FROM_TAG#v}-to-${TO_TAG#v}.patch"
APPLY_LOG="$LOG_DIR/apply-${TO_TAG}.log"
RESOLUTION_LOG="$ARTIFACTS_DIR/manual-resolution-${TO_TAG}.txt"

cleanup_report() {
  git -C "$KERNEL_DIR" status --short > "$ARTIFACTS_DIR/source-status-${TO_TAG}.txt" || true
  git -C "$KERNEL_DIR" diff --stat > "$ARTIFACTS_DIR/source-diff-${TO_TAG}.stat.txt" || true
  git -C "$KERNEL_DIR" diff --binary > "$ARTIFACTS_DIR/source-diff-${TO_TAG}.patch" || true
  find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort > "$ARTIFACTS_DIR/reject-files-${TO_TAG}.txt" || true
}
trap cleanup_report EXIT

require_fixed() {
  local path="$1"
  local text="$2"
  grep -Fq -- "$text" "$KERNEL_DIR/$path" || fail "Expected resolved code is missing from $path: $text"
}

require_absent() {
  local path="$1"
  local text="$2"
  if grep -Fq -- "$text" "$KERNEL_DIR/$path"; then
    fail "Obsolete code is still present in $path: $text"
  fi
}

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

info "Applying the stable delta and preserving rejected Android/vendor hunks"
set +e
git -C "$KERNEL_DIR" apply --reject --whitespace=nowarn "$PATCH_FILE" > "$APPLY_LOG" 2>&1
apply_rc=$?
set -e

mapfile -t actual_rejects < <(find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort)
expected_rejects=(
  "Documentation/networking/ip-sysctl.txt.rej"
  "crypto/algif_aead.c.rej"
  "drivers/android/binder.c.rej"
  "fs/proc/base.c.rej"
  "include/linux/oom.h.rej"
  "include/linux/sched/coredump.h.rej"
  "include/net/ip.h.rej"
  "kernel/fork.c.rej"
  "mm/oom_kill.c.rej"
  "net/ipv4/icmp.c.rej"
)

printf '%s\n' "${actual_rejects[@]}" > "$ARTIFACTS_DIR/actual-rejects-${TO_TAG}.txt"
printf '%s\n' "${expected_rejects[@]}" > "$ARTIFACTS_DIR/expected-rejects-${TO_TAG}.txt"
diff -u "$ARTIFACTS_DIR/expected-rejects-${TO_TAG}.txt" "$ARTIFACTS_DIR/actual-rejects-${TO_TAG}.txt" \
  > "$ARTIFACTS_DIR/reject-set-${TO_TAG}.diff" || fail "The reject set changed; manual review is required"

grep -Fq "error: drivers/slimbus/qcom-ngd-ctrl.c: No such file or directory" "$APPLY_LOG" \
  || fail "Expected absent Qualcomm Slimbus file was not reported"
test ! -e "$KERNEL_DIR/drivers/slimbus/qcom-ngd-ctrl.c" \
  || fail "Qualcomm Slimbus file unexpectedly exists and must be reviewed"

info "Verifying touchGrass already contains the rejected upstream fixes"
require_fixed "Documentation/networking/ip-sysctl.txt" "controlled by this limit. For security reasons, the precise count"
require_fixed "Documentation/networking/ip-sysctl.txt" "For security reasons, the precise burst size is randomized."

require_fixed "drivers/android/binder.c" "enum binder_work_type {"
require_fixed "drivers/android/binder.c" "wtype = w ? w->type : 0;"
require_fixed "drivers/android/binder.c" "case BINDER_WORK_NODE:"
require_absent "drivers/android/binder.c" "static struct binder_work *binder_dequeue_work_head("

require_absent "fs/proc/base.c" "static DEFINE_MUTEX(oom_adj_mutex);"
require_fixed "fs/proc/base.c" "if (test_bit(MMF_MULTIPROCESS, &p->mm->flags)) {"
require_fixed "include/linux/oom.h" "extern struct mutex oom_adj_mutex;"
require_fixed "include/linux/sched/coredump.h" "#define MMF_MULTIPROCESS"
require_fixed "kernel/fork.c" "static void copy_oom_score_adj(u64 clone_flags, struct task_struct *tsk)"
require_fixed "kernel/fork.c" "copy_oom_score_adj(clone_flags, p);"
require_fixed "mm/oom_kill.c" "DEFINE_MUTEX(oom_adj_mutex);"

require_fixed "include/net/ip.h" "mtu = dst_metric_raw(dst, RTAX_MTU);"
require_fixed "net/ipv4/icmp.c" "credit = max_t(int, credit - prandom_u32_max(3), 0);"

info "Adapting the crypto fix to touchGrass's newer sync-skcipher API"
python3 - "$KERNEL_DIR/crypto/algif_aead.c" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
old = "skcipher_request_set_callback(skreq, CRYPTO_TFM_REQ_MAY_BACKLOG,"
new = "skcipher_request_set_callback(skreq, CRYPTO_TFM_REQ_MAY_SLEEP,"
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected exactly one sync-skcipher callback to update, found {count}")
path.write_text(text.replace(old, new, 1))
PY

require_fixed "crypto/algif_aead.c" "skcipher_request_set_callback(skreq, CRYPTO_TFM_REQ_MAY_SLEEP,"
require_fixed "crypto/algif_aead.c" "aead_request_set_callback(&areq->cra_u.aead_req,"
require_fixed "crypto/algif_aead.c" "CRYPTO_TFM_REQ_MAY_SLEEP |"
require_absent "crypto/algif_aead.c" "if (err == -EINPROGRESS || err == -EBUSY)"

info "Installing pinned upstream ARM64 defconfig for generic QEMU builds"
install -D -m 0644 \
  "$STABLE_DIR/arch/arm64/configs/defconfig" \
  "$KERNEL_DIR/arch/arm64/configs/defconfig"
cmp -s \
  "$STABLE_DIR/arch/arm64/configs/defconfig" \
  "$KERNEL_DIR/arch/arm64/configs/defconfig" \
  || fail "Installed ARM64 QEMU defconfig does not match Linux $TO_TAG"

find "$KERNEL_DIR" -type f -name '*.rej' -delete
actual_version=$(kernel_version)
test "$actual_version" = "$TARGET_VERSION" || fail "Resolved tree reports Linux $actual_version instead of $TARGET_VERSION"

{
  printf 'initial_git_apply_exit=%s\n' "$apply_rc"
  printf 'resolution=accepted-known-preapplied-hunks\n'
  printf 'manual_crypto_adaptation=CRYPTO_TFM_REQ_MAY_SLEEP\n'
  printf 'skipped_absent_driver=drivers/slimbus/qcom-ngd-ctrl.c\n'
  printf 'qemu_arm64_defconfig_commit=%s\n' "$to_sha"
  printf 'validated_reject_count=%s\n' "${#actual_rejects[@]}"
  printf 'kernel_version=%s\n' "$actual_version"
} | tee "$RESOLUTION_LOG"

printf 'check_exit=resolved\napply_exit=0\nkernel_version=%s\n' "$actual_version" > "$ARTIFACTS_DIR/apply-result-${TO_TAG}.txt"
info "Official stable update resolved safely: $TOUCHGRASS_BASE_VERSION -> $TARGET_VERSION"
