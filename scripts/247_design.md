# Phase 247 - Lagoon CAMCC dense `clk_hws` compatibility correction

## Hardware evidence entering Phase 247

Authoritative Phase210+ R48/RS48 fusion decode of `A52_RAW_RAMOOPS_20260810_104624.zip` shows the current Phase246 boot reaching subsys initcall level 4 and advancing through 82 pre-call records. The final current-boot record is:

```text
CXF246 S n=81 f=cam_cc_lagoon_init
```

at approximately 616 ms. There is no current `n=82`, no `CXF246 X`, and no current `A52GDSC` registration/probe record before this boundary. The much later high-sequence KGSL/CX block is retained stale data and is not treated as current-boot continuation.

## Static TouchGrass vs GKI finding

TouchGrass Lagoon CAMCC defines:

```c
struct clk_hw *cam_cc_lagoon_hws[] = {
    [CAM_CC_PLL2_OUT_EARLY] = &cam_cc_pll2_out_early.hw,
};
```

`CAM_CC_PLL2_OUT_EARLY` is binding ID 6, so this is a seven-element sparse array with NULL indices 0-5.

TouchGrass `qcom_cc_really_probe()` explicitly skips NULL hardware-clock entries before `devm_clk_hw_register()`.

The Phase54 4.19->5.10 compatibility port copied Lagoon CAMCC and renamed descriptor fields `hwclks` -> `clk_hws` and `num_hwclks` -> `num_clk_hws`, while preserving the sparse designated index.

Exact GKI 5.10 `qcom_cc_really_probe()` treats `clk_hws` as a dense registration list and calls `devm_clk_hw_register(dev, clk_hws[i])` for every entry without a NULL skip. In exact f960 common-clock code, a NULL `clk_hw` proceeds through `clk_hw_register()` into `__clk_register()`, which dereferences `hw->init`.

This provides a deterministic crash candidate inside `cam_cc_lagoon_init()`/probe registration and matches the Phase246 stopping corridor.

## Phase 247 change

Phase247 changes only the generated Lagoon CAMCC auxiliary hardware-clock array:

```c
struct clk_hw *cam_cc_lagoon_hws[] = {
    &cam_cc_pll2_out_early.hw,
};
```

The GKI descriptor remains:

```c
.clk_hws = cam_cc_lagoon_hws,
.num_clk_hws = ARRAY_SIZE(cam_cc_lagoon_hws),
```

Thus GKI registers exactly one valid auxiliary `clk_hw` instead of iterating six leading NULL entries.

## Explicitly unchanged

- Phase245 `FW_DEVLINK_FLAGS_PERMISSIVE` functional state
- Phase246 `CXF246` subsys initcall recorder
- Phase243 CX/GX supplier and provider hooks
- Phase244 remains skipped
- no global `drivers/clk/qcom/common.c` change
- no CAMCC `devm_regulator_get()` restoration
- no CAMCC VDD-class/rate-max restoration
- no `cal_l`, Fabia, Agera, or PLL configuration change
- no DT change
- no boot cmdline change
- no R48/RS48 recorder transport change

## Decisive hardware result

If the static root-cause candidate is correct, Phase247 should advance beyond:

```text
CXF246 S n=81 f=cam_cc_lagoon_init
```

and produce `CXF246 S n=82 ...` or later records. If `n=81` remains the last current record, the next investigation stays inside CAMCC, with the remaining static differences - regulator/VDD handling and PLL/Fabia calibration behavior - prioritized before adding broader instrumentation.
