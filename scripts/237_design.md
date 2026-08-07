# Phase 237: OF population / platform-probe flight recorder

## Why this phase exists

Phase 235 hardware proved that the recorder is alive at about 268 ms but the boot does not reach DRM component-master or RSCC instrumentation. Phase 236 added MSM DRM registration/probe checkpoints, but the stronger boundary is the boot initcall progression itself: the failing boot reaches `BOOT phase=arch` and does not reach the later initcall milestones.

In the pinned Android 5.10 base, `of_platform_default_populate_init()` is registered with `arch_initcall_sync()`. It runs after ordinary arch initcalls and creates platform devices from the device tree. Creating a device can synchronously invoke a platform driver's probe. An earlier Phase 186 capture was able to progress through this area and later reach `BOOT phase=device`, so the hypothesis is not that OF population is universally broken. The useful question is which platform device/driver, if any, stops returning during the current population window.

## Scope

Phase 237 is instrumentation only. It does not intentionally alter device-tree data, driver matching, probe return codes, clocks, power domains, display behavior, RSCC behavior, or the R48 transport.

The recorder transport remains Phase 210 R48 / shortened RS(255,207) with 48 parity bytes / CRC32C / triple copies.

Phase 237 changes the runtime identity to:

`BOOT rs=ready phase=237 focus=ofpop-probe roots=%u copies=3 crc=crc32c`

The recorder admits these prefixes:

- `OFPOP`
- `P3P`
- inherited `BOOT phase=` and boot-control records
- inherited `DISPINIT`
- inherited `DRMCOMP`, `COMP `, and `RSCC`

## OF population checkpoints

`drivers/of/platform.c` receives an activity gate that is true only while `of_platform_default_populate_init()` is executing.

Expected records, in order when boot is healthy:

1. `OFPOP enter`
2. `OFPOP links-paused`
3. `OFPOP reserved begin`
4. zero or more `OFPOP reserved node=<name>`
5. `OFPOP reserved end`
6. `OFPOP firmware begin`
7. `OFPOP firmware end`
8. `OFPOP root begin`
9. `OFPOP root end`
10. `OFPOP exit rc=0`

The gate is enabled before `device_links_supplier_sync_state_pause()` so a stall at that call is also visible.

## Platform probe checkpoints

While the OF-population gate is active, `drivers/base/platform.c::platform_drv_probe()` emits bounded `P3P` records. At most 192 platform-probe instances are traced, keeping the worst-case recorder consumption bounded.

For probe `n`:

- `P3P enter n=<n> dev=<device> drv=<driver>`: platform probe dispatch has started; `of_clk_set_defaults()` is next.
- `P3P pd n=<n> drv=<driver>`: clock defaults returned successfully; `dev_pm_domain_attach()` is next.
- `P3P call n=<n> drv=<driver>`: PM-domain attach returned successfully and the driver's own `probe()` is about to run.
- `P3P exit n=<n> rc=<rc>`: the platform probe returned.
- `P3P exit n=<n> stage=clk rc=<rc>`: clock-default setup returned an error before PM-domain attach.
- `P3P limit n=193`: trace cap was reached.

This means the last record for a probe is diagnostic:

- `enter` without `pd`: stuck in `of_clk_set_defaults()`.
- `pd` without `call` or `exit`: stuck in `dev_pm_domain_attach()`.
- `call` without `exit`: the named driver's own `probe()` did not return.
- `exit`: that probe completed and is not the blocking probe.

## Interpretation matrix

- `BOOT phase=arch` but no `OFPOP enter`: execution stopped in a remaining ordinary arch initcall before the arch-sync OF population initcall.
- `OFPOP enter` but no `OFPOP links-paused`: stall in `device_links_supplier_sync_state_pause()`.
- OFPOP reserved markers stop: inspect the last reserved node and last `P3P` record.
- `OFPOP firmware begin` without `firmware end`: a device/probe below `/firmware` did not return.
- `OFPOP root begin` without `root end`: a root DT platform device/probe did not return. The last incomplete `P3P n=` identifies the exact boundary.
- `OFPOP exit rc=0` but no `BOOT phase=subsys`: the fault is after OF population and before the subsys heartbeat, so the next phase should trace the remaining arch-sync/subsys initcall boundary instead.
- `P3P limit n=193` appears before the stall: the trace cap is too low for the culprit and should be adjusted in a follow-up phase, rather than guessing a driver.

## TouchGrass comparison rule

After Phase 237 identifies a concrete non-returning driver or platform-framework stage, compare only that exact path against TouchGrass commit `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`. Do not change DT `connectors` or display topology unless execution later reaches the DRM component-master evidence that justifies it.
