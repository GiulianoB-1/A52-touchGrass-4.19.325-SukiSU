#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

[[ "$(kernel_version)" == "4.19.206" ]] || fail "Expected Linux 4.19.206 before Phase82 control setup"

DEFCONFIG="$KERNEL_DIR/arch/arm64/configs/a52xq_defconfig"
REPORT="$ARTIFACTS_DIR/phase82-mglru-rcu-control.txt"
mkdir -p "$ARTIFACTS_DIR"

test -f "$DEFCONFIG" || fail "A52 defconfig missing"

# Keep the proven Phase80 MM/scheduler implementation intact.
# Only change MGLRU's boot default and enable the stock 4.19 RCU
# power mechanisms. Samsung ZRAM/zcomp must remain completely untouched.
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN
"$KERNEL_DIR/scripts/config" --file "$DEFCONFIG" --enable LRU_GEN_ENABLED
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

# Control invariant: none of Phase81's ZRAM/zcomp modernization may be present.
grep -Fq 'bit_spin_lock(ZRAM_LOCK' "$KERNEL_DIR/drivers/block/zram/zram_drv.c"
grep -Fq 'bit_spin_unlock(ZRAM_LOCK' "$KERNEL_DIR/drivers/block/zram/zram_drv.c"
grep -Fq 'get_cpu_ptr(comp->stream)' "$KERNEL_DIR/drivers/block/zram/zcomp.c"
grep -Fq 'put_cpu_ptr(comp->stream)' "$KERNEL_DIR/drivers/block/zram/zcomp.c"
! grep -Fq 'wait_on_bit_lock(&zram->table[index].flags' "$KERNEL_DIR/drivers/block/zram/zram_drv.c"
! grep -Fq 'struct mutex lock;' "$KERNEL_DIR/drivers/block/zram/zcomp.h"

{
  echo "phase=82-control"
  echo "baseline=phase80-8474b2881d0abc3214099009c53410d50ed5ad42"
  echo "mglru_default_on=y"
  echo "rcu_power_mode=fast-no-hz-plus-nocb"
  echo "rcu_fast_no_hz=y"
  echo "rcu_nocb_cpu=y"
  echo "rcu_nocbs_policy=6-7"
  echo "rcu_lazy=n"
  echo "zram=phase80-original-samsung"
  echo "zcomp=phase80-original-get_cpu_ptr-model"
} | tee "$REPORT"

git -C "$KERNEL_DIR" diff --check
info "Phase82 MGLRU-default + RCU control configuration completed"
