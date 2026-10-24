# Phase 452: SCM/DSP build, H1 charger release gate — 2026-10-24

## Hardware evidence (capture-20261024-0009)
- 451 DSP boots, 5.10 native AOSS probes `P448 AOSS probe rc=0`.
- Seven `P448 AOSS LOAD_STATE name=adsp val=on/off rc=0` round trips.
- `3000000.qcom,lpass` remains `devices_deferred`; no P451 ADSP PROBE.
- TG has **no** firmware/scm OF node, but 5.10 PAS checks
  qcom_scm_is_available() before rproc_alloc.
- 451 has 2 power supplies, charger state remains read-only + one-way guard.
- New SCM may affect unrelated 5.10 drivers; must keep storage unchanged.

## P452 SCM/DSP *candidate*, manual start only
1. Packaged boot DTB: create /firmware/scm with compatible strings
   qcom,scm-sm6350, qcom,scm and #reset-cells = <1>.
2. `qcom,lagoon` UFS ICE: preserve 451 disable behavior even if
   qcom_scm_ice_available() starts reporting true. Never change
   UFSHCD_CAP_CRYPTO, crypto Kconfig, ICE keys, ICE resource or UFS node.
3. `qcom,lagoon` upstream arm-smmu-qcom: preserve prior
   -EPROBE_DEFER when SCM unavailable; downstream SMMU owns hardware.
4. Assert `CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT=n` and `CONFIG_QCOM_SCM=y`.
5. Enumerate ALL qcom_scm_* direct call sites in the final compiled source
   into P452-SCM-CALLERS.json. Refuse any unreviewed new
   qcom_scm_is_available / qcom_scm_ice_available callers.
   Secure-world users of other SCM calls need runtime validation.
6. Existing downstream SCM and upstream 5.10 SCM will coexist.
   The DT patch does not write SCM download mode or change charger.
7. Keep ADSP PAS manual: `auto_boot=false`; expected offline remoteproc
   entry at boot. TG LPI rails=384 and AOSS LOAD_STATE name=adsp.
8. No Q6 modem/MSS/CDSP/IPA/rmtfs path enabled. No new normal charging.

## H1 remains NO-GO (not advertised as DSP-CHG)
TouchGrass commit 6bf351bd provides:
- 1536-line `drivers/battery/charger/sm5714_charger/sm5714_charger.c`.
- 401-line `sm5714_charger_oper.c`, containing FACTORY1 factory mode
  paths that must be EXCLUDED from the first H1 port.
- 8200-line `drivers/battery/common/sec_battery.c` plus
  `sec_battery_dt.c`, `sec_battery_thermal.c`,
  `sec_battery_vote.c`, `sec_battery_ttf.c`, sysfs,
  common charging helpers and Samsung notification APIs.
- MUIC and MFD are not yet integrated in Mangrove; existing SM5714 is
  READ-ONLY FG with emergency ENQ4FET clear.

Required H1 acceptance before claiming 7 supplies:
- SM5714 charger MFD I2C ownership cannot collide with FG I2C,
  Samsung sec_battery must replace, not duplicate, the current battery
  bridge; MUIC VBUS/cable events integrated with USB/ADB.
- Physical charging **OFF** at takeover regardless of bootloader state;
  both NTCs known and normal before any enable; P449 guard retains
  higher-priority, asynchronous kill permission.
- WDT 90s configured AND successfully kicked and status read back
  BEFORE writing CNTL1 ENQ4FET=1; repeated WDT kicks during charge.
  Watchdog-off must always imply ENQ4FET=0.
- Voltage precisely 4380mV per TG live DT.
- Input and fast charge currents strictly only from TG cable type table,
  TG 500mA ramp and 250mA/ms transition, no guessed fallback.
- NEVER write FACTORY1/bypass, OTP, learned FG NV or fast-charge
  registers; no direct charging H2 (sec-direct/pca9468).
- No first-time combined flash: validate independent PC USB, 5V wall
  USB charger, unplug, over/under-temp inhibit, full/terminate/recharge
  with a known-good TG recovery image, THEN separately test DSP.

## Requested vs implemented artifacts
- `MANGROVE-452-DAILY`: actual existing 451 DAILY baseline compiled
  afresh in the same workflow run; 2 supplies.
- `MANGROVE-452-DSP-SCM-TEST`: same baseline + native 5.10 SCM DT,
  UFS ICE/SMMU freeze and ADSP manual-start. 2 supplies.
- `MANGROVE-452-CHG` / `MANGROVE-452-DSP-CHG`: NOT SHIPPED.
  Both would be fake/unsafe without an actual H1 stack and validation.

A successful compile and DTB audit is not proof that SCM is safe or
remoteproc boots. Check UFS/storage and AOSS probe *before* manual ADSP
start, then collect full Ramoops/early logcat, and compare with 451.
