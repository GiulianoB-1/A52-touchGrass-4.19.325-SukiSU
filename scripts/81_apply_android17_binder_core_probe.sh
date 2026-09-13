#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

AOSP_REPO="https://android.googlesource.com/kernel/common"
AOSP_TAG="android17-6.18-2026-09_r1"
AOSP_COMMIT="bab5f6aca819542b9dd13a70d3c62271e81b8e85"
AOSP_DIR="$WORKSPACE/android17-common-binder"

[[ "$(kernel_version)" == "4.19.206" ]] || fail "Expected Linux 4.19.206 before Binder import"

info "Fetching pinned Android 17 Binder source"
test ! -e "$AOSP_DIR" || fail "Android Binder source staging directory already exists"
git init "$AOSP_DIR"
git -C "$AOSP_DIR" remote add origin "$AOSP_REPO"
git -C "$AOSP_DIR" sparse-checkout init --no-cone
cat > "$AOSP_DIR/.git/info/sparse-checkout" <<'EOF'
/drivers/android/binder.c
/drivers/android/binder_internal.h
/drivers/android/binder_trace.h
/drivers/android/dbitmap.h
/drivers/android/binder_pick.c
/drivers/android/binder_pick.h
/include/uapi/linux/android/binder.h
/include/uapi/linux/android/binder_netlink.h
EOF
git -C "$AOSP_DIR" -c protocol.version=2 fetch --depth=1 --filter=blob:none \
  origin "refs/tags/$AOSP_TAG:refs/tags/$AOSP_TAG"
tag_commit="$(git -C "$AOSP_DIR" rev-list -n1 "$AOSP_TAG")"
[[ "$tag_commit" == "$AOSP_COMMIT" ]] || fail "Unexpected Android Binder source commit: $tag_commit"
git -C "$AOSP_DIR" checkout --detach "$tag_commit"

mkdir -p "$ARTIFACTS_DIR/binder81-before"
for f in \
  drivers/android/binder.c \
  drivers/android/binder_internal.h \
  drivers/android/binder_trace.h \
  drivers/android/binder_alloc.c \
  drivers/android/binder_alloc.h \
  drivers/android/binderfs.c \
  include/uapi/linux/android/binder.h; do
  cp "$KERNEL_DIR/$f" "$ARTIFACTS_DIR/binder81-before/$(basename "$f")"
done

info "Importing Android 17 Binder core, retaining 4.19 allocator and binderfs"
for f in \
  drivers/android/binder.c \
  drivers/android/binder_internal.h \
  drivers/android/binder_trace.h \
  drivers/android/dbitmap.h \
  include/uapi/linux/android/binder.h; do
  cp "$AOSP_DIR/$f" "$KERNEL_DIR/$f"
done

for f in drivers/android/binder_pick.c drivers/android/binder_pick.h; do
  if [[ -f "$AOSP_DIR/$f" ]]; then
    cp "$AOSP_DIR/$f" "$KERNEL_DIR/$f"
  fi
done

python3 "$(dirname "$0")/81_patch_android17_binder_core.py" "$KERNEL_DIR"

{
  echo "source_tag=$AOSP_TAG"
  echo "source_commit=$AOSP_COMMIT"
  echo "scope=android17-binder-core-compile-probe"
  echo "allocator=touchgrass-4.19-retained-with-ABI-shims"
  echo "binderfs=temporarily-disabled-for-core-isolation"
  echo "android_netlink_reporting=temporarily-disabled"
  echo "samsung_freecess=restore-before-hardware-package"
} | tee "$ARTIFACTS_DIR/binder81-source.txt"

git -C "$KERNEL_DIR" diff --check
info "Android 17 Binder core probe import prepared"
