# A52XQ Hybrid GKI 5.10 Plan: Superseded

The 5.10 route was rejected after the device and source audit.

Android Common Kernel 5.10 does not contain the native SM6350/Lagoon platform chain required for A52XQ bring-up. Backporting the later SoC DTS, clock, pinctrl, interconnect and related drivers would be a larger and less maintainable task than beginning from Linux 6.1, where this support already exists.

The active plan is:

```text
docs/HYBRID_GKI_6_1_PLAN.md
```

The active experimental branch is:

```text
hybrid-gki-6.1-experiment
```

The original 5.10 branch is retained only as a record of the initial investigation. It must not be used for new builds or device testing.
