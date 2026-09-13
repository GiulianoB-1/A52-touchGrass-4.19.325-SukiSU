#!/usr/bin/env bash
set -Eeuo pipefail

KERNEL="${1:-workspace/touchgrass-a52xq}"
ANDROID_TAG="android17-6.18-2026-09_r1"
ANDROID_COMMIT="bab5f6aca819542b9dd13a70d3c62271e81b8e85"
BASE="https://android.googlesource.com/kernel/common/+/refs/tags/${ANDROID_TAG}/drivers/android"

test -d "$KERNEL/drivers/android" || { echo "missing kernel tree: $KERNEL" >&2; exit 1; }
mkdir -p artifacts/android17-binder-source

files=(
  binder.c
  binder_alloc.c
  binder_alloc.h
  binder_internal.h
  binder_trace.h
  binderfs.c
  dbitmap.h
  binder_netlink.c
  binder_netlink.h
  binder_pick.c
  binder_pick.h
)

echo "android_tag=$ANDROID_TAG" | tee artifacts/android17-binder-source/source.txt
echo "android_commit=$ANDROID_COMMIT" | tee -a artifacts/android17-binder-source/source.txt

for f in "${files[@]}"; do
  url="$BASE/$f?format=TEXT"
  if curl -fsSL "$url" -o "/tmp/$f.b64"; then
    base64 -d "/tmp/$f.b64" > "$KERNEL/drivers/android/$f"
    sha256sum "$KERNEL/drivers/android/$f" | tee -a artifacts/android17-binder-source/sha256.txt
  else
    echo "optional file not present in $ANDROID_TAG: $f" | tee -a artifacts/android17-binder-source/missing.txt
  fi
done

# Keep the vendor 4.19 Makefile/Kconfig for now. The modern files are compiled
# through the existing Binder targets, while extra helper translation units are
# added only when the imported binder.c requires them.
if grep -Fq '"binder_netlink.h"' "$KERNEL/drivers/android/binder.c"; then
  sed -i 's/obj-$(CONFIG_ANDROID_BINDER_IPC)[[:space:]]*+= binder.o binder_alloc.o/obj-$(CONFIG_ANDROID_BINDER_IPC)\t+= binder.o binder_alloc.o binder_netlink.o/' "$KERNEL/drivers/android/Makefile"
fi
if grep -Fq '"binder_pick.h"' "$KERNEL/drivers/android/binder.c"; then
  sed -i 's/obj-$(CONFIG_ANDROID_BINDER_IPC)[[:space:]]*+= binder.o binder_alloc.o binder_netlink.o/obj-$(CONFIG_ANDROID_BINDER_IPC)\t+= binder.o binder_alloc.o binder_netlink.o binder_pick.o/' "$KERNEL/drivers/android/Makefile"
  sed -i 's/obj-$(CONFIG_ANDROID_BINDER_IPC)[[:space:]]*+= binder.o binder_alloc.o$/obj-$(CONFIG_ANDROID_BINDER_IPC)\t+= binder.o binder_alloc.o binder_pick.o/' "$KERNEL/drivers/android/Makefile"
fi

# Compatibility header for selected post-4.19 helpers used by modern Binder.
cat > "$KERNEL/drivers/android/binder_compat_419.h" <<'EOF'
#ifndef _ANDROID_BINDER_COMPAT_419_H
#define _ANDROID_BINDER_COMPAT_419_H

#include <linux/version.h>
#include <linux/mm.h>
#include <linux/highmem.h>

/* 4.19 predates local kmap helpers. Binder only needs page-local mappings. */
#ifndef kmap_local_page
#define kmap_local_page(page) kmap(page)
#define kunmap_local(addr) kunmap(virt_to_page(addr))
#endif

#endif
EOF

# Inject the compatibility header into the C translation units only.
for f in binder.c binder_alloc.c binderfs.c binder_netlink.c binder_pick.c; do
  test -f "$KERNEL/drivers/android/$f" || continue
  if ! grep -Fq '"binder_compat_419.h"' "$KERNEL/drivers/android/$f"; then
    sed -i '1i#include "binder_compat_419.h"' "$KERNEL/drivers/android/$f"
  fi
done

{
  echo "=== imported Android Binder files ==="
  ls -l "$KERNEL/drivers/android"/binder*.c "$KERNEL/drivers/android"/binder*.h "$KERNEL/drivers/android"/dbitmap.h 2>/dev/null || true
  echo
  echo "=== Binder Makefile ==="
  cat "$KERNEL/drivers/android/Makefile"
} | tee artifacts/android17-binder-source/import-audit.txt

git -C "$KERNEL" diff --check
