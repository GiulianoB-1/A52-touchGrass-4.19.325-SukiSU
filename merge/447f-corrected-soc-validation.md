# Phase447F SOC comparison — corrected evidence and implementation boundary

## Correction (2026-10-22)

The GKI `51%` reading and the TouchGrass `57%` reading were BOTH real. The device **charged in recovery between the captures**. There was no controlled constant-SOC comparison. The earlier inference of `capacity_max=920` from `51%/57%` was WRONG; this ratio cannot be used to calibrate Android battery SOC. The already working 447E battery display must remain unchanged.

## Source-grounded TG capacity math

- Pinned TG commit `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`:
  `sm5714_get_soc()` computes raw *0.1%* from the SM5714 SRAM SOC word with `(word * 10) >> 8`.
- Its `sm5714_fg_get_scaled_capacity()` uses `(raw_deci - capacity_min) * 1000 / (capacity_max - capacity_min)` where nominal A52 R02 DT `capacity_max=1000`, `capacity_min=0`, `capacity_max_margin=300`, and `capacity_calculation_type=0x1f`.
- Dynamic max updates, atomic one-percentage-point smoothing and skip-abnormal behavior depend on runtime battery and charging state, including `is_charging`, `initial_update_of_soc` and `capacity_old`. **Do not enable these blindly** without the actual charger/MUIC/sec_battery port.

## Implemented 447F correction

`scripts/447f_soc_scaling.py` no longer injects an arbitrary 920 correction. It reads the nominal capacity_max/min/type from the running `sm5714-fuelgauge` DT node (or uses the verified A52 R02 nominal 1000/0 if unavailable). It computes and logs a read-only TouchGrass-style candidate SOC while continuing to return the proven 447E GKI measured capacity unchanged. The `sm5714-fuelgauge` supplier is the unmodified raw FG; the Android-facing `battery` remains correctly registered on `samsung_mobile_device:battery`.

Diagnostic: `P447F SOC raw_deci=... TG_max=... TG_min=... candidate_pct=... ui_preserved=... source=DT_no_charge_comparison_pending`.

No learned data, calibration, charger current, OTP or PDIC firmware is written. The estimate remains **comparison-only**, not a claim of complete production `sec_battery` capacity policy parity.

## Correct paired validation protocol (GKI ↔ TG)

1. First capture GKI `battery/capacity`, `sm5714-fuelgauge/capacity`, `voltage_now`, `current_now` where exposed, `status`, `usb/online`, `ac/online`, `dumpsys battery`, full timestamp and `P447F SOC` diagnostics. Do so at the same elapsed time after boot as the TG sample.
2. Switch kernels **without charging in recovery**. No external USB/AC charging in between; on an ADB capture, verify the attached host or recovery session has not started charging. Do not assume this because the cable is connected for data.
3. Capture TouchGrass immediately with the identical fields and timestamps; confirm `Discharging` or otherwise prove absence of charging both during recovery and capture. If charging occurs, **discard** the pair instead of calibrating using it.
4. Repeat at least three valid back-to-back no-charge A/B pairs across SOC levels, ideally on the same hardware and similar temperature. Reverse test order on some pairs to reveal systematic discharge/time bias. Account for unavoidable SOC drift and 0.1%-to-integer rounding.
5. Compare raw FG values and scaled battery percentages separately. A difference of ±1% is consistent with integer rounding; significant repeatable errors require tracing Samsung's actual dynamic max, atomic and charge-state history before changing reported SOC.
6. Pass requires `battery` reported correctly, `sm5714-fuelgauge` under correct TG sysfs path/SELinux label, and working SE8 touchscreen. Cable detection and all nine supplies remain separate unported Wave2 tasks.

**No 920 gain is to be hardcoded, inferred, or flashed.**
