# Phase 235 - RSCC DRM component-master recorder

## Goal

Resolve the boundary after successful `sde_rsc_probe()` and `component_add()` by recording the DRM component-master topology that decides whether `sde_rsc_bind()` can run.

TouchGrass builds the SDE DRM master match list from the MDP `connectors` phandles. The critical question is whether the hybrid tree includes the `qcom,sde-rsc` node in that list and whether the component core finds and binds the corresponding component.

## Transport

Phase 235 does not change the recorder wire format. It retains the proven Phase 210 R48 transport, 255-byte physical records, RS(255,207) with 48 parity symbols, CRC32C, triple-copy behavior, and record/ftrace transport fusion used by the existing decoder and OrangeFox collector v3.2.

## Persisted event classes

The Phase 234 RSCC filter is widened only to these bounded classes:

- `RSCC*` - exact RSCC driver match, probe stages, component registration and bind.
- `DRMCOMP*` - MDP `connectors` property, connector enumeration, `component_match_add()`, and DRM master registration.
- `COMP *` - the inherited Phase 191 component-core trace, already limited to SDE/DSI/MDP-related devices.
- `BOOT ctl=*` and `BOOT rs=ready*` - recorder control and boot identity.

The generic `RSCCCORE` candidate-driver flood remains forbidden.

## Interpretation

1. No RSCC node in `DRMCOMP connector` records: DT/component topology mismatch.
2. RSCC connector is present but no corresponding `COMP slot`/component: component registration or compare mismatch.
3. Match slots are populated but master bring-up does not bind: another display component blocks the master.
4. `RSCC bind enter` and `RSCC bind exit rc=0` occur: RSCC is no longer the root cause and debugging should move downstream into DRM/SDE/KGSL/userspace graphics startup.

## Build gates

The Phase 235 package is rejected unless the final Image contains the Phase 235 boot identity, focused RSCC markers, `DRMCOMP` connector/master markers, bounded `COMP` master/slot/component markers, and the inherited graphics-provider markers. It is also rejected if the old Phase 234 boot identity or generic `RSCCCORE` match markers remain.
