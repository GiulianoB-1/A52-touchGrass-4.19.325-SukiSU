# P452 DSP-CHG H0 -- deliberately experimental

## Two artifacts
- MANGROVE-452-DAILY: unchanged charging-disabled P451 read-only kernel,
  expected two power supplies. AOSS/ADSP remain absent in DAILY.
- MANGROVE-452-DSP-CHG: DAILY + native AOSS, /firmware/scm,
  SM6350 PAS **auto_boot = false**, and opt-in SM5714 **H0** charger
  inside the existing 0x49 FG owner. No duplicate charger I2C owner.

## H0 safety contract (TG 6bf351bd)
- The P449 emergency inhibit runs first; H0 explicitly clears ENQ4FET
  at startup even if the thermistors were ready before probe.
- No automatic charging. H0 arm defaults OFF; kill switch is one-way
  until reboot and writes ENQ4FET OFF immediately.
- Hardware writes confined to CHGCNTL2 **address 0x18** value **0x20**
  (= 500mA charge current), WDTCNTL **0x22**, and CNTL1 **0x13 bit3**.
  Distinguish the 0x20 *value* from a register address.
- WDT 90 seconds: WDTCNTL timer bits [2:1]=2; enable bit0; kick bit3
  before setting ENQ4FET=1; kick every 30 seconds while active; read
  back watchdog and stop if drift/expiry is observed.
- Physical battery ADC NTC must read 15-41C; independent charger
  NTC must read below 50C. No FG thermistor fallback can permit charge.
- Absolute 14,400-second session cutoff, STATUS2 topoff -> FULL;
  recharge only at or below 4310mV if still healthy and intentionally
  armed, without bypassing the 4-hour cutoff.
- Read, do not write BATREG. BATREG 0x1a low six bits must be 0x27
  (4380mV according to TG formula).
- Read, do not write VBUSCNTL 0x15. If the inherited VBUS input limit
  is above 500mA, H0 **refuses enable**, because the TG 500mA input
  ramp is incompatible with the strict no-VBUSCNTL-write requirement.
- No FACTORY1, factory/bypass, CNTL2, input voltage, NV, FG WADDR/WDATA
  or permanent option-bit writes.
- Android battery status and online reflect the limited H0 owner;
  this is **not** Samsung sec_battery + MUIC and does not promise
  seven power-supply nodes. It is not for unattended charging.

## Risks and test sequencing
- Both battery and charger heat behavior must be confirmed under
  close supervision with a known-good TG recovery boot ready.
- On a USB-connected boot, the emergency guard may latch and H0 will
  refuse to arm. Do not override the P449 latch; reboot without USB,
  confirm both NTCs, then attach a current-limited source for a short
  supervised test. Never defeat temperature checks to make H0 run.
- The I2C device's `h0_arm` (0600, root write) must be explicitly set
  to 1 to attempt charge; `h0_kill` (0200) writing 1 immediately
  disables and permanently latches OFF until reboot. Do not
  send arm automatically from an Android init script.
- The H0 feature must be tested before manually starting ADSP.
  The PAS remoteproc is expected offline after a successful SCM probe.
- `QCOM_SCM_DOWNLOAD_MODE_DEFAULT=n` must be verified in final.config.
- AOSP SCM availability can affect UFS ICE and QCOM SMMU. This
  variant forces Lagoon ICE disabled as in P451 and keeps the existing
  qcom-smmu-qcom driver deferred. Evaluate the source-wide caller audit.

## CI / flash gating
Compilation and packed-DTB audits certify only structural build
properties. They do not certify battery current, watchdog operation,
UFS cryptographic behavior, or actual ADSP startup. If the combined
artifact fails audits, do not flash it. Preserve 452 DAILY fallback.
