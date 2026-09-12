#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

[[ "$(kernel_version)" == "4.19.206" ]] || fail "Expected Linux 4.19.206 before MGLRU import"

REF_REMOTE=mglru-qcom-ref
REF_REPO=https://github.com/shinichi-c/Dynamic_kernel_4.19_oneplus_sdm845.git

# Android/Qualcomm 4.19 backport series derived from Yu Zhao's 2022 MGLRU v9
# work, followed by the Qualcomm baseline repair and Android speculative-fault
# integration. Keep the list explicit and pinned so the experiment is reproducible.
MGLRU_COMMITS=(
  # Prerequisite ladder used by the Qualcomm 4.19 backport.
  ae75c1a83c1b76c33e64c3637fda8505da86d975 # use add_page_to_lru_list_tail
  83e2f547484ac2d588b3965640e6edf7d4b6c6d7 # arm64 cpu_has_hw_af helper
  e13048fe51c129efddf7576fdd68da7403ac862f # update_lru_size helper use
  d3e29959487382c0ab0b8831e070d8315dd83b5e # swapcache shadow entries
  97be316d37b66af8f3c856ca870b7b5dd025822a # remove superfluous ClearPageActive
  aba9c57a3ddfcfab86c25ca3f2f756911fefa61b # LRU helper macros
  3af1140d34f4d882316264727540697f422e3b12 # vmscan add_page_to_lru_list
  2adcdb2038a312d146d112195455b9ca67b2f545 # shuffle LRU add/del helpers
  325424277a19b6bb12b5765f9da06c7c4b9c60c9 # drop enum lru_list from add helpers
  f8532e3c136aea75e9cc2601df14ae09f7a8bcc6 # trace insertion API
  4fa0c5f05a83589daab8069e77db50c6aa67ab39 # drop enum from del helper
  2e0c5418e4196427789e691e0a73a212e08f9e20 # __clear_page_lru_flags
  7e60a1516589069575f68fde6738dd30f0604666 # VM_BUG_ON LRU flags
  d6c9c2a13aac9d4fb797dfff6a21ada742f270a8 # fold page_lru_base_type
  842c6e1303445bd6c036340ef3b255cc23422b4f # arch_has_hw_pte_young
  b7e59fa3da6b993f2d91d321a60515b3442f9d75 # ARCH_HAS_NONLEAF_PMD_YOUNG

  # MGLRU v9 core and Android/Qualcomm follow-up fixes.
  67e9d5c8e0d28eb521533e0bb42771d12959d026 # groundwork
  e9343ee8a22fe2267ac8dfd62a8e46c45d2e65e5 # minimal implementation
  fba9f87f68f31973557e8ec315b177181324b670 # rmap locality
  cb7e73b77cef509361ea7923b10ecfe836e3d48d # page-table walks
  0b8c89d3791eec2dd6d0ea9a971bf7cc0bb4c177 # multi-memcg selection
  003eabcd2570febabe7f0466e4c915d5578835c2 # runtime kill switch
  fa25f9ac5f191f3de8b64557a5a7bc2573afa33c # thrashing prevention
  b195eeedf84a4294b67598153b2f5eae72c9477e # debugfs interface
  bbdb283aa6ef69ce5856a1373d4a44b6b00b02f5 # IRQ-off fix
  59a4952e7940da5223a5c28ef8c0c348febb6e5c # Qualcomm 4.19 baseline fixes
  ccdfe894de1450d146de5268a47e25efa6d28296 # speculative page fault integration
)

REPORT="$ARTIFACTS_DIR/mglru-phase1-import.txt"
CONFLICT_REPORT="$ARTIFACTS_DIR/mglru-phase1-conflict.txt"
mkdir -p "$ARTIFACTS_DIR"
: > "$REPORT"
: > "$CONFLICT_REPORT"

