# P460 retain-mode pre-build gate: NO-GO

**Status (2026-10-25): NO FLASHABLE P460 RETAIN IMAGE AUTHORIZED.** This report is source-only. P458 and P459 code are untouched; Phase206's default `-EBUSY` veto remains operative. No sysfs knob, new PM callback, or firmware was built.

## A. Pinned TouchGrass (TG) 4.19.206 SMMU suspend/resume

Pinned reference: `micr0softstore/samsung_android_kernel_a52xq` at `6bf351bd`.

- [TG arm-smmu.c L5827-L5853](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/drivers/iommu/arm-smmu.c#L5827-L5853) explicitly registers `SIMPLE_DEV_PM_OPS(arm_smmu_pm_ops, NULL, arm_smmu_pm_resume)`: **system suspend callback is NULL**. The system-resume callback calls `arm_smmu_power_on(smmu->pwr)`, `arm_smmu_device_reset(smmu)`, and `arm_smmu_power_off(smmu->pwr)`, returning the power-on error when that operation fails. This is not a no-op resume.
- [TG arm-smmu.c L7146-L7152](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/drivers/iommu/arm-smmu.c#L7146-L7152): `qsmmuv500_tbu_driver` has **no .pm callbacks**. Its resource lifetime is still independent, through `tbu->pwr` initialized by `arm_smmu_init_power_resources(pdev)` in [L7073-L7143](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/drivers/iommu/arm-smmu.c#L7073-L7143).
- [TG arm-smmu.c L1213-L1407](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/drivers/iommu/arm-smmu.c#L1213-L1407): SMMU power-on/off are more than clock-enable operations; `arm_smmu_power_on_slow` requests an interconnect/bus path and enables regulators, while `arm_smmu_power_off_slow` removes those votes. So TG behavior does **not** prove that Linux parent clock votes alone retain the TCU/TBUs.
- For the `qcom,qsmmu-v500` apps parent, [TG Lagoon DT L39-L49](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/arch/arm64/boot/dts/vendor/qcom/msm-arm-smmu-lagoon.dtsi#L39-L49) sets `qcom,skip-init`, which adds firmware/bootloader-owned translation-state concerns.

## B. Can subordinate power domains collapse independently of apps-SMMU clock votes?

**YES, this is a credible, source-supported possibility, and sufficient to FAIL the user-specified go/no-go gate.** This is a power-domain/topology capability, **not a claim that a collapse was measured in the P458 logs**.

- [TG Lagoon DT L195-L235](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/arch/arm64/boot/dts/vendor/qcom/msm-arm-smmu-lagoon.dtsi#L195-L235) gives `mnoc_hf_0_tbu` (display) its own `vdd-supply = <&hlos1_vote_mmnoc_mmu_tbu_hf0_gdsc>` and `mnoc_sf_0_tbu` (camera) its own `vdd-supply = <&hlos1_vote_mmnoc_mmu_tbu_sf_gdsc>`. These are not simply the apps parent clock vote.
- [TG arm-smmu.c L1219-L1407](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/drivers/iommu/arm-smmu.c#L1219-L1407) shows per-device regulator and bus vote acquisition/release, including a separate `tbu->pwr` on/off around TBU register access ([L6116-L6118 and L6251-L6258](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/drivers/iommu/arm-smmu.c#L6116-L6118)).
- [TG Lagoon DT L129-L145 and L155-L300](https://github.com/micr0softstore/samsung_android_kernel_a52xq/blob/6bf351bd/arch/arm64/boot/dts/vendor/qcom/msm-arm-smmu-lagoon.dtsi#L129-L300): bus votes are `qcom,msm-bus,active-only`; parent and TBUs use distinct vote clients. There is no proven s2idle always-on contract for the entire TCU/TBU path on the P458 5.10 kernel.
- In P458 the Phase206 unmanaged-TBU handling *disables apps-SMMU runtime PM* but does not port TG's seven child TBU resources. A PM callback that does nothing cannot independently prevent firmware or other owners from dropping GDSC/rail votes. Linux clock votes do not establish guaranteed powered translation context across s2idle.

**Critical secondary mismatch:** TG **does reset the SMMU on system resume**. Proposed P460 skips this reset. No source comparison or hardware dump establishes that all active translation registers survive sleep.

**Decision:** per work order, if any relevant GDSC/rail can collapse, P460 `apps_smmu_s2idle_retain=1` **must not ship**. Therefore do not add the proposed sysfs knob yet and do not publish an Image advertising functional retain mode.

## P458 combined-image audit (pinned variant G)

Checked at `df52c9bda2d68dc15e46e8c2ea78031ee30d39dc`, workflow [`446wx-build.yml`](https://github.com/GiulianoB-1/A526B-Mangrove-Kernel/blob/df52c9bda2d68dc15e46e8c2ea78031ee30d39dc/.github/workflows/446wx-build.yml), [phase chain](https://github.com/GiulianoB-1/A526B-Mangrove-Kernel/blob/df52c9bda2d68dc15e46e8c2ea78031ee30d39dc/scripts/452_apply_phase_chain.sh).

| Requested component | What the P458 G build currently provides |
|---|---|
| Default apps-SMMU suspend veto | Present and unchanged. Keep it. |
| Root 0/1 retain knob | **Not added**, blocked by independent TBU rail-collapse risk. |
| Charger guard 2 s poll | **Not met for H0 variant G:** [H0 generator](https://github.com/GiulianoB-1/A526B-Mangrove-Kernel/blob/df52c9bda2d68dc15e46e8c2ea78031ee30d39dc/scripts/452_h0_sm5714_conservative.py) rewrites the original `A52G_GUARD_POLL_MS 2000` worker to **10 s active / 30 s inactive**. [P457 gate](https://github.com/GiulianoB-1/A526B-Mangrove-Kernel/blob/df52c9bda2d68dc15e46e8c2ea78031ee30d39dc/scripts/457_source_safety_gates.py) checks for the same 10 s/30 s conditional, notwithstanding its misleading nearby comment about 2 s when inactive. |
| ECC48 pstore | [P458 ECC48 DTB step](https://github.com/GiulianoB-1/A526B-Mangrove-Kernel/blob/df52c9bda2d68dc15e46e8c2ea78031ee30d39dc/scripts/458_pstore_ecc48_dtb.py) is present, CI checks `ecc-size=48`; runtime pstore registration is unverified. |
| P453 USB QUSB regulator/tune resume fix | **Not merged into G:** [phase chain](https://github.com/GiulianoB-1/A526B-Mangrove-Kernel/blob/df52c9bda2d68dc15e46e8c2ea78031ee30d39dc/scripts/452_apply_phase_chain.sh#L220-L227) applies `453_daily_usb_resume.py` and VBUS only when `A52_VARIANT_ID=F`. P458 image variant is G. Earlier U1 QUSB support exists but is not the requested P453 balanced-regulator fix. |
| NPU marker/freeze | [P458 generator](https://github.com/GiulianoB-1/A526B-Mangrove-Kernel/blob/df52c9bda2d68dc15e46e8c2ea78031ee30d39dc/scripts/458_dsp_runtime_scm.py) includes legacy `9800000.qcom,npu` and `soc:qcom,smp2p-npu` freezes/markers. |
| P457 `arm-smmu-qcom.c` | P458 [source gate](https://github.com/GiulianoB-1/A526B-Mangrove-Kernel/blob/df52c9bda2d68dc15e46e8c2ea78031ee30d39dc/scripts/457_source_safety_gates.py) preserves the pinned P457 scope. |

**Release gate:** Do NOT imply P458 G has 2 s H0 protection or the P453 USB regulator patch. Correct those independently, with compilation + runtime evidence, only after a separate appropriate work order. This NO-GO report is not permission to enable charging or force USB power/role.

## What would unblock retain mode

Demonstrate, on the actual 5.10 build and *all seven* TBU resources, that a full s2idle cycle retains their required power/clock/bus and translation state; or implement and validate an explicit TBU/TCU suspend/resume ownership and reinitialization contract that safely handles domain collapse. Capture actual domain and fault state before/after real sleep. Only when this is proven should a conservative, root-restricted knob be considered, with default veto untouched and full return-path rollback.

**No flashable output, no runtime measurements, no change to kernel PM return semantics.**
