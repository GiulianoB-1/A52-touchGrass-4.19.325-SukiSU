# Phase 205 audit scope

This phase is intentionally non-flashable and comparison-only.

It checks the current Phase 204 kernel and embedded A52 device tree against the exact TouchGrass reference before any later display recorder or functional patch is created. The gate covers display SMMU domain attributes, IOVA aperture, fault policy, secure VMID handling, direct SCM I/O, DRM component dependencies, DSI, RSCC, power, clock and panel source pairs.

A critical or high statically proven mismatch sets `hardware_test_recommended` to false. Such a mismatch must be corrected before adding instrumentation at a later boundary.
