#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

[[ "$(kernel_version)" == "4.19.206" ]] || fail "Expected Linux 4.19.206 before Phase81 RCU import"

RCU_REMOTE=rcu-lazy-419-ref
RCU_REPO=https://github.com/notkernel-oss/not_samsung.sm8250-4.19.git
REPORT="$ARTIFACTS_DIR/phase81-rcu-lazy-import.txt"
CONFLICT_REPORT="$ARTIFACTS_DIR/phase81-rcu-lazy-conflict.txt"
mkdir -p "$ARTIFACTS_DIR"
: > "$REPORT"
: > "$CONFLICT_REPORT"

NOCB_PREREQS=(
  e823806556f45406e4d02b21421f6940a5c7fe61
  64d26885e4ede0fe28a319e9f2fcf159e948741d
  72da6e226c6da6d4a4d2d08157cde263e1caaee3
  585e2bd7ef2995081611b8ce0be5b8b8fb36ba57
  5ccbec53e54a303e27ed64798f9c9009c7dbee07
  6ca73789d78088d7dc25ef6f6dfd447fadb880b8
  8f3e85176e3f6befbd87c85636cd8d9ccbb26fbe
  2b351c50203a0ee6d9374626636802c2e655e0b9
  4ebec8f0e0c39f131e90f03c01c607ed9de93d90
  7319761ebf97bfb2e865f692e21cdf7f1aa37123
  74bb1a1f305c900aced86e73667c4b618ab13873
  484e7493a3c1584d9fd4af8e34789cf2e3a9dd2d
  09a5e959fee151308ec4340fa4863a6d866f2874
  919df1fd3856bf633e22e3f78405dd9ea7b1fb77
  696f94996a8932db2177d51b942fc04ee158155a
  05476f66152edab537a609fe89feedbb45ab4bf1
  206f2dbb03aa2387238d88dac2589355034106f3
  8e9f91d382e5ce747ab5c3aa6110eb311f2a3389
  ed96ba5dd00050b154e7ee5d5e0515da3f836fd4
  1e73f3e00ed4ff2a9a11cd267cf10a2abef37f88
  8bf9f3cb990163f5b42010bcaeb9f91052461654
  701d95c2bf909454d370ac1dffc369843cc144e2
  9650f92be97e9019f2a573e763dcf3a7b7a47405
  62f7a6dcfbd007d8f2fde4e0f6ce8e8fe1e0055c
  e11051ac00bd0b1a77138d8995637b5e933cba42
  d59e027b607e5c9066977c75e8e54486558a45e6
)

RCU_COMMITS=(
  361f929b5d4c6a41a2f67d8614730f77f902d76a
  f7f3061bc18e88fe5dd59db994be639d9b54d3fb
  1064c904a8e33f3f718de0cd11a5978fab91f2f9
  49219cea5855e9ad8b31c04112f107aa536aee74
  68d9dac3aa5ff04d4f475e02b3ee922b430b56ef
  94d10091e92226fd6e05631a6137b554376e3c71
  fe7dcf6fe54aea09368f15e497974f4b565ab8ed
)

git -C "$KERNEL_DIR" config user.name "A52 Phase81 CI"
git -C "$KERNEL_DIR" config user.email "a52-phase81-ci@localhost"
git -C "$KERNEL_DIR" add -A
if ! git -C "$KERNEL_DIR" diff --cached --quiet; then
  git -C "$KERNEL_DIR" commit -m "ci: snapshot phase80 baseline before phase81 rcu"
fi

git -C "$KERNEL_DIR" remote remove "$RCU_REMOTE" 2>/dev/null || true
git -C "$KERNEL_DIR" remote add "$RCU_REMOTE" "$RCU_REPO"

for sha in "${NOCB_PREREQS[@]}" "${RCU_COMMITS[@]}"; do
  if [[ " ${NOCB_PREREQS[*]} " == *" $sha "* ]]; then
    info "Fetching NOCB prerequisite $sha"
  else
    info "Fetching lazy-RCU backport $sha"
  fi
  git -C "$KERNEL_DIR" fetch --no-tags --depth=2 "$RCU_REMOTE" "$sha"
  subject="$(git -C "$KERNEL_DIR" show -s --format=%s "$sha")"
  printf 'apply=%s %s\n' "$sha" "$subject" | tee -a "$REPORT"

  if ! git -C "$KERNEL_DIR" cherry-pick --no-edit "$sha"; then
    {
      echo "failed_commit=$sha"
      echo "subject=$subject"
      echo "unmerged_paths:"
      git -C "$KERNEL_DIR" diff --name-only --diff-filter=U || true
      echo "status:"
      git -C "$KERNEL_DIR" status --short || true
    } | tee "$CONFLICT_REPORT"
    exit 81
  fi
done

