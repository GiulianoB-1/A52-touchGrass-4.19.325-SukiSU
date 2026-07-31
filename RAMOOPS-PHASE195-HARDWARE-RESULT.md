# Phase 195 hardware result

The raw 1 MiB RAMOOPS snapshot and early frozen copy are identical. SHA-256:

`cbadd5317354536d98ae63c332a3f50707b74861060a320b5273a99e8a2cadee`

The exact phase 195 decoder recovered 702 records, sequences 65 through 766.

Key result:

```text
KMSPOST splash rc=0 regions=1 displays=1
KMSPOST pm-get exit rc=0
KMSPOST blocks exit rc=-19 crtc=0 enc=0 conn=0 plane=0
KMSPOST fail stage=blocks rc=-19
```

DSI controller, PHY, clock manager, MIPI host, panel driver and Samsung
`ss_panel_init()` completed before this point. RSCC also bound successfully.

`-19` is `-ENODEV`. The failure is inside `_sde_kms_hw_init_blocks()` before
any DRM CRTC, encoder, connector or plane is created. The subsequent
`mdss_core_gdsc` collapse is error cleanup, not the root cause.

Phase 196 adds observation-only checkpoints around every hardware-block and
SMMU/splash-map substep to identify the exact source of `-ENODEV`.
