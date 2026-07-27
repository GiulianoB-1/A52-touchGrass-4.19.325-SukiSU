# A52 ACK display parity against working TouchGrass

Working reference: TouchGrass commit `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`.
Failing reference: audited ACK 5.10 Run 32 source plus the current QSEECOM heap and failure-window stages.

## Source parity

- Seven active display source files were compared between TouchGrass `techpack/display/msm` and ACK `drivers/a52_display/msm`.
- Twenty-one critical DSI, panel, SDE CRTC, encoder and KMS functions were compared.
- Nineteen functions were logically identical after removing recorder-only instrumentation and formatting.
- `dsi_clk_manager.c` was byte-for-byte identical.
- The expression `(DSI_LINK_LP_CLK & DSI_LINK_HS_CLK)` exists in both the working TouchGrass source and the failing ACK port, so it is not a standalone explanation for the black screen.
- `sde_crtc_atomic_flush` differs only by initializing `plane` to `NULL` in ACK.
- `sde_kms_complete_commit` differs in its power-resource test: TouchGrass tests `< 0`, while ACK tests logical false. This remains a review item but is not yet proven as the failure trigger.

## Device-tree parity

- Nineteen matching display-related DT/DTSI files were found.
- All nineteen were normalized-identical.
- No active display property differences were found in those matching files.
- Therefore the copied display source DT definitions themselves are not the main divergence.

## Configuration lead

The configuration comparison found several TouchGrass-only display-related options. The most concrete active dependency is:

- TouchGrass: `CONFIG_REGULATOR_REFGEN=y`
- ACK: option absent

The stock boot DTB used by the candidate contains an enabled `qcom,refgen-kona-regulator` provider and the active DSI controller consumes `refgen-supply`. The ACK port has the DT node but currently lacks the corresponding built-in REFGEN regulator driver/configuration.

This is the leading fix candidate because it is an active DSI power dependency, unlike many unrelated generic DRM panel and backlight configuration differences.

## Current plan

1. Port the exact TouchGrass REFGEN regulator implementation into the ACK 5.10 tree with only compatibility adaptations.
2. Enable it built-in.
3. Add recorder events for REFGEN probe, enable, disable and status reads.
4. Keep the proven QSEECOM heaps 19/27, dma-buf bridge, secure gate, heartbeat and watchdog-disarm stages unchanged.
5. Compile, audit and hardware-test the resulting boot image.

No REFGEN build has been produced or hardware-tested yet.
