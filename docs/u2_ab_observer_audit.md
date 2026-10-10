# U2/AB legacy diagnostic MMIO audit — 2026-10-10

Reference U2 branch: `agent/a52-phaseu2-gki-adb-acm-handover-v1` (2294de43).
A/B branch: `agent/a52-phaseu2-observer-quiet-ab-v1`.
The U1 and U2 workflows replay the same Phase430–446 diagnostic stack; U2 additionally installs only USB ConfigFS handover/ACM patches.

## Demonstrably scheduled observers in the replayed code

| Observer | Driver/location | Trigger/frequency | Read characteristics | Quiet-A/B |
|---|---|---|---|---|
| P446 `p446_worker` | `drivers/a52_display/msm/a52_phase445.c` | Starts at init; 10 ms until 10 s, then 1 s | Calls `p446_emit`: raw GCC, DISPCC, REFGEN and, when gated safe, MDP and DSI MMIO | Disabled |
| P446 `p446_emit` | same | Called also on many event marks | Raw MMIO on **every mark**, even when periodic worker is inactive | Disabled |
| P446D `p446_burst_threadfn` | same | Triggered near display bind; every ~1 ms over 120 ms | Repeated `a52_p446_mark` → `p446_emit` MMIO | Disabled |
| P385 `a52_r385_observer_fn` | `block/blk-mq.c` | 230 ms–2 s relative to old frontier trigger | Software block-queue state; not demonstrated to do raw MMIO | Disabled |
| P385 `a52_r385_usb_observer_fn` | `drivers/usb/gadget/udc/core.c` | 230/300/425/750 ms after frontier trigger | USB gadget pointer/config state; not raw MMIO | Disabled |
| P430 `a52_p430_sampler_fn` | `drivers/a52_secure/a52_ack_secure_flight_recorder.c` | Polls SF startup and samples tasks | Includes `a52_p430_ufs_compact` in affected lineage (inspect generated source) | Disabled if present |
| P431 `a52_p431_exec_fn` | same | Executes in SF-relative window | CPU counter busy-loop witness, not MMIO itself | Disabled if present |

## Event-triggered (not periodic) diagnostic MMIO

| Observer | Driver/location | Trigger | Quiet-A/B |
|---|---|---|---|
| P446i `a52_p446i_sw_entry`, `a52_p446i_hw_pre`, `a52_p446i_hw_post` | `a52_phase445.c` | Actual first F0 command | Disabled |
| P439 DSI autopsy | `dsi_ctrl.c`, `dsi_ctrl_hw_cmn.c` | DSI diagnostic instrumentation; the `a52_p439_f0` active variant is off in U2 | Not fully gated |
| P444/P445 deep passive snapshots and recovery ladder | `a52_phase445.c` and display/secure recorder | First-commit window | Not fully gated |
| P446 event calls outside `p446_worker` | multiple display callbacks | Binding, power, IRQ, SMMU | Safe because `p446_emit` is gated |

## Retired sampler requiring effective-source verification

The previous Phase380 **raw UFS MMIO** observer was retired by Phase403:
`scripts/403_apply_retire_phase380_ufs_sampler.py` removes the call to
`a52_r380_start_sampler(hba)`. Its historical target-time table remains
in source, but is not proof of execution. The A/B helper **fails** if an
invocation of that exact start function survives in the effective
`drivers/scsi/ufs/ufshcd.c`.

## Scope and limitations

This is an intentionally conservative screening experiment, **not a proven exhaustive list of every legacy injected MMIO access** in the reconstructed Phase429 baseline. The Phase2xx–Phase4xx layers include many callbacks and one-shot diagnostics. UFS, DSI and USB **functional** MMIO stays enabled by design. Some instrumented MMIO at normal first F0 (P444/P445, P439) is not suppressed.

To resolve completeness, inspect the generated kernel source at the exact CI build root (not just the patch scripts), including older injected sampler entry points. Do not claim an observer to be fully disabled until its source and link object have been checked. The CI A/B report distinguishes installed guards from absent optional function names. The A/B test and breadcrumbs should be tested in separate builds to avoid timing interference.

Recommended test sequence: capture 3 boot-to-stall times each for unmodified U2 and A/B, using PuTTY, with same cable/power; note whether first F0 or Android configfs UDC bind was reached. A/B is **not** hardware-proven until GitHub CI succeeds and a flash test is performed.
