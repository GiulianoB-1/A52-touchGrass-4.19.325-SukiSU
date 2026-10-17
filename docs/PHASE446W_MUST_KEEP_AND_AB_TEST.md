# A52 5.10 Phase446W — MUST-KEEP and test protocol

Reference TG: micr0softstore/samsung_android_kernel_a52xq @ 6bf351bdf18bdb228db79e66f14a7a9c0178e5d7.
GKI: pinned android12-5.10-2026-04_r1 f960ed27302b1ff8e61e152fc202554d778deccd.

## MUST-KEEP across all future builds
- **CONFIRMED WORKING: Phase446V PMK8350 RTC.** The qcom,pmk8350-rtc matcher and two distinct register blocks (RTC 0x6140/0x6146/0x6148; alarm 0x6240/0x6246/0x6248) in drivers/rtc/rtc-pm8xxx.c, with CONFIG_RTC_DRV_PM8XXX=y, CONFIG_RTC_HCTOSYS=y and rtc0 selection. 446V registered the RTC, read it successfully and Android wall clock is correct. NEVER regress this backport.
- Phase446T KGSL/GPU SMMU hold and bounded GMU CX shutdown, plus Phase446S GPU power-vote fixes.
- Working display F0/DSI packet header order, ION dma-buf get_flags importer interoperability, DRM commit and GEM lifetime corrections.
- Phase305 + Phase423 RPMh solver contracts (bounded TCS wait and flush -EBUSY logging), Phase438 devbw semantics.
- PON/RESIN and PMIC gpio pinctrl mapping; vol up/down and power key.
- Memlat guard/PMU fixes.
- Source fingerprint, strict final-tree manifest and Image provenance. Never trust cached objects without the final source/config/DTB audit.
- Phase446U-v2 atomic request truth diagnostics and bounded USB handover; Phase446V read-state introspection.

## Phase446W A/B
- A (default/test): `a52.446w_cold=1 a52.446w_handoff=0`. The SDE *continuous-splash resource adoption* is skipped at hardware init; the bootloader's reserved splash memory is deliberately **not** memblock-freed. Expect a short blank and first normal modeset to cold-init DSI. This is an experiment, not a proven display fix until boot validation.
- B (experimental/disabled): `a52.446w_cold=0 a52.446w_handoff=1`. Requires incoming HWC atomic already joining CRTC107/connector32. It requests a modeset via existing TG DSI bridge cleanup. Does **not** synthesize a connector state or blindly toggle panel/encoder flags; if prerequisites absent, it defers. No hardware proof; do not enable for initial A/B.
- USB (test): Android boot header retains its original `console=null` token. With `CONFIG_CMDLINE_EXTEND=y`, the **kernel built-in** `CONFIG_CMDLINE` supplies `console=ttyGS3,115200`; the early g_serial function allocates port3 directly. U446 count/gate/teardown route only that line. ConfigFS line0 remains unallocated by the legacy function; whether Samsung smdexe and ADB both work must be checked on hardware. `a52.446w_tty=3`. Do not rewrite the bootloader `console=null` token or change only `console=` without moving actual function allocation.

## Runtime acceptance
1. Cold selection appears: `P446W SPLASH COLD_SELECTED=1`; FIRST_HWC snapshot has no DSI/SDE cont_splash and panel was initialized during initial modeset.
2. Zero-plane requests in P446S/446U logs are **zero** from first SurfaceFlinger/HWC commit through all initial FRAME_SUMMARY windows; manual off/on still works.
3. Retained `P446V RTC REGISTER registration_rc=0 first_read_rc=0` and sane time.
4. `P446W USB early legacy g_serial allocated ttyGS3`, U446 preflight/DEFER on port3 in printk; Android gadget takes the UDC and `adb devices` sees the device. USB is unproven until tested.
5. Compare full `P446W_FULL_POST_NORMAL_ON` snapshots with `FIRST_HWC_ATOMIC` and logs from 446V.
6. On failure revert to known-working 446V boot image; B must not be enabled automatically.

## Roadmap, **not** 446W
Battery 0%: Samsung sec_battery/SM5714 charger, fuel-gauge and MUIC integration is not yet ported. **No evidence yet that GKI can safely charge**; verify charge current/voltage with independent instrumentation before assuming. Prefer charging with confirmed TG behavior.
