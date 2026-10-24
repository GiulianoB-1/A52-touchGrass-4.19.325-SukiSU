# Phase447H addendum — A52 PM6150L ADC thermistor contract

Reference: user addendum 2026-10-22, pinned TG kernel `micr0softstore/samsung_android_kernel_a52xq@6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`, `drivers/iio/adc/qcom-spmi-adc5.c:926-933` and A52 R02 overlay.

## Device-tree wiring

| Channel | Samsung consumer | Meaning | Source and conversion |
|---|---|---|---|
| 0x52 | sec_thermistor `pa_therm` | GPIO1 | TG ADC voltage, client-specific table |
| 0x53 | sec_battery `adc-temp` | `bat_thm`, actual battery NTC | **processed uV** → Samsung `battery,temp_table_adc`/`battery,temp_table_data` |
| 0x54 | sec_battery `adc-chg-temp` | `chg_thm`, charger NTC | **processed uV** → `battery,chg_temp_table_adc`/`battery,chg_temp_table_data` |
| 0x55 | `quiet_therm` | GPIO4 | TG ADC voltage, thermal client-specific table |

The A52 R02 DT requests `qcom,ratiometric`, `qcom,pre-scaling=<1 1>`, `qcom,hw-settle-time=200`; do not overwrite these.

### Important difference versus addendum example

The addendum proposes `ADC5_CHAN_TEMP(...SCALE_HW_CALIB_THERM_100K_PULLUP)`. **Pinned TouchGrass for this phone instead uses `ADC_CHAN_VOLT("gpioN_pu2",1,SCALE_HW_CALIB_DEFAULT)` for all four GPIOs.** The ACK 5.10 equivalent is `ADC5_CHAN_VOLT("gpioN_pu2",0,SCALE_HW_CALIB_DEFAULT)` (different table prescale indices: 0 is 1:1 in ACK). Calling IIO `TEMP` and then using Samsung's voltage-to-temperature interpolation would double-convert. Preserve the target TG semantics, not unrelated generic mainline thermistor semantics.

### Implementation

- `447g_adc5_gpio2.py` covers GPIO2/3/4; `447h_adc5_tg_ntc.py` extends GPIO1 and enforces all 4 exact TG voltage descriptors in the final source.
- Unknown-channel handling remains strict/fail-closed (not SKIP_CHANNEL). In particular the provider **must never silently skip 0x53 or 0x54**. Logs `P447H TEMP MISSING`, `P447H ADC5 PROVIDER DT_FAIL`, `P447H ADC5 GPIO`, `P447H ADC5 PROVIDER DT_OK`.
- `447g_ntc_iio.py` and `447h_ntc_retry.py` bind `adc-temp` under the true Samsung platform DT node; deferred probes are retried without losing Android's battery power_supply.
- `447h_tchg_ntc.py` independently binds `adc-chg-temp`, uses the Samsung charger table, and logs `P447H TEMP therm=<Tbat> chg=<Tchg> fg=<FG> src=<...>` every 30 seconds in the existing guard sample. `Tchg` is diagnostic only; guard prioritizes `Tbat`.
- All code paths retain the preexisting one charger write site (emergency SM5714 `CNTL1.ENQ4FET` **disable only**); no new charge-enable, fast-current, OTP, direct-charger, fuel-gauge calibration writes.
- Keep the independent battery and charger ADC paths and report failures, rather than conflating the FG die temperature with the battery cell temperature.

## Runtime acceptance gates (not established by a successful CI)

1. Real GKI `pm6150l vadc@3100` provider probes successfully with all 0x52–0x55 child channels, specifically 0x53/0x54.
2. `P447H NTC READY` and `P447H TCHG READY`; `P447H TEMP therm=... chg=... fg=... src=BATT_THERM`.
3. Compare `Tbat` to back-to-back TG within **±1.5 °C** and check `Tchg` against TG charger thermistor reading. `FG` should be logged independently, never substituted as cell temperature without an explicit fallback tag.
4. Charging must remain **prohibited** on the experimental GKI until the complete charger/MUIC/USBPD/`sec_battery` thermal-control state machine is implemented and tested. This read-only ADC work does NOT validate normal charging or charge suspension under hardware load.

Touch operation on SE8/i2c-35, I2C 34/35/36, and the read-only FG path remain unchanged.
