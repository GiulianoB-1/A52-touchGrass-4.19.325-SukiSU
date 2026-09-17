#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

KERNEL="workspace/touchgrass-a52xq"
mkdir -p artifacts release
chmod +x scripts/*.sh scripts/*.py

say() { printf '\n=== %s ===\n' "$*"; }

say "Reconstruct exact 4.19.206 Phase80 base"
./scripts/01_prepare_source.sh
./scripts/03_apply_linux_4.19.153.sh
./scripts/04_apply_linux_4.19.154.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.154 4.19.159
./scripts/checkpoint_resolve_linux_4.19.159.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.159 4.19.164
./scripts/checkpoint_resolve_linux_4.19.164.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.164 4.19.180
./scripts/checkpoint_resolve_linux_4.19.180.sh
./scripts/05a_diagnose_linux_checkpoint.sh 4.19.180 4.19.200
./scripts/checkpoint_resolve_linux_4.19.200.sh
./scripts/57_update_linux_4.19.200_to_4.19.206.sh
./scripts/58_backport_bpf_ringbuf.sh
./scripts/58_apply_ringbuf_overrun_fix.sh
./scripts/07_patch_resukisu_exec_hook.sh

say "Apply EEVDF/CASS/MGLRU Phase80 stack"
python3 scripts/59_apply_eevdf_phase1_scaffold.py "$KERNEL"
python3 scripts/60_apply_eevdf_phase2_accounting.py "$KERNEL"
python3 scripts/61_apply_eevdf_phase3_deadline_lag.py "$KERNEL"
python3 scripts/62_apply_eevdf_phase4_tree_picker.py "$KERNEL"
python3 scripts/63_apply_eevdf_phase5_picker_integration.py "$KERNEL"
python3 scripts/64_apply_eevdf_phase6_wakeup_preempt.py "$KERNEL"
python3 scripts/65_apply_eevdf_phase7_placement_consistency.py "$KERNEL"
python3 scripts/74_apply_eevdf_multitask_correctness.py "$KERNEL"
python3 scripts/77_apply_eevdf_lifecycle_correctness.py "$KERNEL"
python3 scripts/78_apply_eevdf77_cass_hybrid.py "$KERNEL"
./scripts/79_apply_mglru_phase1.sh
python3 scripts/80_apply_mglru_android_hardening.py "$KERNEL"

git -C "$KERNEL" diff --check

say "Apply Android17 Binder/BinderFS/allocator and Android FUSE base"
./scripts/81_apply_android17_binder_core_probe.sh
python3 scripts/82_enable_retained_binderfs.py "$KERNEL"
./scripts/84_apply_android17_binder_alloc.sh
python3 scripts/84_apply_fuse_passthrough_android.py "$KERNEL"

git -C "$KERNEL" diff --check

say "Apply Android17 uclamp Phase1 + CASS Phase2"
python3 scripts/85_apply_android17_uclamp_phase1.py "$KERNEL"
python3 scripts/86_apply_uclamp_cass_p2.py "$KERNEL"

git -C "$KERNEL" diff --check

say "Apply A619 GPU modernization P1-P5"
python3 scripts/85_apply_a619_gpu_modernization.py "$KERNEL" 2>&1 | tee artifacts/a619-gpu-p1-apply.txt
python3 scripts/87_apply_a619_gpu_modernization_p2.py "$KERNEL" 2>&1 | tee artifacts/a619-gpu-p2-apply.txt
python3 scripts/90_apply_a619_gpu_modernization_p3.py "$KERNEL" 2>&1 | tee artifacts/a619-gpu-p3-apply.txt
python3 scripts/91_apply_a619_gpu_modernization_p4.py "$KERNEL" 2>&1 | tee artifacts/a619-gpu-p4-apply.txt
python3 scripts/94_apply_a619_gpu_modernization_p5.py "$KERNEL" 2>&1 | tee artifacts/a619-gpu-p5-apply.txt

git -C "$KERNEL" diff --check

say "Apply BBRv3/FQ and Bluetooth P2A-P2C"
./scripts/87_apply_bbr3_net.sh "$KERNEL" 2>&1 | tee artifacts/bbr3-fq-apply.txt
python3 scripts/96_apply_bt_geni_wake_hardening.py "$KERNEL" 2>&1 | tee artifacts/bt-p2a-geni-apply.txt
python3 scripts/97_apply_bt_geni_async_wake.py "$KERNEL" 2>&1 | tee artifacts/bt-p2b-async-wake-apply.txt
python3 scripts/98_apply_bt_geni_rx_pm_sync.py "$KERNEL" 2>&1 | tee artifacts/bt-p2c-rx-pm-sync-apply.txt

git -C "$KERNEL" diff --check

say "Apply FUSE Modern P1-P3 + INIT probe + 7.40 compatibility"
python3 scripts/98_apply_fuse_passthrough_modern_p1.py "$KERNEL" 2>&1 | tee artifacts/fuse-modern-p1-apply.txt
python3 scripts/99_apply_fuse_passthrough_modern_p2.py "$KERNEL" 2>&1 | tee artifacts/fuse-modern-p2-apply.txt
python3 scripts/101_apply_fuse_passthrough_modern_p3.py "$KERNEL" 2>&1 | tee artifacts/fuse-modern-p3-apply.txt
python3 scripts/102_apply_fuse_negotiation_probe.py "$KERNEL" 2>&1 | tee artifacts/fuse-negotiation-probe-apply.txt
python3 scripts/103_apply_fuse_740_init_compat.py "$KERNEL" 2>&1 | tee artifacts/fuse-740-init-compat-apply.txt

git -C "$KERNEL" diff --check

say "Apply GPU UV P1 + diagnostics"
python3 scripts/104_apply_a619_gpu_undervolt_p1.py "$KERNEL" 2>&1 | tee artifacts/a619-gpu-uv-p1-apply.txt
python3 scripts/105_apply_a619_gpu_undervolt_p1_diag.py "$KERNEL" 2>&1 | tee artifacts/a619-gpu-uv-p1-diag-apply.txt

git -C "$KERNEL" diff --check

say "Apply Modern MGLRU P1+P2+P3 chain from baseline run"
python3 scripts/106_apply_mglru_modern_p1.py "$KERNEL"

git -C "$KERNEL" diff --check

say "Apply EEVDF post-6.6 correctness and uclamp/CASS efficiency fixes"
python3 scripts/109_apply_eevdf_post66_efficiency.py "$KERNEL" 2>&1 | tee artifacts/eevdf-post66-efficiency-apply.txt
python3 scripts/110_apply_uclamp_cass_efficiency.py "$KERNEL" 2>&1 | tee artifacts/uclamp-cass-efficiency-apply.txt

git -C "$KERNEL" diff --check

say "Static scheduler audit"
grep -Fq 'u64\t\t\t\tvprot;' "$KERNEL/include/linux/sched.h"
grep -Fq 'A52 EEVDF post-6.6 protection correctness' "$KERNEL/kernel/sched/fair.c"
grep -Fq 'eevdf_protect_slice(curr)' "$KERNEL/kernel/sched/fair.c"
grep -Fq 'expired || !eevdf_protect_slice(curr)' "$KERNEL/kernel/sched/fair.c"
! grep -Fq 'curr->vlag == curr->deadline' "$KERNEL/kernel/sched/fair.c"
! grep -Fq 'se->vlag = se->deadline;' "$KERNEL/kernel/sched/fair.c"
grep -Fq 'A52 uclamp/CASS efficiency: clamp-only wake placement' "$KERNEL/kernel/sched/cass.c"
grep -Fq 'unsigned long p_util = task_util_est(p);' "$KERNEL/kernel/sched/cass.c"
grep -Fq 'uclamp_eff_value(p, UCLAMP_MIN)' "$KERNEL/kernel/sched/cass.c"
grep -Fq 'uclamp_eff_value(p, UCLAMP_MAX)' "$KERNEL/kernel/sched/cass.c"
! grep -Fq 'unsigned long p_util = uclamp_task(p);' "$KERNEL/kernel/sched/cass.c"
grep -Fq 'U17 uclamp + WALT schedutil compatibility path active' "$KERNEL/kernel/sched/cpufreq_schedutil.c"
grep -Fq 'sysctl_sched_uclamp_util_min_rt_default = SCHED_CAPACITY_SCALE;' "$KERNEL/kernel/sched/core.c"

cat > artifacts/eevdf-uclamp-efficiency-audit.txt <<'EOF'
baseline_run=35199901520
baseline_commit=3de2b4cdb394a1c13c8fbfc68e8a081c8aa6a022
kernel=4.19.206
eevdf_base=phase77_lifecycle_correctness
eevdf_fix=explicit_vprot_plus_protected_period_resched
uclamp_base=android17_phase86
uclamp_fix=cass_raw_util_plus_explicit_clamp_without_schedtune_margin
walt_schedutil=schedtune_then_uclamp_clamp_retained
rt_uclamp_default=1024_retained
binder=unchanged_android17_6.18_r1
mglru=modern_p1_p2_p3
EOF

say "Build kernel"
./scripts/57_build_resukisu_susfs_manual_detection.sh 2>&1 | tee artifacts/eevdf-uclamp-efficiency-build.log

say "Post-build audit"
test -s artifacts/Image-touchgrass-4.19.206-resukisu-v4.1.0-susfs-v1.4.2-manual-core
test -s "$KERNEL/out/vmlinux"
grep -Fxq 'CONFIG_UCLAMP_TASK=y' "$KERNEL/out/.config"
grep -Fxq 'CONFIG_SCHED_TUNE=y' "$KERNEL/out/.config"
grep -Fxq 'CONFIG_SCHED_CASS=y' "$KERNEL/out/.config"
grep -Fq 'eevdf_protect_slice' "$KERNEL/out/vmlinux" || true

printf 'baseline_run=35199901520\nbaseline_commit=3de2b4cdb394a1c13c8fbfc68e8a081c8aa6a022\n' > artifacts/build-baseline.txt
printf 'build=success\n' >> artifacts/build-baseline.txt

say "Done"
