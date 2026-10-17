# Phase446S — GPU CX GDSC held by KGSL SMMU probe vote

Source provenance: user-provided comparison report A6, cross-checked by Claude against Phase446R log and pinned ACK 5.10 source. This note preserves the diagnosis and defines the source contracts to test on hardware.

## Observed

- The A52 DT node `arm,smmu-kgsl@3d40000` has the Phase250 legacy `qcom,regulator-names = "vdd"` and `vdd-supply` contract but lacks `power-domains`.
- Phase250 `250_phase249_gpu_smmu_power_contract_overlay.py` enables the SMMU regulator during probe and disables it on `arm_smmu_runtime_suspend` or removal. The original ACK 5.10 SMMU code only enables runtime PM when `dev->pm_domain` exists.
- The 446R log is reported to contain one CX GDSC enable at 1.093979 s, zero disables, and eleven `GMU CX gdsc off timeout` lines. These observations support a stuck SMMU vote, but framebuffer effects and suspend recovery still require a hardware test.

## 446S implementation

- `A52_KGSL_SMMU_RPM_V1`: enable runtime PM *only* when the SMMU matches both `smmu-kgsl@3d40000` and the Phase250 `vdd` DT supply.
- Hold a runtime PM usage reference throughout probe / bus registration. When complete, release through `pm_runtime_mark_last_busy` and `pm_runtime_put_autosuspend` with a 20 ms autosuspend policy.
- Existing ACK `arm_smmu_rpm_get/put` remains responsible for resuming/suspending register operations. Phase250 runtime resume enables regulators before clocks; runtime suspend disables clocks before regulator vote release.
- Log probe `dev->pm_domain`, `pm_runtime_enabled`, `power.usage_count`, and runtime status; runtime suspend/resume, system suspend/resume, remove and probe-error cleanup.
- Probe-error unwinds its acquired Phase250 supply and clocks. No regulator forcing is done during successful operation.
- The upstream context and global fault IRQ handlers can touch SMMU registers without RPM get. Added *observational* warnings if a fault IRQ fires while A52 runtime PM is suspended; IRQ behavior is otherwise unchanged. Other special register access paths still merit dynamic validation.
- Retain `A52_PHASE446S_GMU_SLUMBER_DIAG_V1` and `A52_PHASE446S_GPU_CX_VOTES_V1` logs. Do not shorten `CX_GDSC_TIMEOUT=5000` or bypass `gmu_core_dev_cx_is_on`.

## Hardware acceptance

1. After idle, `P446S KGSL_SMMU_RPM stage=runtime_suspend_vdd_off` appears with `usage=0`, and the CX GDSC provider logs a disable.
2. No `GMU CX gdsc off timeout` after the GPU becomes idle.
3. No new SMMU/IOMMU fault events or GPU hangs, and GPU maps/unmaps + subsequent power-on succeed.
4. Suspend proceeds past GMU; power button can resume; no memlat `-95` or timer crash.
5. Unsampled 300-commit trace determines whether the inferred black-frame alternation is genuine, and whether it goes away.

## Unchanged/forbidden

Preserve Phase446P packet builder, Phase446N REARM, 146/148/149, QSEECOM 19/27, dma_fence union, FENCE1C, U1 QUSB2, Phase305, Phase423, Phase438, FENCE1G and Phase446R GEM free. Keep CAL31 OFF, GDSC-recovery variant OFF and no dma-buf flags=0 importer fallback. Never force off the GPU CX domain or remove the DT SMMU supply.
