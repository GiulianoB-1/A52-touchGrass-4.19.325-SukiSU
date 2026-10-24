# P459 apps-SMMU s2idle -EBUSY root-cause audit

**Status:** source cause **CONFIRMED**; currently active master runtime-PM reference counts **NOT YET CAPTURED** from the failing P458 device; no SMMU return semantics changed.

## Exact -EBUSY path

Mangrove `scripts/206_apply_smmu_display_contracts.py` `patch_smmu_c()` installs two fail-closed checks in generated `drivers/iommu/arm/arm-smmu/arm-smmu.c`:

```c
static int __maybe_unused arm_smmu_pm_suspend(struct device *dev)
{
    if (a52_apps_smmu_has_unmanaged_tbus(dev)) {
        dev_warn(dev,
            "system suspend blocked until qsmmuv500 TBU support is ported\n");
        return -EBUSY;
    }

    if (pm_runtime_suspended(dev))
        return 0;
    return arm_smmu_runtime_suspend(dev);
}
```

`arm_smmu_runtime_suspend()` also begins with `if (a52_apps_smmu_has_unmanaged_tbus(dev)) return -EBUSY;`, before clock or GDSC disable. **For system sleep, the direct `arm_smmu_pm_suspend` check returns -16 first**: no downstream RPM/GDSC or bus-vote error is reached on this path.

The helper returns **true based solely on live-OF topology**:
```c
static bool a52_apps_smmu_has_unmanaged_tbus(const struct device *dev)
{
    struct device_node *child;

    if (!a52_apps_smmu(dev))
        return false;
    for_each_available_child_of_node(dev->of_node, child) {
        if (of_device_is_compatible(child, "qcom,qsmmuv500-tbu")) {
            of_node_put(child);
            return true;
        }
    }
    return false;
}
```

The `a52_apps_smmu()` guard matches the parent `qcom,qsmmu-v500`. This helper **does not read master PM usage counts, memory traffic, bus votes, or any TBU status**. Consequently a currently idle master cannot make `-EBUSY` clear, and identifying active consumers is a *separate* question.

The original Android 5.10 system-suspend function has no `-EBUSY`; the P446S KGSL-focused runtime-PM instrumentation preserves the guard, while P446T's GPU-only cmdline gate does not change it. Qualcomm `arm-smmu-qcom.c` and the P457 `scm = !lagoon && available` probe path do not return this system-suspend error.

## DT-specified unmanaged TBUs (hardware topology, NOT currently active master list)

Pinned TG Lagoon reference: `arch/arm64/boot/dts/vendor/qcom/msm-arm-smmu-lagoon.dtsi` at 6bf351bd. All seven below carry compatible `qcom,qsmmuv500-tbu`. At least one present/available child makes the static guard true.

| DT TBU | Base address | SID range | Associated fabric/client indication |
|---|---|---|---|
| `anoc_1_tbu` | 0x15185000 | 0x000–0x3ff | ANOC/aggregator |
| `anoc_2_tbu` | 0x15189000 | 0x400–0x7ff | ANOC/aggregator |
| `mnoc_hf_0_tbu` | 0x1518d000 | 0x800–0xbff | MDP display path, `hlos1_vote_mmnoc_mmu_tbu_hf0_gdsc` |
| `mnoc_sf_0_tbu` | 0x15191000 | 0xc00–0xfff | CAMNOC SF path, `hlos1_vote_mmnoc_mmu_tbu_sf_gdsc` |
| `adsp_tbu` | 0x15195000 | 0x1000–0x13ff | ADSP/LPASS path |
| `compute_dsp_0_tbu` | 0x15199000 | 0x1400–0x17ff | Compute DSP/NPU master |
| `pcie_tbu` | 0x1519d000 | 0x1800–0x1bff | PCIe TBU |

**Critical distinction:** being a child in this table does NOT mean the master is currently active or holds a PM reference. `qcom,skip-init` on the apps-SMMU means firmware/bootloader-programmed context may be live. It is unsafe to power-collapse the parent/TBUs without an explicit ownership and state-retention contract.

## Runtime PM evidence still required

The 38-minute failing P458 suspend session and its on-phone `power/runtime_status`, `power/runtime_usage`, consumers and IOMMU-group data were **not supplied** in this source audit. The two previously provided P457 boot bundles are early boot captures; neither contains the reported SMMU suspend-abort event.

Use `diagnostics/p459_apps_smmu_pm_snapshot.sh` on the actual failing device to capture:

- The `15000000.apps-smmu` parent's `power/runtime_status` and `power/runtime_usage` (if kernel sysfs exports the latter), plus the KGSL and display/USB/ADSP/NPU/PCIe consumer statuses.
- Sysfs device links, IOMMU group/master membership and available TBU children, separated from any claims of active votes.
- Existing kernel PM abort warnings and power-state traces.

The Phase206 source also intentionally leaves runtime PM disabled for apps-SMMU when unmanaged TBUs exist, so `runtime_usage` may be absent or not an applicable indication of this guard. Do not invent usage counts or infer a master is responsible for a static topology veto.

## Root-cause conclusion and safe remediation criterion

- **Direct cause:** Phase206 `arm_smmu_pm_suspend` early guard, not ADSP, P446S RPM, or downstream bus vote. The repeated abort occurs whenever driver-core system suspend reaches the apps-SMMU callback while at least one available unmanaged TBU child exists.
- **Physical hazard avoided:** loss of live SMMU/TBU translation configuration when the clock and regulator trees are powered down or restarted.
- **Do not change:** P457 byte-identical `arm-smmu-qcom.c` SCM guard; Phase97 UFS ICE bypass; nine frozen SCM callers; manual ADSP and H0 policies.
- **To make s2idle genuinely complete:** implement and validate the QSMMUv500 TBU power/ownership path **or** an explicit, instrumented SMMU+TBU retention policy that verifies clocks, GDSC and interconnect votes remain safe throughout the sleep and resume sequence. Only then replace the Phase206 unconditional veto with a guard whose success is tied to proven hardware state, NOT `return 0` just because the device looks idle.
- **Acceptance:** several s2idle transitions with no `-16`, no TBU/SMMU faults or stale mappings, clean dwc3/qusb resumes and stable IOMMU translation; only afterward repeat manual ADSP start across real sleep.

### Verification provenance

- `scripts/206_apply_smmu_display_contracts.py`, inspected via connected GitHub at P458/P459 branch.
- `scripts/446s_apply_kgsl_smmu_runtime_pm.py`, audited its PM callback edits.
- `scripts/446t_apply_smmu_hold_no_nested.py.z64`, decoded in successful read-only CI run #40133357547; no `-EBUSY` or SMMU sleep-return additions found.
- Complete script-generator inventory run #40133450218, which isolated the Phase206 guard.
- Pinned TG 6bf351bd Lagoon SMMU DT source for seven child TBUs.
