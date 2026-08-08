# Phase 239: GPU CX vdd_parent parity

## Root cause

The Lagoon `gpu_cx_gdsc` node carries both `parent-supply = <&VDD_CX_LEVEL>` and `vdd_parent-supply = <&VDD_CX_LEVEL>`. Phase 233 preserved only the generic regulator-core `parent-supply` ordering. It did not acquire the separate `vdd_parent` consumer used by Qualcomm's downstream GDSC driver, nor did it place the LOW_SVS operational vote on that rail.

## Functional fix

- Keep `parent-supply` regulator-core ordering unchanged.
- For GPU CX only, acquire `devm_regulator_get(dev, "vdd_parent")` when `vdd_parent-supply` exists.
- Preserve `-EPROBE_DEFER` from that acquisition.
- Vote `RPMH_REGULATOR_LEVEL_LOW_SVS` before CX enable/register access.
- Unvote on enable failure and when CX is disabled.
- During `is_enabled()`, temporarily hold the parent regulator while reading the GDSCR, matching downstream Qualcomm behavior.
- Do not give GX, UFS or MDSS a `vdd_parent` handle, so their functional behavior is unchanged.

## Diagnostics retained

All Phase 238 G238/KGPPOST driver-core, platform-probe, CX supplier, pre-match driver-walk and 155-second replay diagnostics remain enabled. The Phase 210 R48/RS48/CRC32C persistent transport is unchanged.

## Hardware expectation

`3d9106c.qcom,gdsc` should bind to `a52-legacy-gdsc-regulator`. KGSL supplier checking should then advance past GPU CX. If another blocker exists, the retained Phase 238 diagnostics should identify the next exact dependency rather than losing the transition.