info "Creating a clean local commit for the already-reviewed Phase77 EEVDF + CASS source"
git -C "$KERNEL_DIR" config user.name "A52 MGLRU CI"
git -C "$KERNEL_DIR" config user.email "a52-mglru-ci@localhost"
git -C "$KERNEL_DIR" add -A
if ! git -C "$KERNEL_DIR" diff --cached --quiet; then
  git -C "$KERNEL_DIR" commit -m "ci: snapshot phase77 eevdf cass baseline before mglru"
fi
BASELINE_SHA="$(git -C "$KERNEL_DIR" rev-parse HEAD)"
printf 'baseline_sha=%s\n' "$BASELINE_SHA" | tee -a "$REPORT"

git -C "$KERNEL_DIR" remote remove "$REF_REMOTE" 2>/dev/null || true
git -C "$KERNEL_DIR" remote add "$REF_REMOTE" "$REF_REPO"

resolve_groundwork_conflicts() {
  local sha="$1"
  local unmerged

  [[ "$sha" == "67e9d5c8e0d28eb521533e0bb42771d12959d026" ]] || return 1
  unmerged="$(git -C "$KERNEL_DIR" diff --name-only --diff-filter=U | sort)"
  [[ "$unmerged" ==   info "Fetching pinned MGLRU commit $sha"
  git -C "$KERNEL_DIR" fetch --no-tags --depth=2 "$REF_REMOTE" "$sha"
  subject="$(git -C "$KERNEL_DIR" show -s --format=%s "$sha")"
  printf 'apply=%s %s\n' "$sha" "$subject" | tee -a "$REPORT"

  if ! git -C "$KERNEL_DIR" cherry-pick --no-edit "$sha"; then
    unmerged="$(git -C "$KERNEL_DIR" diff --name-only --diff-filter=U || true)"

    if resolve_groundwork_conflicts "$sha"; then
      echo "resolved=$sha samsung-pageflags-kconfig" | tee -a "$REPORT"
      continue
    fi

    if [[ -z "$unmerged" ]]; then
      # Linux 4.19.206 may already contain individual prerequisite backports.
      # Treat a clean empty cherry-pick as "already present" rather than a
      # port failure.
      echo "skip_or_empty=$sha $subject" | tee -a "$REPORT"
      git -C "$KERNEL_DIR" cherry-pick --skip
      continue
    fi

    {
      echo "failed_commit=$sha"
      echo "subject=$subject"
      echo "unmerged_paths:"
      printf '%s\n' "$unmerged"
      echo
      echo "status:"
      git -C "$KERNEL_DIR" status --short || true
      echo
      echo "conflict_hunks:"
      git -C "$KERNEL_DIR" grep -n -E '^(<<<<<<<|=======|>>>>>>>)' -- . ':!*.patch' || true
    } | tee "$CONFLICT_REPORT"
    exit 79
  fi
done

DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
test -f "$DEFCONFIG" || fail "A52 defconfig missing"

# Phase 1 safety policy:
# Compile the full runtime-switchable implementation, but boot with legacy LRU.
# After a successful boot MGLRU can be enabled live through:
#   echo Y > /sys/kernel/mm/lru_gen/enabled
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --disable LRU_GEN_ENABLED
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --disable LRU_GEN_STATS

grep -Fxq 'CONFIG_LRU_GEN=y' "$DEFCONFIG"
grep -Fxq '# CONFIG_LRU_GEN_ENABLED is not set' "$DEFCONFIG"

# Structural checks for the imported implementation and the Android-specific
# speculative-fault integration.
grep -Fq 'struct lru_gen_struct' "$KERNEL_DIR/include/linux/mmzone.h"
grep -Fq 'lru_gen_enabled' "$KERNEL_DIR/mm/vmscan.c"
grep -Fq 'lru_gen_enter_fault' "$KERNEL_DIR/mm/memory.c"
grep -Fq 'lru_gen_exit_fault' "$KERNEL_DIR/mm/memory.c"

{
  echo "mglru_compiled=y"
  echo "mglru_default_enabled=n"
  echo "runtime_enable=/sys/kernel/mm/lru_gen/enabled"
  echo "runtime_min_ttl=/sys/kernel/mm/lru_gen/min_ttl_ms"
  echo "reference_repo=$REF_REPO"
  echo "reference_series=4.19-qcom-pinned"
  echo "speculative_fault_integration=y"
  echo "baseline_sha=$BASELINE_SHA"
  echo "final_sha=$(git -C "$KERNEL_DIR" rev-parse HEAD)"
} | tee -a "$REPORT"

git -C "$KERNEL_DIR" diff --check
info "MGLRU phase 1 import completed"
include/linux/page-flags-layout.h\nmm/Kconfig' ]] || return 1

  info "Resolving Samsung page-flags/Kconfig shape for MGLRU groundwork"

  git -C "$KERNEL_DIR" checkout --ours -- include/linux/page-flags-layout.h mm/Kconfig

  python3 - "$KERNEL_DIR" "$ARTIFACTS_DIR/mglru-groundwork-resolution.txt" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
rows = []

p = root / "include/linux/page-flags-layout.h"
s = p.read_text()

old = "#if SECTIONS_WIDTH+ZONES_WIDTH+NODES_SHIFT <= BITS_PER_LONG - NR_PAGEFLAGS"
new = "#if SECTIONS_WIDTH+ZONES_WIDTH+NODES_SHIFT+LRU_GEN_WIDTH+LRU_REFS_WIDTH <= BITS_PER_LONG - NR_PAGEFLAGS"
if old not in s:
    raise SystemExit("Samsung NODES_WIDTH page-flags anchor missing")
s = s.replace(old, new, 1)
rows.append("nodes_width=preserved-samsung-plus-lrugen-bits\n")

old = "#if SECTIONS_WIDTH+ZONES_WIDTH+NODES_SHIFT+LAST_CPUPID_SHIFT <= BITS_PER_LONG - NR_PAGEFLAGS"
new = "#if SECTIONS_WIDTH+ZONES_WIDTH+NODES_SHIFT+LAST_CPUPID_SHIFT+LRU_GEN_WIDTH+LRU_REFS_WIDTH <= BITS_PER_LONG - NR_PAGEFLAGS"
if old not in s:
    raise SystemExit("Samsung LAST_CPUPID_WIDTH page-flags anchor missing")
s = s.replace(old, new, 1)
rows.append("last_cpupid_width=preserved-samsung-plus-lrugen-bits\n")

old = """#if SECTIONS_WIDTH+NODES_WIDTH+ZONES_WIDTH+LAST_CPUPID_WIDTH+KASAN_TAG_WIDTH \\
\t> BITS_PER_LONG - NR_PAGEFLAGS"""
new = """#if SECTIONS_WIDTH+NODES_WIDTH+ZONES_WIDTH+LAST_CPUPID_WIDTH+KASAN_TAG_WIDTH+ \\
\tLRU_GEN_WIDTH+LRU_REFS_WIDTH > BITS_PER_LONG - NR_PAGEFLAGS"""
if old not in s:
    raise SystemExit("Samsung KASAN page-flags capacity anchor missing")
s = s.replace(old, new, 1)
p.write_text(s)
rows.append("kasan_capacity=includes-lrugen-bits\n")

p = root / "mm/Kconfig"
s = p.read_text()
if "config LRU_GEN\n" not in s:
    anchor = """config ARCH_HAS_PTE_SPECIAL
\tbool
"""
    block = """
config LRU_GEN
\tbool "Multi-Gen LRU"
\tdepends on MMU
\t# the following options can use up the spare bits in page flags
\tdepends on !MAXSMP && (64BIT || !SPARSEMEM || SPARSEMEM_VMEMMAP)
\thelp
\t  A high performance LRU implementation to overcommit memory.
"""
    if anchor not in s:
        raise SystemExit("Samsung mm/Kconfig ARCH_HAS_PTE_SPECIAL anchor missing")
    s = s.replace(anchor, anchor + block, 1)
    p.write_text(s)
rows.append("kconfig=preserved-samsung-options-added-lru-gen\n")

report.write_text("".join(rows))
PY

  git -C "$KERNEL_DIR" add include/linux/page-flags-layout.h mm/Kconfig
  git -C "$KERNEL_DIR" diff --check --cached
  git -C "$KERNEL_DIR" cherry-pick --continue
  return 0
}

for sha in "${MGLRU_COMMITS[@]}"; do
  info "Fetching pinned MGLRU commit $sha"
  git -C "$KERNEL_DIR" fetch --no-tags --depth=2 "$REF_REMOTE" "$sha"
  subject="$(git -C "$KERNEL_DIR" show -s --format=%s "$sha")"
  printf 'apply=%s %s\n' "$sha" "$subject" | tee -a "$REPORT"

  if ! git -C "$KERNEL_DIR" cherry-pick --no-edit "$sha"; then
    unmerged="$(git -C "$KERNEL_DIR" diff --name-only --diff-filter=U || true)"
    if [[ -z "$unmerged" ]]; then
      # Linux 4.19.206 may already contain individual prerequisite backports.
      # Treat a clean empty cherry-pick as "already present" rather than a
      # port failure.
      echo "skip_or_empty=$sha $subject" | tee -a "$REPORT"
      git -C "$KERNEL_DIR" cherry-pick --skip
      continue
    fi

    {
      echo "failed_commit=$sha"
      echo "subject=$subject"
      echo "unmerged_paths:"
      printf '%s\n' "$unmerged"
      echo
      echo "status:"
      git -C "$KERNEL_DIR" status --short || true
      echo
      echo "conflict_hunks:"
      git -C "$KERNEL_DIR" grep -n -E '^(<<<<<<<|=======|>>>>>>>)' -- . ':!*.patch' || true
    } | tee "$CONFLICT_REPORT"
    exit 79
  fi
done

DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
test -f "$DEFCONFIG" || fail "A52 defconfig missing"

# Phase 1 safety policy:
# Compile the full runtime-switchable implementation, but boot with legacy LRU.
# After a successful boot MGLRU can be enabled live through:
#   echo Y > /sys/kernel/mm/lru_gen/enabled
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --disable LRU_GEN_ENABLED
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --disable LRU_GEN_STATS

grep -Fxq 'CONFIG_LRU_GEN=y' "$DEFCONFIG"
grep -Fxq '# CONFIG_LRU_GEN_ENABLED is not set' "$DEFCONFIG"

# Structural checks for the imported implementation and the Android-specific
# speculative-fault integration.
grep -Fq 'struct lru_gen_struct' "$KERNEL_DIR/include/linux/mmzone.h"
grep -Fq 'lru_gen_enabled' "$KERNEL_DIR/mm/vmscan.c"
grep -Fq 'lru_gen_enter_fault' "$KERNEL_DIR/mm/memory.c"
grep -Fq 'lru_gen_exit_fault' "$KERNEL_DIR/mm/memory.c"

{
  echo "mglru_compiled=y"
  echo "mglru_default_enabled=n"
  echo "runtime_enable=/sys/kernel/mm/lru_gen/enabled"
  echo "runtime_min_ttl=/sys/kernel/mm/lru_gen/min_ttl_ms"
  echo "reference_repo=$REF_REPO"
  echo "reference_series=4.19-qcom-pinned"
  echo "speculative_fault_integration=y"
  echo "baseline_sha=$BASELINE_SHA"
  echo "final_sha=$(git -C "$KERNEL_DIR" rev-parse HEAD)"
} | tee -a "$REPORT"

git -C "$KERNEL_DIR" diff --check
info "MGLRU phase 1 import completed"
