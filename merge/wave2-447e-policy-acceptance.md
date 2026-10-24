# Phase447E: Samsung battery sysfs ownership and SELinux acceptance

## Uploaded evidence (2026-10-22 Wave2 variant A)

Both collector archives confirm SE7 FIFO bus31, SE8 GSI bus32, SE10 FIFO bus33 and a working SM5714 read-only fuel gauge at 0x49/0x71. Initial SOC 71%, 3967mV, temp 38.2°C. Manual /sys/class/power_supply/battery shows 69%, 3847mV, 43.9°C, present=1.

Android BatteryService still reports present:false / level:0. health HAL is denied sysfs read on battery type, thermal HAL is denied getattr on /sys/devices/platform/soc/984000.i2c/i2c-31/31-0049/power_supply/battery/temp. Their target is the generic u:object_r:sysfs:s0 label. The Samsung battery platform device remains unbound.

**The uploaded archives do not include vendor genfs_contexts or ls -Z outputs**. Do not claim vendor label fixed until on-device confirmation.

## Kernel changes

1. Source-grounded owner: TouchGrass 6bf351bd drivers/battery/common/sec_battery.c registers the battery power_supply with power_supply_register(&pdev->dev,...) in the sec-battery platform driver matching samsung,sec-battery. The current 447D bridge registered it from the I2C SM5714 parent instead.
2. The 447E bridge reuses the exact same read-only SM5714 SRAM SOC, voltage and temperature code, now registering the I2C power-supply name sm5714-fuelgauge. A Samsung-compatible platform driver binds the real samsung,sec-battery node, obtains that FG supplier, and registers the Android-facing battery supply from its own platform parent. It defers if the FG is absent; never spoofs charger state or level.
3. Linux 5.10 i2c_add_adapter ALREADY calls of_alias_get_id and registers a numbered adapter when the DT provides the alias. Changing to i2c_add_numbered_adapter without verified aliases would disrupt touch; 447E logs the actual alias per SE, without forced renumbering.
4. The Windows collector now captures real power_supply paths and ls -Z labels, available text vendor/odm genfs_contexts lines, Samsung battery platform and health/thermal denials. Never set SELinux permissive or patch vendor policy.

## Safety and unimplemented pieces

- Full production sec_battery, charger, MUIC, PDIC, direct charger and ADC_TM remain missing. Do not falsely claim working charging or thermal policy.
- usb, ac, otg, sec-direct-charger supplies are not fabricated. Charging status is UNKNOWN and no charger OTP/FG learned NV/PDIC firmware is written.
- Any effective vendor genfscon paths and SELinux labels must be learned from the next capture, not assumed.

## Acceptance (after CI and device flash)

- Boot logs include P447E battery PLATFORM_PROBE rc=0 and P447E I2C SE%d DT_ALIAS, plus normal P447D SE7/SE8/SE10 and touch logs.
- /sys/class/power_supply/{sm5714-fuelgauge,battery} exist; the battery symlink target is under the real Samsung platform device rather than I2C 31-0049.
- ls -Z of battery/type, present, capacity and temp matches a vendor-allowed battery label, as determined by the actual vendor genfscon. No hal_health_default or hal_thermal_default tcontext=sysfs denials.
- dumpsys battery shows present:true and approximately 69-71% after the HAL reads successfully. Charging icon/AC/USB status remains out of scope until the full charger/MUIC stack is audited.

Preserve 447D transport, SE8 GSI touch, 447C compiled wave defaults and 8MiB dmesg, KSU driver 35171, handoff=0 and known-good recovery path.
