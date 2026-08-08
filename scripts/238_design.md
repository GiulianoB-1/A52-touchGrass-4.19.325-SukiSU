# Phase 238 - Broad GPU supplier-chain recorder

## Starting point

Phase 237 hardware ruled out OF default population as the boot blocker. The recovered Phase 230 supplier replay instead shows KGSL repeatedly deferring because `3d9106c.qcom,gdsc` is not bound. The earlier GX supplier at `3d9100c.qcom,gdsc` is bound, GPUCC probes successfully, and the Phase 233 `A52GDSC GPU_CX_PROFILE_V1` marker is present in the compiled image.

The unresolved question is therefore narrower: why does the CX GDSC platform device remain unbound even though CX compatibility code exists in the image?

## First Phase 238 hardware result

Capture `A52_RAW_RAMOOPS_20260808_091140.zip`, decoded with the Phase 210 R48 transport-fusion decoder, confirms the KGSL dependency progression.

At approximately 448 ms, `3d90000.qcom,gpucc` passes `device_links_check_suppliers()` with `rc=0` and its CXLVL RPMh supplier already has a bound driver. Therefore a globally unavailable CXLVL RPMh provider is not the explanation for the missing CX binding.

The retained Phase 230 late journal at approximately 154.9 s shows:

1. An early KGSL attempt stops on unbound QFPROM.
2. On a later retry QFPROM is bound.
3. `3d9100c.qcom,gdsc` / GPU GX is bound.
4. The next supplier is `3d9106c.qcom,gdsc` / GPU CX, with no bound driver and link status zero.
5. `device_links_check_suppliers()` immediately returns `-EPROBE_DEFER` after reaching CX.

The Phase 230 journal prints device-link flags with `%x`, so `fl=160` is hexadecimal `0x160`, not decimal 160. In this 5.10 device-link layout that link includes `AUTOPROBE_CONSUMER`, `MANAGED`, and `INFERRED`. The current first unavailable managed KGSL supplier is therefore conclusively the CX GDSC itself.

The same capture exposed a retention issue in Phase 238 diagnostics: the recorder retains records through roughly 0.556 s and resumes around 148.369 s, while the original G238 replay was scheduled at 145 s. That replay fell inside the missing-record window.

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

### 2. Exact CX path in the inherited late journal

The first Phase 238 capture proved the normal early G238 records can be lost even when the later Phase 230 KGPPOST journal survives. Phase 238 now extends the already-proven Phase 230 journal selection to the exact CX device/driver pair:

- device `3d9106c.qcom,gdsc`
- driver `a52-legacy-gdsc-regulator`

The extension records and later replays the CX platform match/attach path, `really_probe()` path, `device_links_check_suppliers()` entry/result, device-link suppliers, and fwnode suppliers when supplier checking runs.

The extension changes only trace selection. Matching, supplier decisions, deferred-probe behavior, callback order, and return values remain untouched.

### 3. Generic platform probe

Keep Phase 237 P3P tracing and add `G238 P` tracing that is not limited to the OF-population window.

Record:

- focused platform-probe entry
- device, driver and OF node
- clock-default stage
- PM-domain stage
- driver callback stage
- every `platform_drv_probe()` return code

This distinguishes no match / no platform callback from a provider callback that returns an error.

### 4. Phase 233 legacy GDSC provider

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
- presence of TouchGrass `parent-supply`
- presence of both `hw-ctl-addr` and `hw-ctrl-addr`

Resolve and record phandles for:

- `parent-supply`
- `vdd_parent-supply` as a diagnostic control spelling only
- `hw-ctl-addr`
- `hw-ctrl-addr`

For resolved phandles record the target node and, when represented by a platform device, its currently bound driver.

The original Phase 238 logger accidentally used `vdd_parent-supply` for its `parent=` summary. A post-overlay diagnostic repair now makes `parent-supply` authoritative while retaining one `vdd_parent-supply` control trace. No provider behavior is changed.

### 5. Provider call-site checkpoints

Before suspicious operations in the custom GDSC probe, emit a stage marker with operation and source line. Operations include:

- regulator acquisition / registration
- MMIO resource lookup / ioremap
- syscon / regmap resolution
- OF phandle/property parsing
- regulator enable / voltage / load where present
- clock enable where present
- readl / writel
- CX profile-selection branches where present

Every custom GDSC probe return is wrapped so the exact return code and source line are recorded.

### 6. Retention-safe late replay

The first Phase 238 hardware capture showed the 145 s replay itself landed inside the observed recorder hole. A post-overlay timing repair now changes only the three Phase 238 delayed-work timers from 145000 ms to 155000 ms.

The 155 s late replay records:

- number of CX platform-probe attempts
- last CX platform stage and return code
- last attached CX driver name
- current binding state for CX, GX, GPUCC, KGSL and QFPROM
- current supplier links for those devices
- number of CX custom-provider probe entries
- last custom-provider stage and return code
- remembered CX MMIO/property summary

The existing Phase 230 KGPPOST replay remains at its established heartbeat window and now also retains the exact CX path described above.

## Interpretation matrix

### KGPPOST shows CX match but CX supplier check returns `-517`

CX itself is blocked before the platform callback. The preceding CX `fw n=` or `dl s=` records identify the supplier edge responsible.

### KGPPOST shows CX match and supplier check succeeds, but no `G238 P in`

Investigate the driver-core path after supplier gating and before the platform callback.

### No CX KGPPOST match/attach records and late replay finds CX with `drv=-`

The CX platform device exists but the expected driver did not reach the normal match/attach path. Investigate driver registration or platform matching.

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
- The CX journal extension changes trace selection only.
- The 155 s replay is diagnostic and does not modify device state.
