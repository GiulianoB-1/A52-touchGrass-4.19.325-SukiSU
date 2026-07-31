# Phase 191 hardware result

## Evidence integrity

The 2026-07-31 capture preserved identical early and later 1 MiB RAMOOPS snapshots. Both have SHA-256 `e6bea10f71d9435ff4441e8a6d48cd85df0fcd2d85c7ce0f5479f0580e3d05f9`.

The exact phase 191 Reed-Solomon decoder recovered 810 records. `BOOT_READY` was reached and the kernel heartbeat continued through 62.496 seconds, so this remained a live black-screen boot rather than a kernel crash.

## Component result

The active SDE `connectors` list contains:

1. `qcom,wb-display@0`
2. `qcom,dsi-display-primary`
3. `qcom,sde_rscc`
4. `qcom,dp_display@ae90000`

DisplayPort is intentionally skipped when `CONFIG_SEC_DISPLAYPORT` is disabled. The component master therefore has three match slots.

At master registration:

```text
COMP slot i=0 found=1 dev=soc:qcom,wb-display@0 bound=0 dup=0
COMP slot i=1 found=0 dev=- bound=0 dup=0
COMP slot i=2 found=0 dev=- bound=0 dup=0
```

After primary DSI successfully probes and calls `component_add()`:

```text
COMP slot i=1 found=1 dev=soc:qcom,dsi-display-primary bound=0 dup=0
COMP slot i=2 found=0 dev=- bound=0 dup=0
```

The only missing component is slot 2, `qcom,sde_rscc`, compatible `qcom,sde-rsc`.

## Consequence

Because RSCC never registers as a component, the component framework never invokes the DRM master bind callback. KMS initialization, DSI bridge attach, panel prepare/enable, backlight enable and atomic display commits do not run.

## Phase 192 scope

Instrument `drivers/a52_display/msm/sde_rsc.c` only:

- RPMh and main RSCC driver registration
- DT node and platform-device presence
- RPMh child probe
- every main probe stage and exact failure code
- cleanup
- `component_add()`
- component bind

Do not force a retry, change an errno, bypass RSCC, remove it from the component list, or modify display hardware behavior yet.
