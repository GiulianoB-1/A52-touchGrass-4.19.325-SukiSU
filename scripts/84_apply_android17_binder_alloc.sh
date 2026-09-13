#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

AOSP_REPO="https://android.googlesource.com/kernel/common"
AOSP_TAG="android17-6.18-2026-09_r1"
AOSP_COMMIT="bab5f6aca819542b9dd13a70d3c62271e81b8e85"
AOSP_DIR="$WORKSPACE/android17-common-binder-alloc"

[[ "$(kernel_version)" == "4.19.206" ]] || fail "Expected Linux 4.19.206 before Binder allocator import"

info "Fetching pinned Android 17 Binder allocator"
rm -rf "$AOSP_DIR"
git init "$AOSP_DIR"
git -C "$AOSP_DIR" remote add origin "$AOSP_REPO"
git -C "$AOSP_DIR" sparse-checkout init --no-cone
cat > "$AOSP_DIR/.git/info/sparse-checkout" <<'EOF'
/drivers/android/binder_alloc.c
/drivers/android/binder_alloc.h
EOF
git -C "$AOSP_DIR" -c protocol.version=2 fetch --depth=1 --filter=blob:none \
  origin "refs/tags/$AOSP_TAG:refs/tags/$AOSP_TAG"
tag_commit="$(git -C "$AOSP_DIR" rev-list -n1 "$AOSP_TAG")"
[[ "$tag_commit" == "$AOSP_COMMIT" ]] || fail "Unexpected Android Binder allocator source commit: $tag_commit"
git -C "$AOSP_DIR" checkout --detach "$tag_commit"

mkdir -p "$ARTIFACTS_DIR/binder84-before"
cp "$KERNEL_DIR/drivers/android/binder_alloc.c" "$ARTIFACTS_DIR/binder84-before/binder_alloc.c"
cp "$KERNEL_DIR/drivers/android/binder_alloc.h" "$ARTIFACTS_DIR/binder84-before/binder_alloc.h"

cp "$AOSP_DIR/drivers/android/binder_alloc.c" "$KERNEL_DIR/drivers/android/binder_alloc.c"
cp "$AOSP_DIR/drivers/android/binder_alloc.h" "$KERNEL_DIR/drivers/android/binder_alloc.h"

python3 "$(dirname "$0")/84_patch_android17_binder_alloc.py" "$KERNEL_DIR"

{
  echo "source_tag=$AOSP_TAG"
  echo "source_commit=$AOSP_COMMIT"
  echo "binder_alloc=android17-native-logic"
  echo "mm_compat=linux-4.19-mmap_sem-find_vma"
  echo "list_lru_compat=linux-4.19-callback-and-add-del-ABI"
  echo "shrinker_compat=linux-4.19-static-register_shrinker"
  echo "page_copy_compat=linux-4.19-kmap-kmap_atomic"
  echo "page_install_serialization=alloc-mutex"
} | tee "$ARTIFACTS_DIR/binder84-allocator-source.txt"

git -C "$KERNEL_DIR" diff --check
info "Android 17 Binder allocator backport prepared"
