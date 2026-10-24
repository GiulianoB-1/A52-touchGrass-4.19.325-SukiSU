# Mangrove Kernel 447M: implementation scope and test checklist

Basis: A52 GKI status after 447K, 22 October 2026.

## Confirmed by user, still awaiting objective log proof
447K resolved visible flicker and feels smoother. Collector v1.13 is still needed to check DRM composer errors, FLICKER_RING and about 120 vblank IRQ per second.

## Included in this diagnostic revision
- Synchronize Kconfig-generated auto.conf before checking Mangrove kernelrelease, fixing the failing branded CI.
- Retain the 447J vblank diagnostic and working 447K CRTC callbacks.
- 447I bounded STM finger lifecycle trace (48 parsed coordinate actions before Android reports).
- 447L read-only UDC stage and Qualcomm DWC3 suspend/resume instrumentation.
- Preserve charger guard, battery thermistor and fuelgauge behavior. No charger/USB/SD hardware-control changes.

## Pending behavior-changing work
- Full SM5714 charger driver, Samsung battery/JEITA thermal policy, current limiting, termination, MUIC/PDIC/PCA9468. Current emergency inhibitor alone is NOT verified safe charging. Use known-good TouchGrass or recovery to charge.
- Touch finger-up event-stream repair and DeX touchpad suppression pending paired TG/GKI evdev caps/event traces.
- USB role / wakelock / QUSB regulator changes pending proof of failing UDC stage and VBUS source of truth.
- SDHCI msm for 8804000.sdhci and GPIO94 card detect pending DT/config parity.
- Modem firmware path, DSP, audio, wireless, camera ports in later waves.

## Next capture
Use collector v1.13 for TG and Mangrove Kernel. Avoid touch during Mangrove boot. Scroll during a manual collector run. Compare P447I TOUCH LIFE, P447J and P447K VBLANK, P447L USB, FLICKER_RING, input_caps.txt, touch_events.txt and usb_state.txt. Record cable inserted before/after boot, whether PC detected it and whether it failed after screen sleep.

The attached report references a detailed 447I work order but does not contain sufficient source anchors/DT wiring or safe charger policy to implement it purely from the summary. Do not guess charger register writes.
