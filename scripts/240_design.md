# Phase 240: GPU CX supplier-gate frozen latch

## Hardware evidence entering Phase 240

The Phase 239 hardware capture boots with the expected GPU-CX `vdd_parent`
functional parity, but `3d9106c.qcom,gdsc` remains unbound and KGSL still
defers with `-EPROBE_DEFER` on that supplier.

The capture does **not** retain `A52GDSC CX_VDD_PARENT_GET_V1` or an exact
`G238 GD` CX provider-entry record.  More importantly, the initial CX
`__device_attach()` entry/exit are consecutive retained records while no
`cxw cand`/match record exists between them.  That proves the initial device
attach happened before a currently registered matching GDSC driver was walked.

The decisive later driver-registration / driver-side attach attempt can occur
inside the mid-boot ramoops retention hole, so Phase 240 freezes that path
rather than changing GDSC behavior again.

## Diagnostic change

Phase 240 keeps the complete Phase 239 functional stack and adds one dedicated,
append-only CX latch:

- Capacity: 96 records; older entries are never overwritten.
- Replay: heartbeat ticks 155 and 170.
- Exact custom-driver registration walk:
  - `CXF240 drvwalk-in`
  - `CXF240 drvwalk-out`
- Exact `3d9106c.qcom,gdsc` / `a52-legacy-gdsc-regulator` driver-side pair:
  - `CXF240 drv-match`
  - `CXF240 drv-probe`
- Exact supplier gate immediately around the existing
  `device_links_check_suppliers()` call:
  - `CXF240 sup-in`
  - one `CXF240 sup ...` line per supplier link
  - `CXF240 sup-out`
- Selected existing CX evidence (`A52GDSC`, `G238`, `KGPPOST cxw`) is copied
  into the same first-event latch so the provider transition survives the
  retention hole.

## Guardrails

Phase 240 is diagnostic-only:

- no device link is added, removed, modified, or bypassed;
- no driver match result is rewritten;
- no probe return is rewritten;
- no deferred-probe decision is rewritten;
- no driver registration order or initcall level is changed;
- no GDSC functional behavior is changed from Phase 239;
- the Phase 210 R48/RS48/CRC32C transport is unchanged.

## Questions the next hardware capture must answer

1. Does `a52-legacy-gdsc-regulator` actually run its driver registration/attach
   walk after the CX device already exists?
2. Does that driver-side walk visit and positively match `3d9106c.qcom,gdsc`?
3. If it reaches `really_probe()`, what exact supplier links exist on CX?
4. Does `device_links_check_suppliers()` return `-EPROBE_DEFER`, and if so,
   which preceding supplier record is unavailable/unbound?
5. If the supplier gate returns 0, does the later platform/GDSC probe execute,
   and what exact return code prevents CX from binding?

A Phase 240 capture is evidence-gathering only.  A functional bypass or repair
must be based on the identified failing boundary rather than assumed from DT
text or from KGSL's secondary defer.
