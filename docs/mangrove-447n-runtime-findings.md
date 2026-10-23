# Mangrove Kernel — TG comparison and 447N targeted capture (23 October 2026)

## Runtime findings (from user-provided capture summaries)

- **447K display proven:** Linux 5.10.252-Mangrove, successful CRTC vblank enable, ~8.2 ms vsync intervals; visible flicker gone. No need to change display scheduling yet.
- **Touch parity:** per-device getevent capabilities for sec_touchscreen, sec_touchpad, proximity and keys match TouchGrass. Both have identical 0x3e custom axis declaration and release order. Normal event reporting is not proven faulty.
- **Remaining touch crash:** occurred near 84 and 116s after power-on during Android startup/display transition. The old 447I 48-action buffer fills by ~18s, too early to observe it.
- **USB:** two attempts at composite bind failed with rc=-19; 155s retry produced COMPOSITE, UDC_START and CONNECT success. Failure happened before UDC start; cause *may* be FunctionFS readiness, but not proven.
- **Frame performance:** a 15.6 fps scrolling result used different app content from TG's 98.8 fps; cannot attribute this difference to kernel without the same app, gesture, surface and collector timing.

## New 447N instrumentation

- Record up to 200 STM parsed coordinate actions only during 60–140s boottime, including prior action, slot, touch count and power state.
- Always report fts_release_all_finger, KEY_INT_CANCEL and input open/close and fts start/stop transitions **within the same boot window**.
- Add ConfigFS binding-stage logging of function name and rc for each usb_add_function, plus composite prepare, string descriptor and config validation stages; preserve earlier 447L UDC/PM records.
- No input event mutation, forced device mode, USB power-vote changes, SD/card changes, GPU/display change, or charger writes.

## Follow-up collection

Use diagnostics collector v1.14 with matched scrolling of the *same Android app list* on TG and Mangrove. Compare frame summary and interrupts in a comparable window. For touch crash, capture during 60–140s, and include display lifecycle plus P447N touch trace; do not intentionally stress touch during unstable boot unless rollback and logs are ready. For USB, record retries and which function returns rc=-19.

**Charging safety:** SM5714 gauge/thermistor and existing emergency guard are present, but full Samsung charger policy/JEITA/termination is still missing. Charge on the known-good TouchGrass kernel or recovery, not Mangrove.
