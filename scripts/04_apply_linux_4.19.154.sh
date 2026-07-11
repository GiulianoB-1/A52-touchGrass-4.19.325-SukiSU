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
RESOLUTION_LOG="$ARTIFACTS_DIR/manual-resolution-$TO_TAG.txt"

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
test "$to_sha" = "f5d8eef067acee3fda37137f4a08c0d3f6427a8e" || fail "Unexpected $TO_TAG commit: $to_sha"

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

mapfile -t actual_rejects < <(find "$KERNEL_DIR" -type f -name '*.rej' -printf '%P\n' | sort)
expected_rejects=(
  "drivers/mailbox/mailbox.c.rej"
  "drivers/scsi/ufs/ufs-qcom.c.rej"
  "drivers/usb/gadget/function/f_ncm.c.rej"
  "fs/f2fs/sysfs.c.rej"
  "kernel/sched/core.c.rej"
)
printf '%s\n' "${actual_rejects[@]}" > "$ARTIFACTS_DIR/actual-rejects-$TO_TAG.txt"
printf '%s\n' "${expected_rejects[@]}" > "$ARTIFACTS_DIR/expected-rejects-$TO_TAG.txt"
diff -u "$ARTIFACTS_DIR/expected-rejects-$TO_TAG.txt" "$ARTIFACTS_DIR/actual-rejects-$TO_TAG.txt" \
  > "$ARTIFACTS_DIR/reject-set-$TO_TAG.diff" || fail "The $TO_TAG reject set changed; manual review is required"

info "Resolving the five reviewed touchGrass/vendor collisions"
python3 - "$KERNEL_DIR" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])

def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1))

# 1. Mailbox: retain touchGrass structure and apply the upstream duplicate-timer guard.
replace_once(
    root / "drivers/mailbox/mailbox.c",
    "\tif (!err && (chan->txdone_method & TXDONE_BY_POLL))\n"
    "\t\t/* kick start the timer immediately to avoid delays */\n"
    "\t\thrtimer_start(&chan->mbox->poll_hrt, 0, HRTIMER_MODE_REL);\n",
    "\t/* kick start the timer immediately to avoid delays */\n"
    "\tif (!err && (chan->txdone_method & TXDONE_BY_POLL)) {\n"
    "\t\t/* but only if not already active */\n"
    "\t\tif (!hrtimer_active(&chan->mbox->poll_hrt))\n"
    "\t\t\thrtimer_start(&chan->mbox->poll_hrt, 0, HRTIMER_MODE_REL);\n"
    "\t}\n",
    "mailbox polling timer guard",
)

# 2. Qualcomm UFS: touchGrass already omits the unsafe runtime-PM/hold pair in this function.
ufs = (root / "drivers/scsi/ufs/ufs-qcom.c").read_text()
start = ufs.index("int ufs_qcom_testbus_config(struct ufs_qcom_host *host)")
end = ufs.index("static void ufs_qcom_testbus_read", start)
segment = ufs[start:end]
for obsolete in (
    "pm_runtime_get_sync(host->hba->dev);",
    "ufshcd_hold(host->hba, false);",
    "ufshcd_release(host->hba);",
    "pm_runtime_put_sync(host->hba->dev);",
):
    if obsolete in segment:
        raise SystemExit(f"UFS testbus still contains obsolete line: {obsolete}")

# 3. USB NCM: touchGrass already supplies SuperSpeed descriptors for SuperSpeedPlus.
ncm = (root / "drivers/usb/gadget/function/f_ncm.c").read_text()
start = ncm.index("static int ncm_bind(")
end = ncm.index("#ifdef CONFIG_USB_ANDROID_SAMSUNG_COMPOSITE", start)
segment = ncm[start:end]
if "ncm_ss_function, ncm_ss_function" not in segment:
    raise SystemExit("NCM SuperSpeedPlus descriptor fix is not present")

# 4. F2FS: wait for the kobject release completion before freeing the superblock state.
replace_once(
    root / "fs/f2fs/sysfs.c",
    "\tkobject_del(&sbi->s_kobj);\n"
    "\tkobject_put(&sbi->s_kobj);\n"
    "}\n",
    "\tkobject_del(&sbi->s_kobj);\n"
    "\tkobject_put(&sbi->s_kobj);\n"
    "\twait_for_completion(&sbi->s_kobj_unregister);\n"
    "}\n",
    "F2FS sysfs unregister completion",
)

# 5. Scheduler: expose the feature mask for all SCHED_DEBUG builds, as in v4.19.154.
replace_once(
    root / "kernel/sched/core.c",
    "#if defined(CONFIG_SCHED_DEBUG) && defined(CONFIG_JUMP_LABEL)\n",
    "#ifdef CONFIG_SCHED_DEBUG\n",
    "scheduler debug feature guard",
)
PY

# The two already-present fixes and three adapted fixes now supersede all rejects.
find "$KERNEL_DIR" -type f -name '*.rej' -delete

current_version=$(kernel_version)
test "$current_version" = "$TARGET_VERSION" || fail "Resolved tree reports Linux $current_version instead of $TARGET_VERSION"

grep -Fq 'if (!hrtimer_active(&chan->mbox->poll_hrt))' "$KERNEL_DIR/drivers/mailbox/mailbox.c" \
  || fail "Mailbox timer guard is missing"
grep -Fq 'wait_for_completion(&sbi->s_kobj_unregister);' "$KERNEL_DIR/fs/f2fs/sysfs.c" \
  || fail "F2FS unregister completion is missing"
grep -Fq '#ifdef CONFIG_SCHED_DEBUG' "$KERNEL_DIR/kernel/sched/core.c" \
  || fail "Scheduler debug guard is missing"
git -C "$KERNEL_DIR" diff --check

{
  printf 'from_tag=%s\n' "$FROM_TAG"
  printf 'from_commit=%s\n' "$from_sha"
  printf 'to_tag=%s\n' "$TO_TAG"
  printf 'to_commit=%s\n' "$to_sha"
  printf 'upstream_commit_count=119\n'
  printf 'changed_files=%s\n' "$(wc -l < "$ARTIFACTS_DIR/upstream-files-$TO_TAG.txt")"
  printf 'patch_bytes=%s\n' "$(wc -c < "$PATCH_FILE")"
  printf 'initial_apply_exit=%s\n' "$apply_rc"
  printf 'validated_reject_count=%s\n' "${#actual_rejects[@]}"
  printf 'reported_kernel_version=%s\n' "$current_version"
  printf 'result=resolved-known-vendor-collisions\n'
} | tee "$REPORT"

{
  printf 'mailbox=applied-hrtimer-active-guard\n'
  printf 'ufs_qcom=upstream-fix-already-present\n'
  printf 'usb_ncm=upstream-fix-already-present\n'
  printf 'f2fs=applied-kobject-release-wait\n'
  printf 'scheduler=applied-sched-debug-guard\n'
} | tee "$RESOLUTION_LOG"

info "Official Linux stable update resolved safely: $FROM_VERSION -> $TARGET_VERSION"
