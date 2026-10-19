# Phase447 Wave 2: source-verified Samsung battery/charging port queue

**Scope:** pinned TG `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`, GKI `f960ed27302b1ff8e61e152fc202554d778deccd`. This is an implementation queue, **not** a claim of port completion. Default `a52.wave=1` keeps all Phase447 drivers OFF. Do not switch `a52.wave=2` until all Wave 2 sources compile and are individually gated.

## Confirmed TG sources

| Order | Function / config | Exact pinned source | P447 switch |
|---:|---|---|---|
| 1 | SM5714 MFD / CONFIG_MFD_SM5714 | `drivers/mfd/sm/sm5714/sm5714_core.c`, `sm5714-irq.c` | `a52.sm5714` |
| 2 | Samsung battery / CONFIG_BATTERY_SAMSUNG | `drivers/battery/common/sec_battery.c`, `sec_battery_vote.c`, `sec_battery_thermal.c`, `sec_battery_dt.c`, `sec_battery_ttf.c`, `sb_full_soc.c`, `sec_core_sysfs.c`, `sec_battery_sysfs.c`, `sec_device_sysfs.c`, `sec_event_sysfs.c` | `a52.battery` |
| 3 | SM5714 fuel gauge / CONFIG_FUELGAUGE_SM5714 | `drivers/battery/fuelgauge/sm5714_fuelgauge/sm5714_fuelgauge.c` | `a52.fuelgauge` |
| 4 | SM5714 charger / CONFIG_CHARGER_SM5714 | `drivers/battery/charger/sm5714_charger/sm5714_charger.c`, `sm5714_charger_oper.c` | `a52.charger` |
| 5 | SM5714 MUIC / CONFIG_MUIC_SM5714 | `drivers/muic/sm/sm5714/sm5714-muic.c`, `sm5714-muic-afc.c`, `sm5714-muic-pdic.c` | `a52.muic` |
| 6 | SM5714 PDIC / CONFIG_PDIC_SM5714 | `drivers/usb/typec/sm/sm5714/sm5714_typec.c`, `sm5714_policy.c`, `sm5714_pd.c` | `a52.pdic` |
| 7 | PCA9468 direct charger / CONFIG_CHARGER_PCA9468 | `drivers/battery/charger/pca9468_charger/pca9468_charger.c` | `a52.directcharger` |
| 8 | PMIC ADC5 / CONFIG_QCOM_SPMI_ADC5 | `drivers/iio/adc/Kconfig` (resolve active C driver and DT compatible in TG and ACK) | `a52.adc` |
| 9 | QTI ADC-TM / CONFIG_QTI_ADC_TM | `drivers/thermal/qcom/Kconfig` (resolve active C driver and DT compatible in TG and ACK) | `a52.adc` |

The rows above are anchored to the TG `a52xq_defconfig` and its `drivers/Kconfig`/Makefile includes, not to guesses about top-level directories. The common battery Makefile and other subdirectory Makefiles identify the listed built-in objects.

## Nonnegotiable implementation safety

- Introduce a **uniquely named 5.10 port** for each conflicted downstream-compatible binding. Ensure the old ACK matching driver cannot bind the same node.
- Call `a52_447_probe_allowed(dev, ID)` **before** hardware I/O, IRQ request, or scheduling work. On OFF return `-ENODEV` and print the P447 disabled marker. A header and gate without a probe call do **not** constitute a functional port.
- Keep hardware supplier dependencies explicit: MFD first; then gauge, charger and battery class; Type-C / MUIC / PPS afterward. Parse R02 device-tree `compatible`, interrupts, GPIOs, regulators, ADC channels, pinctrl and wake IRQs against TG and final GKI DT.
- Forbid firmware, flash, OTP, NVM, learned-state, fuel-gauge calibration, EEPROM and modem NV writes in every phase. Carefully distinguish ordinary ephemeral I2C configuration writes (needed for normal sensing) from persistent writes.
- Preserve `446AF` touch/GSI and `446AE` QUP wrapper IOMMU, display/KGSL, BakaSU rc3, ttyGS3/U446 and HWC ring trace.
- A probe must report supplier requests, deferrals, firmware name if applicable, success and first functional event. All new hardware transfers must have timeout and checked return values.
- Require `a52_final_tree_manifest_audit.py --strict` and each port's exact `--check-only` guard before compilation; include source hashes and resulting Image/DTB fingerprints in build artifacts.

## Verification after *each* independently isolated port

Review `/sys/class/power_supply/battery`, `usb`, `ac`, `otg`; Samsung `batt_*` nodes; `dumpsys battery`; USB charging detection and safe unplug; kernel thermal/charger errors. A successful module initialization without correct capacity/health/temp reporting is **not** sufficient.

**Current status:** TG source file locations verified; compatibility/API port, unique binding and persistent-write audit **not completed**. The Phase447 branch builds a default-OFF gate infrastructure only.
