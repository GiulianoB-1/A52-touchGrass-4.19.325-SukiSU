# Mangrove Phase 450 DAILY — diagnostic candidate

Built on the independently validated P448 E IPC/GLINK baseline and P449 F
charger NTC/mixed-temperature one-way inhibit. Does not port the charger or
remoteproc. Before flashing retain a known-good TG charging kernel.

## Exact design
- TG FG SRAM 0x05/0x09 for current_now and current_avg in uA.
- TG FG AUX_STAT 0x22 bit 10 multiplies signed current by 25/10.
- TG FG SYSTEM_STATUS 0x10 bit 15 forces current to zero.
- The current readings do not yet implement TG flag_chg_status policy and
  must NOT be used as proof of actual charging.
- CHG_CNTL1 0x13 bit3 (ENQ4FET) only clears, never sets.
- Charger STATUS1 0x0d, STATUS2 0x0e, STATUS3 0x0f logged as P450 CHG_STATE.
  TG STATUS2 bit3 charging, bit5 topoff.
- Startup missing Tchg sensor may wait <=10 seconds ONLY if CNTL1 bit3=0
  AND STATUS2 bit3=0, no battery-overtemp, with VBUS known present.
  All other unreadable/active conditions retain immediate inhibit.
- No charger/MUIC, no PAS ADSP/CDSP/MSS, wave default 2, TG charging only.

## Diagnostics to collect
1. P448 IPC ipcc / smem / smp2p x3; P448 GLINK/QRTR remote channels absent.
2. P449 CHG_GUARD startup rule and sensor registration.
3. P450 CHG_STATE, P450 FG current_ua and avg_ua, AUX_STAT and SYSTEM_STATUS.
4. Collect same-boot +20s and +120s and power-supply sysfs evidence.
Expected power-supply count = 2, not 7/9. Charger present is not verified
battery charging. A successful CI compile does not prove hardware safety.

## Separate development branches
DSP: exclusive AOSS QMP hardware ownership, packaged native AOSS DTB,
SM6350 ADSP PAS descriptor, reserved memory/IRQ/proxy domains, firmware,
GLINK and safe SSR. CHG: sm5714 MFD and charger_oper, conservative sec_battery,
MUIC cable tables, WDT, 500mA ramp, validated thermal and termination.
Do NOT publish DSP or CHG or DSP-CHG until their actual dependencies exist.
