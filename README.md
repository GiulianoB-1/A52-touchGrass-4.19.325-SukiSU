<p align="center">
  <img src="assets/mangrove-kernel-banner.webp" alt="Mangrove Kernel — 2D mangrove forest illustration" width="760">
</p>

# Mangrove Kernel

**An Android 16 kernel bring-up project for the Samsung Galaxy A52 5G (SM-A526B / a52xq), based on GKI 5.10.**

Mangrove Kernel is an ongoing port of the Galaxy A52 5G's Qualcomm and Samsung downstream platform support from **TouchGrass Linux 4.19** to the **Android 12 GKI 5.10 kernel branch**, targeting the UN1CA Android 16 ROM. The goal is to retain the device-specific hardware behavior while moving to a newer Linux kernel foundation.

> **Experimental project.** The phone boots and the display now operates without the previously observed flicker, but charging control and other hardware subsystems remain incomplete. Do not use this kernel as a daily-driver or for unattended charging.

## Current bring-up status (22 October 2026)

| Subsystem | Status |
| --- | --- |
| Boot / Android userspace | Boots on the A52 5G with the Android 16 port |
| Display / GPU | Display and GPU working; 447K fixed the recurring vblank error and flicker in user testing; formal runtime audit ongoing |
| Touch | Basic touch works; touching during Android boot can crash `system_server` |
| Battery measurements | Fuel-gauge capacity, temperature sensors, VBUS detection, and an emergency charging guard are present |
| Charging | **Not safe for normal charging yet:** complete charger driver, Samsung policy, current limits, and full-charge behavior remain unported |
| USB data | ADB / MTP intermittent; investigating legacy `g_serial` handover and suspend/resume PHY faults |
| RTC / power keys | Functional |
| SD card | Pending SDHCI driver bring-up |
| Modem, audio, Wi-Fi, Bluetooth, sensors, camera | Porting and validation still in progress |

### Active priorities

1. Complete and validate charger + thermal safety (Phase 447I).
2. Diagnose the touch input sequence that can restart Android during boot.
3. Repair intermittent USB gadget enumeration and USB PHY suspend/resume.
4. Continue modem/DSP, audio, sensors, Wi-Fi/Bluetooth, camera, and remaining platform bring-up.

## Kernel identity

The development name is **Mangrove Kernel**. Builds from the branding change append `-Mangrove` to the kernel release (for example, `5.10.x-Mangrove`) so it is visible through:

```sh
uname -r
cat /proc/version
```

The exact numeric Linux version is determined by the pinned build source. Historical phase numbers, `GKI` filenames, and TouchGrass references may remain in internal scripts and logs for reproducibility; they are not the current project name.

## Source and build

- **Active development branch:** [`agent/a52-phase447-big-merge-off-v1`](https://github.com/GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/tree/agent/a52-phase447-big-merge-off-v1)
- **Current test builds:** [`446wx-build.yml`](https://github.com/GiulianoB-1/A52-touchGrass-4.19.325-SukiSU/actions/workflows/446wx-build.yml)
- **GKI base:** Android common `android12-5.10-2026-04_r1`, pinned SHA `f960ed27302b1ff8e61e152fc202554d778deccd`.
- **TouchGrass hardware reference:** `micr0softstore/samsung_android_kernel_a52xq` at `6bf351bd`.
- **Build validation:** CI source anchor checks, strict precompile final-tree audit, per-variant configuration and Image checks, and boot package auditing.
- **A/B/C artifacts:** A = battery-focused Wave 2; B = Wave 3 without MSS; C = Wave 3 with MSS/IPA. They are independently compiled and are all experimental.

The repository retains legacy TouchGrass and 4.19.325 work for historical provenance, but **the current goal is the 5.10 GKI migration**.

## Safety and testing

**Charge on a known-good TouchGrass kernel or recovery until the charger state machine, current limits, thermal zones, and termination are validated on Mangrove Kernel.** Keep a known-good rollback boot image. Do not touch the screen during boot until the input crash is resolved.

Use the current A52 diagnostics collector to compare Mangrove Kernel and TouchGrass runtime behavior, particularly display vsync, touch events, USB state, and battery temperature.

## Development principles

Pin upstream and reference revisions; preserve known-good fixes; make compatibility changes through audited, reproducible source transformations; and isolate risky subsystem changes into separately testable phases.

The nickname **Mangrove Kernel** is distinct from upstream Google GKI, Linux, and the TouchGrass reference implementation.
