# Phase 194 hardware result

## Visible behavior

The Samsung boot logo remained visible and frozen. The display no longer became black after roughly 20 seconds.

## Capture integrity

- Raw RAMOOPS size: 1,048,576 bytes.
- Early and exported snapshots were identical.
- SHA-256: `58f999824ff8442e726aa86e98bd57905c4230a4a4f7c68876dcb36beb7d68b0`.
- Decoder recovered 706 records, sequence 17 through 755.
- Last recovered timestamp: 1113.392 ms.

## Confirmed progress

Phase 194 resolved the RSCC supplier gate:

- `mdss_core_gdsc` bound to `a52-legacy-gdsc-regulator`.
- RSCC supplier check returned zero.
- `sde_rsc_probe()` completed.
- RSCC `component_add()` returned zero.
- DRM component assembly reached `msm_drm_bind()`.
- DSI controller, PHY, clock manager and host bind succeeded.
- `dsi_panel_drv_init()` and Samsung `ss_panel_init()` succeeded.
- RSCC bind succeeded.
- `sde_kms_init()` and `_sde_kms_hw_init_blocks()` returned.

## Final recovered events

```text
749 1108.662 ms DISP enter fn=a52.life.sde_kms_hw_init
750 1109.051 ms DISP enter fn=a52.life._sde_kms_hw_init_blocks
751 1112.610 ms DISP exit fn=a52.life._sde_kms_hw_init_blocks
752 1112.741 ms A52GDSC mode profile=mdss name=mdss_core_gdsc mode=1 rc=0
753 1113.006 ms A52GDSC disable profile=mdss name=mdss_core_gdsc rc=0
754 1113.271 ms DISP exit fn=a52.life.sde_kms_hw_init
755 1113.392 ms DISP exit fn=a52.life._msm_drm_init_helper
```

## Current interpretation

The successful path in TouchGrass collapses MDP resources at this point only when the continuous-splash display count is zero. The physical logo can remain in the AMOLED panel frame memory even after MDP resources are dropped, explaining the new stuck-logo behavior.

This is not yet sufficient justification to force continuous splash or keep the GDSC enabled. The exact post-KMS boundary must be traced first.
