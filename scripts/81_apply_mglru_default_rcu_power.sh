#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

[[ "$(kernel_version)" == "4.19.206" ]] || fail "Expected Linux 4.19.206 before Phase81 RCU power setup"

DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
REPORT="$ARTIFACTS_DIR/phase81-rcu-power.txt"
mkdir -p "$ARTIFACTS_DIR"

test -f "$DEFCONFIG" || fail "A52 defconfig missing"

# MGLRU is now an intentional kernel default, not dependent on Android init.
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN_ENABLED

# Linux 4.19 already contains the two RCU mechanisms we actually want here:
# 1) RCU_FAST_NO_HZ: avoid waking idle CPUs just to drain callbacks, explicitly
#    intended to improve energy efficiency.
# 2) RCU_NOCB_CPU: offload callback invocation from boot-selected CPUs.
#
# We intentionally do NOT import/enable RCU_LAZY. It is not needed for this
# power goal and adds memory-pressure behavior we do not want to mix with the
# newly validated MGLRU baseline.
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable RCU_EXPERT
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable RCU_FAST_NO_HZ
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable RCU_NOCB_CPU

grep -Fxq 'CONFIG_LRU_GEN=y' "$DEFCONFIG"
grep -Fxq 'CONFIG_LRU_GEN_ENABLED=y' "$DEFCONFIG"
grep -Fxq 'CONFIG_RCU_EXPERT=y' "$DEFCONFIG"
grep -Fxq 'CONFIG_RCU_FAST_NO_HZ=y' "$DEFCONFIG"
grep -Fxq 'CONFIG_RCU_NOCB_CPU=y' "$DEFCONFIG"

grep -Fq 'config RCU_FAST_NO_HZ' "$KERNEL_DIR/kernel/rcu/Kconfig"
grep -Fq 'config RCU_NOCB_CPU' "$KERNEL_DIR/kernel/rcu/Kconfig"
grep -Fq 'rcu_nocbs' "$KERNEL_DIR/kernel/rcu/tree_plugin.h"

{
  echo "mglru_default_on=y"
  echo "rcu_power_mode=fast-no-hz-plus-nocb"
  echo "rcu_fast_no_hz=y"
  echo "rcu_nocb_cpu=y"
  echo "rcu_nocbs_policy=6-7"
  echo "rcu_nocbs_reason=offload-callbacks-from-two-performance-cores"
  echo "rcu_lazy=n"
  echo "rcu_idle_gp_delay=kernel-default"
} | tee "$REPORT"

git -C "$KERNEL_DIR" diff --check
info "Phase81 MGLRU default-on + RCU power configuration completed"
