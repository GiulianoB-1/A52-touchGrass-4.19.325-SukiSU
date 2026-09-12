#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

[[ "$(kernel_version)" == "4.19.206" ]] || fail "Expected Linux 4.19.206 before MGLRU import"

REF_REMOTE=mglru-qcom-ref
REF_REPO=https://github.com/shinichi-c/Dynamic_kernel_4.19_oneplus_sdm845.git

MGLRU_COMMITS=(
  ae75c1a83c1b76c33e64c3637fda8505da86d975
  83e2f547484ac2d588b3965640e6edf7d4b6c6d7
  e13048fe51c129efddf7576fdd68da7403ac862f
  d3e29959487382c0ab0b8831e070d8315dd83b5e
  97be316d37b66af8f3c856ca870b7b5dd025822a
  aba9c57a3ddfcfab86c25ca3f2f756911fefa61b
  3af1140d34f4d882316264727540697f422e3b12
  2adcdb2038a312d146d112195455b9ca67b2f545
  325424277a19b6bb12b5765f9da06c7c4b9c60c9
  f8532e3c136aea75e9cc2601df14ae09f7a8bcc6
  4fa0c5f05a83589daab8069e77db50c6aa67ab39
  2e0c5418e4196427789e691e0a73a212e08f9e20
  7e60a1516589069575f68fde6738dd30f0604666
  d6c9c2a13aac9d4fb797dfff6a21ada742f270a8
  842c6e1303445bd6c036340ef3b255cc23422b4f
  b7e59fa3da6b993f2d91d321a60515b3442f9d75
  67e9d5c8e0d28eb521533e0bb42771d12959d026
  e9343ee8a22fe2267ac8dfd62a8e46c45d2e65e5
  fba9f87f68f31973557e8ec315b177181324b670
  cb7e73b77cef509361ea7923b10ecfe836e3d48d
  0b8c89d3791eec2dd6d0ea9a971bf7cc0bb4c177
  003eabcd2570febabe7f0466e4c915d5578835c2
  fa25f9ac5f191f3de8b64557a5a7bc2573afa33c
  b195eeedf84a4294b67598153b2f5eae72c9477e
  bbdb283aa6ef69ce5856a1373d4a44b6b00b02f5
  59a4952e7940da5223a5c28ef8c0c348febb6e5c
  ccdfe894de1450d146de5268a47e25efa6d28296
)

REPORT="$ARTIFACTS_DIR/mglru-phase1-import.txt"
CONFLICT_REPORT="$ARTIFACTS_DIR/mglru-phase1-conflict.txt"
mkdir -p "$ARTIFACTS_DIR"
: > "$REPORT"
: > "$CONFLICT_REPORT"

info "Creating clean Phase77 EEVDF + CASS snapshot"
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

resolve_minimal_kconfig_conflict() {
  local sha="$1"
  local unmerged_csv

  [[ "$sha" == "e9343ee8a22fe2267ac8dfd62a8e46c45d2e65e5" ]] || return 1

  unmerged_csv="$(git -C "$KERNEL_DIR" diff --name-only --diff-filter=U | sort | paste -sd, -)"
  [[ "$unmerged_csv" == "mm/Kconfig" ]] || return 1

  info "Resolving Samsung mm/Kconfig conflict for MGLRU minimal implementation"
  git -C "$KERNEL_DIR" checkout --ours -- mm/Kconfig

  python3 - "$KERNEL_DIR" "$ARTIFACTS_DIR/mglru-minimal-kconfig-resolution.txt" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
p = root / "mm/Kconfig"
s = p.read_text()

if "config LRU_GEN\n" not in s:
    raise SystemExit("LRU_GEN missing before minimal Kconfig resolution")

if "config LRU_GEN_STATS\n" not in s:
    anchor = """config LRU_GEN
\tbool "Multi-Gen LRU"
\tdepends on MMU
\t# the following options can use up the spare bits in page flags
\tdepends on !MAXSMP && (64BIT || !SPARSEMEM || SPARSEMEM_VMEMMAP)
\thelp
\t  A high performance LRU implementation to overcommit memory.
"""
    block = """
config LRU_GEN_STATS
\tbool "Full stats for debugging"
\tdepends on LRU_GEN
\thelp
\t  Do not enable this option unless you plan to look at historical stats
\t  from evicted generations for debugging purpose.

\t  This option has a per-memcg and per-node memory overhead.
"""
    if anchor not in s:
        raise SystemExit("Samsung LRU_GEN block shape changed unexpectedly")
    s = s.replace(anchor, anchor + "\n" + block, 1)

p.write_text(s)
report.write_text(
    "minimal_kconfig=preserved-samsung-layout\n"
    "lru_gen_stats=added\n"
)
PY

  git -C "$KERNEL_DIR" add mm/Kconfig
  git -C "$KERNEL_DIR" diff --check --cached
  git -C "$KERNEL_DIR" cherry-pick --continue
}

