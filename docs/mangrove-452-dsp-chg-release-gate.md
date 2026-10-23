# Mangrove 452 — combined DSP + H1 charger acceptance gate

**State: NO-GO. No `MANGROVE-452-DSP-CHG` boot image has been produced.**
This note is the source audit and implementation dependency list, not a
claim that the full SM5714 charger port has been implemented.

## 1. Verified DSP SCM change
- P451 AOSS is already hardware-validated with
  `P448 AOSS probe rc=0` and `LOAD_STATE name=adsp val=on/off rc=0`.
- PAS ADSP currently defers before `rproc_alloc()` because 5.10
  `qcom_scm_is_available()` returns false without a device node.
- Staged boot DTB patch adds exactly
  `/firmware/scm`, compatible `qcom,scm-sm6350` +
  `qcom,scm`, `#reset-cells = <1>`.
  It does not modify UFS nodes, download-mode resources, ADSP auto_boot,
  or existing AOSS/GLINK/charger wiring.

## 2. Actual final compiled-tree SCM audit
- Reference is **MANGROVE-451-DAILY-431** final source tar, not base 5.10.
- UFS source SHA-256 `54f6ec31...`: `A52_UFS_ICE_SAFE_BRINGUP=1`
  clears `UFSHCD_CAP_CRYPTO` then returns *before* SCM ICE query/MMIO.
  `CONFIG_BLK_INLINE_ENCRYPTION_FALLBACK=y`.
- `CONFIG_QCOM_SCM=y`;
  `# CONFIG_QCOM_SCM_DOWNLOAD_MODE_DEFAULT is not set` (required).
- `CONFIG_SCSI_UFS_QCOM=y`; `QCOM_IOMMU=n`,
  `DRM_MSM=n`, `VIDEO_QCOM_VENUS=n`,
  `MMC_SDHCI_MSM=n`, `RMTFS_MEM=n`, `OCMEM=n`.
- `arm-smmu-qcom.c` is pinned to exact final-tree SHA-256;
  P204 downstream `qcom,skip-init` code must remain intact.
- The new `scripts/452_scm_storage_audit.py` inventories 19
  external SCM calling source files plus 4 upstream SCM internals,
  checks compiled Kconfig, checks known source SHA-256, and hard-fails
  on unknown new SCM callers. **This is a static proof, not a
  guarantee of identical SMMU/PDC behavior when SCM probes.**
- SMMU/PDC/pinctrl original callers may now complete secure operations
  that previously had no SCM device. Validate PDC/GPU/SMMU boot logs
  and compare UFS capabilities and encrypted /data mount at runtime
  before manual ADSP start.

## 3. H1 source topology blocker: MFD ownership
- Current `a52_sm5714_fg_ro.c` is an I2C driver bound directly to
  `siliconmitus,sm5714mfd` at 0x49. It allocates a dummy 0x71 FG client,
  registers `battery` and `sm5714-fuelgauge`, and owns P449 inhibit.
- Real TG H1 must replace that parent with
  `drivers/mfd/sm/sm5714/sm5714_core.c` and `sm5714-irq.c`;
  a real MFD must register child FG and charger. It is **not safe**
  to bind a second competing i2c_driver to the same 0x49 node.
- TG charger:
  `drivers/battery/charger/sm5714_charger/sm5714_charger.c` and
  `sm5714_charger_oper.c`.
- Samsung manager: `drivers/battery/common/sec_battery.c`,
  `sec_battery_dt.c`, `sec_battery_thermal.c`, `sec_battery_vote.c`
  and supporting Samsung extensions, *not* standalone fake PSYs.
- MUIC: `drivers/muic/sm/sm5714/sm5714-muic.c` and its interfaces.
- Actual H1 supplies targeted:
  **ac, battery, otg, sm5714-charger, sm5714-fuelgauge, usb, wireless**.
  Do not fabricate registrations to hit count=7.
- H1 `battery,charger_name` must be `sm5714-charger`.
  TG's `sec-direct-charger` relies on excluded H2 PCA9468 fast charging.
- Move P449 guard to higher-priority MFD-safe arbitration:
  no charge enable while batt NTC or charger NTC invalid, bootloader
  ENQ4FET bit3 may start as 1, and thermal inhibit wins against
  sec_battery enabling.

## 4. Specific SM5714 hardware contract
- Read and log CNTL1 + STATUS2/STATUS3 before any write;
  actively clear ENQ4FET immediately if charger is ON at handoff
  and NTCs not both confirmed valid.
- TG `chg_set_enq4fet()`, lines ~328–347:
  on enable with input limit >500mA, first set 500mA,
  sleep `(old_mA - 500)/250` milliseconds,
  set CNTL1 bit3, then restore TG cable-table limit.
- **TG's `sm5714_chg_charging()` calls ENQ4FET enable before
  `chg_set_wdt_enable` (lines ~739–740). That original sequence
  VIOLATES this work order's stronger safety contract. The H1 port
  must instead configure 90-second watchdog, confirm enable/readback,
  kick WDT, and only then enable ENQ4FET. Never allow WDT off on
  charge-enable paths. Disable on any watchdog/NTC/policy error.
- WDT expired STATUS2 bit7 requires reset (WDTCNTL bit6) and 90s setup;
  WDT enable=WDTCNTL bit0, kick=bit3, timer=bits1..2.
- Float voltage **4380mV**. Cable currents only from the exact TG
  `cable-info/current_group_*` table, not guessed 500/1000/1800
  defaults or wall-charger assumptions.
- No FACTORY1 or Q3 option-bit programming: TG charger probe at
  ~1303–1333 can write 0x51/0x4C/0x57/0x49/0x4A and option
  0xEA/0xED/0xE4/0xCB. Port must remove this path entirely.
- Charge current and WDT state need closed-loop readbacks,
  cable attach and detach events, full/recharge and swelling policy,
  watchdog refresh, fail-closed on thermal and bus-read errors.
- **Referenced Claude file is not accessible in the current project
  attachments**: `claude/finding-20261024-sm5714-bootloader-handover-registers-decoded-h1-inputs.md`.
  Complete live handover decoding must be compared against that
  report before enabling charger writes.

## 5. Planned release and test contract (not achieved)
- **MANGROVE-452-DAILY**: unchanged P450/P451 guard, 2 supplies,
  P448 IPC, P449 CHG_GUARD, P450 CHG_STATE/current_ua,
  P451 CHG_REGS / BATTERY current_mA.
  Existing known-good 451-DAILY artifact remains available.
- **MANGROVE-452-DSP-CHG**: everything above plus
  P448 AOSS probe; P452 SCM probe, P451 ADSP PROBE
  manual_start=1 auto_boot=0, and actual
  P452 SM5714 MFD/CHARGER PROBE, P452 SEC_BATTERY CABLE/TEMP,
  P452 MUIC CABLE, P452 WDT enable/kick/readback,
  P452 ENQ4FET before/after, P452 FLOAT 4380.
  Expected 7 *real* power supplies, no fast-charge/PDIC.
- First **compile/source/packed DTB audit**, then **separate
  hardware validation** of the charger at supervised PC USB and
  plain 5V wall, then manual ADSP start and logs.
  Combined image is **NO-GO** until full H1 port exists and
  executable safety gates pass.
- The staging branch does NOT trigger a release workflow. The
  release branch and successful DAILY image remain unchanged.
