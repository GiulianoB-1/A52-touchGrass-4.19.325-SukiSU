# Phase 450 four-variant acceptance gate — NO-GO pending genuine drivers
Date: 2026-10-23. Source: confirmed P449 F captures, TG 6bf351bd,
Golden TG FDR + active DTB (not published), AOSP pinned android12-5.10.

## Current evidence
- P449 F boots: P448 IPC ipcc=PROBED irq18, SMEM hwlock3 version=c0000,
  SMP2P remote IDs 1/2/5 and TG SMEM pairs.
- P449 CHG_GUARD at 2.082s sees Tchg=-10000, VBUS=1, logical latch=0,
  so inhibits on unknown sensor; CNTL1 remains 0x64 (bit3 clear).
  At 4.099s Tbat/Tchg=329/357 deciC, but latch=1.
- TG charger chg_set_enq4fet(false) clears CNTL1 bit3. The no-op is expected:
  the *hardware* enable bit is already 0. TG STATUS2 bit3 shows Charging ON,
  STATUS2 bit5 is Topoff. Do not infer charge from VBUS or SOC.
- F's only supplies: battery + sm5714-fuelgauge. Golden TG lists 9:
  ac, battery, otg, pca9468-charger, sec-direct-charger, sm5714-charger,
  sm5714-fuelgauge, usb, wireless.

## Immutable four-artifact contract — release only after all four exist
| Artifact | Genuine content | Required runtime marks | Supply target |
|---|---|---|---|
| MANGROVE-450-DAILY | current E/F baseline + guarded startup, SM5714 STATUS1/2/3 and raw FG current | P448 IPC (IPCC, SMEM, 3x SMP2P); P449 CHG_GUARD; P450 CHG_STATE, P450 FG current_ua | 2 |
| MANGROVE-450-DSP | DAILY + exclusive native AOSS QMP + SM6350 ADSP PAS, actual firmware start + GLINK | DAILY + P448 AOSS probe, P448 AOSS LOAD_STATE name=adsp, P448 FW name=adsp, P448 ADSP remoteproc=running, P448 GLINK REGISTER, P448 QRTR IPCRTR | 2 |
| MANGROVE-450-CHG | DAILY + SM5714 main charger MFD child, sec_battery thermal/cable/current/full/recharge policy + MUIC | DAILY + P449 CHARGER PROBE, P449 SEC_BATTERY POLICY, P449 MUIC CABLE, P450 CHG_STATE status and current | 7 (target: TG non-direct supplies) |
| MANGROVE-450-DSP-CHG | Actual union of DSP and H1 charger, independently auditable | all DAILY + DSP + CHG marks | 7 |

7-supply H1 target explicitly excludes pca9468-charger and
sec-direct-charger; full nine-supply parity belongs to later H2 and must
not be faked. The numbers are acceptance targets, not current observations.

## Hard blockers
- **DSP NO-GO:** 5.10 qcom_q6v5_pas.c lacks SM6350 PAS descriptor;
  packaged DTB has qcom,qmp-mbox at c300000 instead of the upstream
  AOSS binding; still need exclusive mailbox ownership, exact TG live
  AOSS load_state power domains, ADSP reserved memory/IRQ/supply map,
  safe firmware/pas boot, fail-closed SSR and real GLINK edge registration.
- **CHG NO-GO:** codebase has only read-only FG bridge and CHG_GUARD;
  no TG SM5714 charger MFD child/charger_oper, no full sec_battery
  state machine, no MUIC cable detection, no verified thermal zones/
  charging current/termination/recharge/watchdog policy; 2 supplies.
- **DSP-CHG NO-GO:** combining incomplete code increases thermal and
  reboot risk, especially if remoteproc faults during charger validation.
- Runtime evidence is necessary. Compilation alone NEVER certifies charge
  control, remoteproc startup, AOSS domain transitions or charging safety.

## P450 DAILY diagnostics staged separately (not a flash release)
scripts/450_daily_fg_and_guard_diag.py adds:
- Read-only current SRAM 0x05/0x09, signed uA, no claim of true charging
  state without vendor AUX_STATUS gate and charger state machine.
- P450 CHG_STATE logging CNTL1, STATUS1/2/3, enable bit polarity
  and charge / top-off flags after inhibit and periodically.
- First 10-second *logical-latch* grace only when CNTL1 bit3 is
  physically OFF and STATUS2 bit3 confirms charging OFF; any
  uncertainty enforces existing one-way inhibit. Never enables
  hardware charging, no automatic resumption.
- Actual recovery hysteresis 55→48C, mix 42/50→39C belongs to the
  complete H1 policy, not temporary inhibit-only code.

This commit is a staging/source-audit deliverable ONLY.
No push to the CI-triggered branch, no four-variant artifacts and
NO instruction to flash DSP-CHG until both genuine ports are complete.
