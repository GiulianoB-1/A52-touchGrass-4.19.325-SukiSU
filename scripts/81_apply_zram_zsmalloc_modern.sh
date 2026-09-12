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

  # Linux 4.19.206 may already contain the compacted-page accounting fix.
  # Detect the complete semantic state before attempting the donor commit,
  # otherwise Samsung's extended mm_stat_show() creates a conflict that
  # resolves to an empty cherry-pick.
  if [[ "$sha" == "638c954653d4183549cd7b859976da6e5698184f" ]] &&
     grep -Fq 'atomic_long_t pages_compacted;' "$KERNEL_DIR/include/linux/zsmalloc.h" &&
     grep -Fq 'atomic_long_add(pages_freed, &pool->stats.pages_compacted);' "$KERNEL_DIR/mm/zsmalloc.c" &&
     grep -Fq 'return pages_freed;' "$KERNEL_DIR/mm/zsmalloc.c" &&
     grep -Fq 'atomic_long_read(&pool_stats.pages_compacted)' "$KERNEL_DIR/drivers/block/zram/zram_drv.c"; then
    echo "already_present=$sha compacted-page-accounting" | tee -a "$REPORT"
    continue
  fi

  if ! git -C "$KERNEL_DIR" cherry-pick --no-edit "$sha"; then
    unmerged="$(git -C "$KERNEL_DIR" diff --name-only --diff-filter=U || true)"
    if [[ -z "$unmerged" ]]; then
      echo "already_present_or_empty=$sha" | tee -a "$REPORT"
      git -C "$KERNEL_DIR" cherry-pick --skip
      continue
    fi

    # 638c954 only conflicts in Samsung's extended zram mm_stat_show().
    # The upstream semantic change is a single read-side conversion because
    # zs_pool_stats.pages_compacted becomes atomic_long_t. Preserve Samsung's
    # dedup/LRU-writeback fields and apply only that required conversion.
    if [[ "$sha" == "638c954653d4183549cd7b859976da6e5698184f" ]] &&
       [[ "$unmerged" == "drivers/block/zram/zram_drv.c" ]]; then
      info "Resolving zsmalloc compacted-page accounting against Samsung ZRAM extensions"
      git -C "$KERNEL_DIR" checkout --ours -- drivers/block/zram/zram_drv.c
      python3 - "$KERNEL_DIR/drivers/block/zram/zram_drv.c" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text()
old = "pool_stats.pages_compacted,"
new = "atomic_long_read(&pool_stats.pages_compacted),"
old_count = s.count(old)
new_count = s.count(new)
if old_count == 1 and new_count == 0:
    s = s.replace(old, new, 1)
elif old_count == 0 and new_count == 1:
    # The reconstructed 4.19.206 Samsung side already carries the
    # atomic read-side form. Keep it and only resolve the textual conflict.
    pass
else:
    raise SystemExit(
        f"unexpected Samsung pages_compacted readers: legacy={old_count} atomic={new_count}"
    )
p.write_text(s)
PY
      git -C "$KERNEL_DIR" add drivers/block/zram/zram_drv.c
      if git -C "$KERNEL_DIR" diff --cached --quiet; then
        # All non-conflicting hunks are already in the 4.19.206 baseline and
        # our Samsung-side resolution is identical too. The donor commit is
        # therefore semantically present and the cherry-pick is empty.
        git -C "$KERNEL_DIR" cherry-pick --skip
        echo "already_present=$sha empty-after-samsung-resolution" | tee -a "$REPORT"
      else
        GIT_EDITOR=true git -C "$KERNEL_DIR" cherry-pick --continue
        echo "resolved=$sha samsung-zram-mm-stat-atomic-accounting" | tee -a "$REPORT"
      fi
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
