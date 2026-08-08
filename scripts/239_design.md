# Phase 239: GPU CX `vdd_parent` parity repair

## Proven boundary

Phase 238 hardware reaches userspace but KGSL remains deferred on the exact
managed supplier `3d9106c.qcom,gdsc` (`gpu_cx_gdsc`). The sibling GX GDSC at
`3d9100c` binds and Lagoon GPUCC also binds.

## Static parity defect

Pinned TouchGrass `gdsc-regulator.c` treats two DT properties independently:

- `parent-supply` supplies regulator-core parent ordering.
- `vdd_parent-supply` causes acquisition of consumer supply `vdd_parent` and
  parent-rail voltage handling around GDSC register/state-machine access.

Phase 233 implemented the first contract for GPU CX but omitted the second.
The Lagoon GPU CX node carries both contracts; GX does not require the missing
`vdd_parent` path.

## Phase 239 change

Only the exact Phase 233 GPU CX compatibility profile is changed:

- Preserve `parent-supply -> init_data->supply_regulator = "parent"`.
- If `vdd_parent-supply` exists, acquire `devm_regulator_get(..., "vdd_parent")`.
- Propagate acquisition errors, including `-EPROBE_DEFER`, exactly rather than
  hiding supplier readiness.
- Use the exact TouchGrass `RPMH_REGULATOR_LEVEL_LOW_SVS` value (`64`) for the
  parent vote.
- Guard CX `is_enabled()` GDSCR access with parent enabled-state, LOW_SVS vote,
  temporary parent enable, and cleanup.
- Before CX software enable, place the LOW_SVS vote; retain a successful vote
  until CX disable, matching pinned TouchGrass.
- Clear the parent vote on enable failure and on CX disable.

## Guardrails

- UFS, MDSS and GPU GX GDSC behavior is unchanged.
- No device link is deleted or bypassed.
- No `-EPROBE_DEFER` is rewritten.
- No KGSL return code is forced.
- GPUCC, IOMMU, ZAP authentication and display behavior are unchanged.
- Phase 238 CX driver-walk, supplier, probe and 155-second replay diagnostics
  remain enabled.
- Phase 210 R48/RS48/CRC32C recorder transport is unchanged.

## Expected hardware result

`3d9106c.qcom,gdsc` should reach `a52-legacy-gdsc-regulator`, acquire the
`vdd_parent` CX-level regulator, register `gpu_cx_gdsc`, and become a bound KGSL
supplier. KGSL should then progress past the repeated `-EPROBE_DEFER` boundary.
Any later failure is downstream of this now-restored TouchGrass provider
contract and remains visible through retained Phase 238 instrumentation.
