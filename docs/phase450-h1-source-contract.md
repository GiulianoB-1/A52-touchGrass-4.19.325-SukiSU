# Phase 450 H1 charger port source contract (safety-critical)

Pinned TouchGrass 4.19 ref: 6bf351bd, source:
- drivers/battery/charger/sm5714_charger/sm5714_charger.c
- drivers/battery/charger/sm5714_charger/sm5714_charger_oper.c
- drivers/battery/common/sec_battery.c
- include/linux/mfd/sm/sm5714/sm5714-private.h
- Golden TG active.dtb and ROM power-supply baseline

## Must-be-real requirements
1. SM5714 MFD child owns real charger I2C client at 0x49; the existing
   read-only FG uses secondary 0x71. Do not register two drivers for the
   same device or export duplicate battery power_supply nodes.
2. H1 sec_battery's `battery,charger_name` must map to a real available
   power_supply. TG live DT targets `sec-direct-charger` but that is
   H2 fast-charge policy; H1 requires a validated
   `sm5714-charger` binding or a fully implemented pass-through.
   Never relabel a fake supply as connected.
3. TG `chg_set_enq4fet(enable)`: CNTL1 0x13 bit3,
   1=enable, 0=disable; on enable, if prior VBUS limit exceeds 500 mA:
   lower to 500 mA, delay `(old_mA-500)/250` milliseconds,
   set bit3, then restore limit; clamp/check limits and serialize with
   `charger_mutex`. Do not enable unless cable source and temperature
   policy are valid and thermal guard grants charging.
4. On charger start TG checks STATUS2 0x0E WDT-expired bit7, resets
   WDTCNTL, programs 90-s watchdog, sets enable, and kicks watchdog.
   `sm5714_charger_oper` is a REQUIRED op-mode event state machine;
   `chg_set_enq4fet` alone is insufficient. Disabled operation must
   never kick/re-enable charging.
5. Important TG source hazard: `sm5714_charger_probe` contains Q3
   vendor option-bit writes to registers 0xEA/0xED/0xE4/0xCB and others
   around TG line 1303-1333. These are NOT approved to copy into
   experimental H1; add an explicit audited allowlist for safe
   operational charger register writes, and reject option/OTP/NV paths.
6. Full conservative 5V sec_battery state machine: charging status,
   health, battery NTC + charger NTC, low/high thresholds, swelling,
   cable-info current groups, input/current caps, full termination,
   recharge timers, thermal fail-closed and watchdog.
7. MUIC cable detection + VBUS session notifications must be integrated
   without losing USB gadget ttyGS3/ADB, and without giving two drivers
   ownership of the USB role.
8. H1 output supplies target 7: ac, battery, otg,
   sm5714-charger, sm5714-fuelgauge, usb, wireless.
   Do not fabricate unused supplies to satisfy a count.
   H2 later adds sec-direct-charger and pca9468-charger.

## Gated artifacts
- DAILY is a diagnostic baseline; it does not charge a battery.
- CHG must not be published until actual source/config/DTB/policy
  integration passes CI, then supervised PC-USB and plain 5V tests.
- DSP is separate: native AOSS-compatible mailbox owned exclusively by
  its one driver, SM6350 PAS ADSP descriptor plus clocks/power/reserved
  memory, firmware, glink and safe SSR.
- DSP-CHG only after CHG and DSP pass their separate runtime criteria.

## Thermal, log and test acceptance
Required P449 CHARGER PROBE, P449 SEC_BATTERY POLICY, P449 MUIC CABLE,
P450 CHG_STATE and P450 FG current_ua with STATUS2/3. Abort charging
test immediately for high temp, missing sensors or protective cutoff.
Returning from sensor NOT_READY to ready must not automatically enable
hardware charging. Keep TG kernel as the known-good charging path.
