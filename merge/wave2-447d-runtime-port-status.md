# Phase447D Wave 2: actual captured boot failure and source-grounded first port

## Evidence: 2026-10-21 three collector bundles

The collector archives are analyzed locally, **not** stored in this repository. The built kernel reported `P447 GATES source=kconfig wave=2 mss=0 ipa=0`, then `P447 W2 ... ARMED`. All three logs showed `geni_i2c 984000.i2c: P446Y I2C SE_SKIP ... SE8_ONLY=1` and the same for `990000.i2c`. All three had an empty `/sys/class/power_supply/`. Thus the old 447C software gates work but do not supply the missing drivers.

## A52 R02 reference: pinned TouchGrass 6bf351bd

Source: `arch/arm64/boot/dts/samsung/a52/a52xq/a52xq_eur_open_w00_r02.dts` (SM-A526B).

| Fragment | Resolved target and content | 447D behavior |
|---|---|---|
| `fragment@49` | `qupv3_se7_i2c` at 0x984000, 400 kHz, `sm5714@49` compatible `siliconmitus,sm5714mfd`; irq gpio TLMM 84, wakeup | Allow SE7 with Wave2, read-only FG at secondary address 0x71, no IRQ request by interim FG |
| `fragment@50` | `qupv3_se10_i2c` 0x990000 at 400 kHz, `usbpd-sm5714@33`, `usbpd,usbpd_int` TLMM 124, `pdic-manager` child | Allow SE10 I2C, but **no PD firmware/programming** until vendor port is audited |
| `fragment@52` | `qupv3_se7_i2c`: `pca9468@57` direct charger, NXP compatible | Bus exposure only; do **not** write charger IC on unported policy |
| `fragment@39` | `qupv3_se10_i2c`: disabled `smb1396@34` and `smb1396@35` and subdevices; bus clock override 100 kHz | Do not enable SMB1396 |
| `fragment@116` | `qupv3_se10_i2c`: clock override 400 kHz | Bus clock only; no extra clients |
| `fragment@137` | `qupv3_se7_i2c`: `sm5714-fled` camera flash | No LED/torch driver, remains untouched |

## Implemented in this first 447D increment

1. `scripts/447d_se7_se10_i2c.py` removes the SE8-only restriction, replacing it with an exact SE7/SE8/SE10 allowlist. Wave1 still allows only SE8. Compiled Kconfig default bitmask is `0x580`; `a52.i2c_se=8,7,10` remains an optional override. SE-specific logs identify FIFO vs GSI based on the read-only firmware register at offset `0x64`, plus probe and first transfer result. Existing 446Z MSM bus votes and 446AE QUPv3_1 wrapper DMA ownership are reused. A missing GPI provider, DMA descriptor, IOMMU, AHB clock or IRQ fails closed.
2. `scripts/447d_readonly_sm5714_fg.py` creates an **actual built-in** 5.10 I2C/power_supply driver at 0x49 with the FG secondary I2C client at 0x71. It uses exact TG `sm5714-private.h` offsets `RADDR=0x8c`, `RDATA=0x8d`, reads SRAM `SOC=0x00`, `VBAT=0x03`, `TEMP=0x07`, reproduces TG scaling, and only registers the `battery` supply after plausible readings. It never writes SRAM WADDR 0x8e, WDATA 0x8f, learned SOC, charger, OTP or PDIC. Reads require a volatile SRAM address-selector write; this is not a firmware/NV write.
3. ACK 5.10's existing `CONFIG_QCOM_SPMI_ADC5` driver is compiled. This **does not** yet port downstream `qcom,adc-tm5-iio` because the requisite vendor thermal callbacks/DT ABI are different. Its runtime match must be checked.

## Not yet ported — acceptance blockers

- **Full Samsung `sec_battery` + direct charger**: TG includes `drivers/battery/common/sec_battery*.c`, `sec_direct_charger.c`, `pca9468_charger.c`. These depend on Samsung power_supply extended properties, managers, charging algorithms, alarmtimer/wakeup/source. Blindly compiling or enabling this code risks charger/PD and learned/NV writes.
- **Production SM5714 MFD and charger/fuelgauge/MUIC**: TG `drivers/mfd/sm/sm5714/{sm5714_core.c,sm5714-irq.c}`, `sm5714_charger`, `sm5714_fuelgauge`, `sm5714-muic`. The interim FG at the same OF compatible **must be removed/replaced** before MFD registration. Never let two drivers own 0x49 or 0x71.
- **SM5714 USB-PD / PDIC manager, PCA9468 direct charging**: source and 4.19→5.10 dependencies not yet validated. IRQ trigger/supplier ordering is unverified. Keep outputs unchanged, no firmware flash, OTP writes or battery-fuelgauge calibration updates.
- **ADC_TM**: ACK 5.10 ADC5 source is present, but the downstream battery thermal monitor `qcom,adc-tm5-iio` provider remains unported. Do not falsely claim thermal policy works.

**This is a first, constrained working-code bring-up, not completion of the requested full 447D acceptance.** Success of CI proves compile and packaging only. Runtime acceptance requires SE7/SE10 probe, first I2C transfer, true capacity and voltage, then a separately audited charge-control stack with battery/usb/ac supplies and actual charging-state transitions. Keep recovery image, USB ttyGS3, KSU 35171, display handoff=0, 447B touch, 8 MiB dmesg.