resolve_groundwork_conflicts() {
  local sha="$1"
  local unmerged_csv

  [[ "$sha" == "67e9d5c8e0d28eb521533e0bb42771d12959d026" ]] || return 1

  unmerged_csv="$(git -C "$KERNEL_DIR" diff --name-only --diff-filter=U | sort | paste -sd, -)"
  [[ "$unmerged_csv" == "include/linux/page-flags-layout.h,mm/Kconfig" ]] || return 1

  info "Resolving Samsung page-flags and mm/Kconfig conflicts for MGLRU groundwork"
  git -C "$KERNEL_DIR" checkout --ours -- include/linux/page-flags-layout.h mm/Kconfig

  python3 - "$KERNEL_DIR" "$ARTIFACTS_DIR/mglru-groundwork-resolution.txt" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
rows = []

p = root / "include/linux/page-flags-layout.h"
s = p.read_text()

pairs = [
    (
        "#if SECTIONS_WIDTH+ZONES_WIDTH+NODES_SHIFT <= BITS_PER_LONG - NR_PAGEFLAGS",
        "#if SECTIONS_WIDTH+ZONES_WIDTH+NODES_SHIFT+LRU_GEN_WIDTH+LRU_REFS_WIDTH <= BITS_PER_LONG - NR_PAGEFLAGS",
        "nodes_width"
    ),
    (
        "#if SECTIONS_WIDTH+ZONES_WIDTH+NODES_SHIFT+LAST_CPUPID_SHIFT <= BITS_PER_LONG - NR_PAGEFLAGS",
        "#if SECTIONS_WIDTH+ZONES_WIDTH+NODES_SHIFT+LAST_CPUPID_SHIFT+LRU_GEN_WIDTH+LRU_REFS_WIDTH <= BITS_PER_LONG - NR_PAGEFLAGS",
        "last_cpupid_width"
    ),
]
for old, new, name in pairs:
    if old not in s:
        raise SystemExit(f"missing Samsung page-flags anchor: {name}")
    s = s.replace(old, new, 1)
    rows.append(f"{name}=includes-lrugen-bits\n")

old = "#if SECTIONS_WIDTH+NODES_WIDTH+ZONES_WIDTH+LAST_CPUPID_WIDTH+KASAN_TAG_WIDTH \\\n\t> BITS_PER_LONG - NR_PAGEFLAGS"
new = "#if SECTIONS_WIDTH+NODES_WIDTH+ZONES_WIDTH+LAST_CPUPID_WIDTH+KASAN_TAG_WIDTH+ \\\n\tLRU_GEN_WIDTH+LRU_REFS_WIDTH > BITS_PER_LONG - NR_PAGEFLAGS"
if old not in s:
    raise SystemExit("missing Samsung KASAN page-flags capacity anchor")
s = s.replace(old, new, 1)
p.write_text(s)
rows.append("kasan_capacity=includes-lrugen-bits\n")

p = root / "mm/Kconfig"
s = p.read_text()
if "config LRU_GEN\n" not in s:
    anchor = "config ARCH_HAS_PTE_SPECIAL\n\tbool\n"
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
        raise SystemExit("missing Samsung mm/Kconfig ARCH_HAS_PTE_SPECIAL anchor")
    s = s.replace(anchor, anchor + "\n" + block, 1)
    p.write_text(s)
rows.append("kconfig=preserved-samsung-options-added-lru-gen\n")

report.write_text("".join(rows))
PY

  git -C "$KERNEL_DIR" add include/linux/page-flags-layout.h mm/Kconfig
  git -C "$KERNEL_DIR" diff --check --cached
  git -C "$KERNEL_DIR" cherry-pick --continue
}

for sha in "${MGLRU_COMMITS[@]}"; do
  info "Fetching pinned MGLRU commit $sha"
  git -C "$KERNEL_DIR" fetch --no-tags --depth=2 "$REF_REMOTE" "$sha"
  subject="$(git -C "$KERNEL_DIR" show -s --format=%s "$sha")"
  printf 'apply=%s %s\n' "$sha" "$subject" | tee -a "$REPORT"

  if ! git -C "$KERNEL_DIR" cherry-pick --no-edit "$sha"; then
    unmerged="$(git -C "$KERNEL_DIR" diff --name-only --diff-filter=U || true)"

    if resolve_groundwork_conflicts "$sha"; then
      echo "resolved=$sha samsung-pageflags-kconfig" | tee -a "$REPORT"
      continue
    fi

    if resolve_minimal_kconfig_conflict "$sha"; then
      echo "resolved=$sha samsung-minimal-kconfig" | tee -a "$REPORT"
      continue
    fi

    if [[ -z "$unmerged" ]]; then
      echo "skip_or_empty=$sha $subject" | tee -a "$REPORT"
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
    exit 79
  fi
done

DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
test -f "$DEFCONFIG" || fail "A52 defconfig missing"

"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --disable LRU_GEN_ENABLED
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --disable LRU_GEN_STATS

grep -Fxq 'CONFIG_LRU_GEN=y' "$DEFCONFIG"
grep -Fxq '# CONFIG_LRU_GEN_ENABLED is not set' "$DEFCONFIG"
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
