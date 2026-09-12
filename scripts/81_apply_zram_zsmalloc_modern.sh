#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

[[ "$(kernel_version)" == "4.19.206" ]] || fail "Expected Linux 4.19.206 before Phase81 ZRAM/zsmalloc port"

REF_REMOTE=zram-modern-419-ref
REF_REPO=https://github.com/notkernel-oss/not_samsung.sm8250-4.19.git
REPORT="$ARTIFACTS_DIR/phase81-zram-zsmalloc-import.txt"
CONFLICT_REPORT="$ARTIFACTS_DIR/phase81-zram-zsmalloc-conflict.txt"
mkdir -p "$ARTIFACTS_DIR"
: > "$REPORT"
: > "$CONFLICT_REPORT"

# Post-4.19.206 zsmalloc correctness foundations needed before we build more
# aggressive modern locking/object-API work in subsequent phases.
ZSMALLOC_FIXES=(
  638c954653d4183549cd7b859976da6e5698184f
  b19675b6a046da6b4e160a78eedb3dd2584aa889
  645996efc2ae391246d595832aaa6f9d3cc338c7
)

git -C "$KERNEL_DIR" config user.name "A52 Phase81 CI"
git -C "$KERNEL_DIR" config user.email "a52-phase81-ci@localhost"

git -C "$KERNEL_DIR" remote remove "$REF_REMOTE" 2>/dev/null || true
git -C "$KERNEL_DIR" remote add "$REF_REMOTE" "$REF_REPO"

for sha in "${ZSMALLOC_FIXES[@]}"; do
  info "Fetching zsmalloc foundation $sha"
  git -C "$KERNEL_DIR" fetch --no-tags --depth=2 "$REF_REMOTE" "$sha"
  subject="$(git -C "$KERNEL_DIR" show -s --format=%s "$sha")"
  printf 'apply=%s %s\n' "$sha" "$subject" | tee -a "$REPORT"

  if ! git -C "$KERNEL_DIR" cherry-pick --no-edit "$sha"; then
    unmerged="$(git -C "$KERNEL_DIR" diff --name-only --diff-filter=U || true)"
    if [[ -z "$unmerged" ]]; then
      echo "already_present_or_empty=$sha" | tee -a "$REPORT"
      git -C "$KERNEL_DIR" cherry-pick --skip
      continue
    fi

    {
      echo "failed_commit=$sha"
      echo "subject=$subject"
      echo "unmerged_paths:"
      printf '%s\n' "$unmerged"
      echo "status:"
      git -C "$KERNEL_DIR" status --short || true
    } | tee "$CONFLICT_REPORT"
    exit 82
  fi
done

python3 scripts/81_modernize_zram_preemptible.py "$KERNEL_DIR"

grep -Fq 'wait_on_bit_lock(&zram->table[index].flags' "$KERNEL_DIR/drivers/block/zram/zram_drv.c"
grep -Fq 'clear_and_wake_up_bit(ZRAM_LOCK' "$KERNEL_DIR/drivers/block/zram/zram_drv.c"
grep -Fq 'struct mutex lock;' "$KERNEL_DIR/drivers/block/zram/zcomp.h"
grep -Fq 'struct zcomp_strm __percpu *stream;' "$KERNEL_DIR/drivers/block/zram/zcomp.h"
! grep -Fq 'get_cpu_ptr(comp->stream)' "$KERNEL_DIR/drivers/block/zram/zcomp.c"
! grep -Fq 'bit_spin_lock(ZRAM_LOCK' "$KERNEL_DIR/drivers/block/zram/zram_drv.c"

git -C "$KERNEL_DIR" diff --check

{
  echo "zram_generation=2025-preemptibility-foundation"
  echo "zram_entry_lock=sleepable"
  echo "zcomp_stream_cpu_pinning=removed"
  echo "zsmalloc_post_4.19.206_fixes=applied"
  echo "zsmalloc_object_api=deferred-until-kmap-local-prereqs"
  echo "recompression=deferred"
} | tee -a "$REPORT"

info "Phase81 ZRAM/zsmalloc foundation completed"
