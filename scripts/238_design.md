# Phase 238 - Broad GPU supplier-chain recorder

## Starting point

Phase 237 hardware ruled out OF default population as the boot blocker. The recovered Phase 230 supplier replay instead shows KGSL repeatedly deferring because `3d9106c.qcom,gdsc` is not bound. The earlier GX supplier at `3d9100c.qcom,gdsc` is bound, GPUCC probes successfully, and the Phase 233 `A52GDSC GPU_CX_PROFILE_V1` marker is present in the compiled image.

The unresolved question is therefore narrower: why does the CX GDSC platform device remain unbound even though CX compatibility code exists in the image?

## Phase 238 goal

Record every plausible stage that can explain the missing `gpu_cx_gdsc` supplier while preserving the Phase 210 R48/RS48/CRC32C transport and all inherited graphics diagnostics.

Phase 238 is diagnostic only. It must not change GDSC, GPUCC, KGSL, DRM, or SurfaceFlinger behavior.

## Broad but focused coverage

### 1. Driver-core supplier gating

Instrument `really_probe()` for the GPU dependency family and dump managed supplier links immediately before `device_links_check_suppliers()`.

Focus includes:

- `3d9106c.qcom,gdsc` / `gpu_cx_gdsc`
- `3d9100c.qcom,gdsc` / `gpu_gx_gdsc`
- `3d90000.qcom,gpucc`
- `3d00000.qcom,kgsl-3d0`
- `780000.qfprom`
- CX-level RPMh/GPU/GDSC/QFPROM names reached through the same path

For every focused attempt record:

- candidate device
- candidate driver
- currently bound driver
- each supplier device name
- each supplier's currently bound driver
- device-link status and flags
- supplier-check return code
- final `really_probe()` return code and source line

This detects a hidden supplier that prevents the CX provider from reaching its platform probe.

### 2. Generic platform probe

Keep Phase 237 P3P tracing and add `G238 P` tracing that is not limited to the OF-population window.

Record:

- focused platform-probe entry
- device, driver and OF node
- clock-default stage
- PM-domain stage
- driver callback stage
- every `platform_drv_probe()` return code

This distinguishes no match / no platform callback from a provider callback that returns an error.

### 3. Phase 233 legacy GDSC provider

Instrument `drivers/regulator/a52-legacy-gdsc-regulator.c` directly for both CX and GX, with CX as the primary target.

At probe entry record:

- device name
- attached driver name
- `regulator-name`
- `compatible`
- whether the node is classified as CX
- MMIO resource start/end

Dump bounded DT information:

- every property name, up to 40 properties
- `qcom,clk-dis-wait-val`
- `qcom,gds-timeout`
- `qcom,no-status-check-on-disable`
- presence of `vdd_parent-supply`
- presence of both `hw-ctl-addr` and `hw-ctrl-addr`

Resolve and record phandles for:

- `vdd_parent-supply`
- `hw-ctl-addr`
- `hw-ctrl-addr`

For resolved phandles record the target node and, when represented by a platform device, its currently bound driver.

### 4. Provider call-site checkpoints

Before suspicious operations in the custom GDSC probe, emit a stage marker with operation and source line. Operations include:

- regulator acquisition
- MMIO resource lookup / ioremap
- regulator registration
- syscon / regmap resolution
- OF phandle/property parsing
- regulator enable / voltage / load
- clock enable
- readl / writel
- CX profile-selection branches where present

Every custom GDSC probe return is wrapped so the exact return code and source line are recorded.

### 5. Retention-safe late replay

The Phase 237 capture showed that early records can be lost from the 1 MiB ramoops window. Phase 238 therefore schedules a diagnostic replay at approximately 145 seconds.

The late replay records:

- number of CX platform-probe attempts
- last CX platform stage and return code
- last attached CX driver name
- current binding state for CX, GX, GPUCC, KGSL and QFPROM
- current supplier links for those devices
- number of CX custom-provider probe entries
- last custom-provider stage and return code
- remembered CX MMIO/property summary

The existing Phase 230 KGPPOST supplier replay is retained, and `KGPPOST*` is admitted by the Phase 238 recorder filter.

## Interpretation matrix

### No `G238 D in` and late replay finds CX with `drv=-`

The CX platform device exists but no driver reached `really_probe()`. Investigate match/attachment/driver-registration parity.

### `G238 D in` followed by `G238 D sup-out ... rc=-517`, with no `G238 P in`

CX is itself blocked by one of its device-link suppliers. The preceding `G238 D sup` records identify that supplier.

### `G238 P in` but no `G238 GD in`

Generic platform probing reached a driver callback path that is not the expected Phase 233 GDSC provider, or the callback path differs from the assumed provider function.

### `G238 GD in` followed by negative `G238 GD out`

The Phase 233 provider is matching CX but fails internally. The last `G238 GD st` stage plus return source line identifies the failing operation.

### `G238 GD out ... rc=0` but late replay still shows CX unbound

Investigate driver-core post-probe teardown or binding state changes after the provider returns successfully.

### CX binds in late replay but KGSL remains deferred

Use the retained Phase 230 KGPPOST chain to identify the next supplier after CX.

## Transport and behavior contract

- Phase 210 R48 / RS48 / CRC32C transport unchanged.
- OrangeFox R48 collector v3.2 remains compatible.
- No graphics-provider functional workaround is added.
- No SurfaceFlinger or userspace behavior is changed.
- Logging is bounded and GPU-focused to reduce unrelated recorder pressure.
- Late replay is diagnostic and does not modify device state.