python3 - "$KERNEL_DIR" "$ARTIFACTS_DIR/phase81-rcu-runtime-control.txt" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
kconfig = root / "kernel/rcu/Kconfig"
tree = root / "kernel/rcu/tree.c"

ks = kconfig.read_text()
if "config RCU_LAZY\n" not in ks:
    raise SystemExit("RCU_LAZY missing after 4.19 lazy-RCU import")

if "config RCU_LAZY_DEFAULT_OFF\n" not in ks:
    anchor = """config RCU_LAZY
\tbool "RCU callback lazy invocation functionality"
\tdepends on RCU_NOCB_CPU
\tdefault n
\thelp
\t  To save power, batch RCU callbacks and flush after delay, memory
\t  pressure or callback list growing too big.
"""
    block = """
config RCU_LAZY_DEFAULT_OFF
\tbool "Turn RCU lazy invocation off by default"
\tdepends on RCU_LAZY
\tdefault n
\thelp
\t  Build lazy RCU support while keeping it disabled unless explicitly
\t  enabled with rcutree.enable_rcu_lazy=1 on the kernel command line.
"""
    if anchor not in ks:
        raise SystemExit("RCU_LAZY Kconfig anchor changed")
    ks = ks.replace(anchor, anchor + "\n" + block, 1)
    kconfig.write_text(ks)

s = tree.read_text()
old_call = """void call_rcu(struct rcu_head *head, rcu_callback_t func)
{
\treturn __call_rcu_common(head, func, IS_ENABLED(CONFIG_RCU_LAZY));
}
"""
new_call = """void call_rcu(struct rcu_head *head, rcu_callback_t func)
{
\treturn __call_rcu_common(head, func, enable_rcu_lazy);
}
"""
if old_call not in s and new_call not in s:
    raise SystemExit("call_rcu lazy-selection anchor changed")

if "static bool enable_rcu_lazy __read_mostly" not in s:
    marker = "#ifdef CONFIG_RCU_LAZY\n/**\n * call_rcu_flush()"
    insertion = """#ifdef CONFIG_RCU_LAZY
/*
 * Keep the feature compiled in but default it off. Phase81's primary
 * AnyKernel package enables it explicitly with rcutree.enable_rcu_lazy=1.
 * This mirrors modern Android's safety model and leaves a boot-time
 * kill switch for memory-pressure or suspend regressions.
 */
static bool enable_rcu_lazy __read_mostly =
\t!IS_ENABLED(CONFIG_RCU_LAZY_DEFAULT_OFF);
module_param(enable_rcu_lazy, bool, 0444);

/**
 * call_rcu_flush()"""
    if marker not in s:
        raise SystemExit("call_rcu_flush insertion anchor changed")
    s = s.replace(marker, insertion, 1)

if old_call in s:
    s = s.replace(old_call, new_call, 1)

tree.write_text(s)

report.write_text(
    "lazy_rcu_compiled=y\n"
    "lazy_rcu_build_default=off\n"
    "lazy_rcu_boot_param=rcutree.enable_rcu_lazy\n"
    "lazy_rcu_primary_package=on\n"
    "lazy_rcu_rollback_package=off\n"
    "lazy_rcu_memory_pressure_shrinker=y\n"
    "lazy_rcu_expedited_gp_hurry=y\n"
)
PY

DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN_ENABLED
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable RCU_LAZY
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable RCU_LAZY_DEFAULT_OFF

grep -Fxq 'CONFIG_LRU_GEN=y' "$DEFCONFIG"
grep -Fxq 'CONFIG_LRU_GEN_ENABLED=y' "$DEFCONFIG"
grep -Fxq 'CONFIG_RCU_LAZY=y' "$DEFCONFIG"
grep -Fxq 'CONFIG_RCU_LAZY_DEFAULT_OFF=y' "$DEFCONFIG"
grep -Fq 'call_rcu_flush' "$KERNEL_DIR/kernel/rcu/tree.c"
grep -Fq 'lazy_rcu_shrink_scan' "$KERNEL_DIR/kernel/rcu/tree_plugin.h"
grep -Fq 'enable_rcu_lazy' "$KERNEL_DIR/kernel/rcu/tree.c"
grep -Fq 'rcu_gp_is_expedited()' "$KERNEL_DIR/kernel/rcu/tree.c"

git -C "$KERNEL_DIR" diff --check

{
  echo "mglru_default_on=y"
  echo "rcu_nocb_forward_progress_prereqs=y"
  echo "rcu_nocb_bypass=y"
  echo "rcu_lazy_compiled=y"
  echo "rcu_lazy_default_off=y"
  echo "rcu_lazy_boot_enable=rcutree.enable_rcu_lazy=1"
  echo "rcu_nocbs_for_a52=0-7"
} | tee -a "$REPORT"

info "Phase81 MGLRU default-on + lazy-RCU import completed"
