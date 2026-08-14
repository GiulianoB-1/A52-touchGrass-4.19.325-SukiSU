# A52 Phase263 Peripheral Parity Audit

Baseline: Phase263 head `d26cbf71b723551a99e86b241db71fa72062ac75`

Golden source: `micr0softstore/samsung_android_kernel_a52xq@6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`

This audit compares the Golden TouchGrass kernel/runtime, the actual Phase263 compiled artifact, and the healthy Android 16 A52 vendor/userspace contract. Compile presence is not treated as hardware validation.

| Subsystem | Phase263 status | Risk | Required repair |
|---|---|---|---|
| PIL / SSR | Golden provider restored; runtime equivalence of legacy DMA remap shim unproven | YELLOW | Preserve Phase263 provider and validate on hardware; do not replace blindly |
| FastRPC / ADSPRPC | Vendor ABI absent; `CONFIG_QCOM_FASTRPC` disabled; no `adsprpc-smd` strings | RED | Restore Golden `MSM_ADSPRPC` plus service locator/notifier/PDR closure |
| Audio / DSP | Generic ALSA core present, Samsung/Qualcomm techpack and SLIMbus NGD absent | RED | Port Golden audio techpack after FastRPC/PDR foundation |
| Camera | Generic media/V4L2 present, Spectra camera producer stack absent | RED | Port Golden `techpack/camera` and its Qualcomm dependencies |
| Sensors / SSC | Samsung SSC stack absent; healthy runtime uses `sscrpcd` and `sensors-ssc` | RED | Restore SSC/SLPI sensor transport after FastRPC/PDR |
| Fingerprint | Exact ET713/ET7xx producer absent | RED | Port Golden `drivers/fingerprint` ET7xx secure QCOM chain |
| Wi-Fi | cfg80211, ICNSS and Golden WLAN producer absent from Phase263 | RED | Restore ICNSS2/CNSS and identify/rebuild exact QCA WLAN producer/module |
| Bluetooth | Core HCI/QCA transport present | YELLOW | Preserve modern core; add only missing downstream `bt_power`/vendor glue if required |
| Modem / radio | QRTR + GLINK/RPMSG present, IPA3/RMNET/QMI-RMNET absent | RED | Restore Golden IPA/RMNET/QMI data path without replacing working QRTR core |
| GNSS | Vendor userspace service is primary; generic GNSS kernel framework is not the Golden contract | YELLOW | Re-test after shared Qualcomm transport restoration; do not add arbitrary GNSS drivers |
| NFC / eSE | Generic NFC differs from Golden Samsung `SEC_NFC` contract | RED | Port `sec_nfc`, `ese_p3`, logger/wakelock closure |
| USB | DWC3 QCOM core present; vendor gadget extensions incomplete | YELLOW | Keep current DWC3; add Samsung/QCOM gadget functions only if runtime composition needs them |
| Charging / battery | Samsung battery, SM5714 and PCA9468 chain absent | RED | Port Samsung battery/charger/fuel-gauge closure and SM5714 integration |
| Thermal / performance | Generic/QCOM base is substantial but Golden LMH/devfreq/sysfs pieces differ | YELLOW | Restore only proven missing provider nodes after higher-risk RED subsystems |
| Display / touch | Display handled separately; exact STM FTS touch producer still missing | RED for touch | Port exact Golden STM FTS touch chain without disturbing graphics path |

## Boot criticality

Not every RED subsystem is boot-critical. Phase263 reaching Android/SystemUI already demonstrates that several are feature-critical rather than kernel-boot critical.

- Boot-path critical or potentially boot-blocking: PIL/SSR for GPU ZAP, essential power/clock/storage/display/IOMMU paths, and mandatory remoteprocessor dependencies.
- Android-service critical: FastRPC/PDR, audio DSP, SSC sensors, modem transport and Wi-Fi. Missing them can cause service failures or watchdog delays without necessarily preventing kernel boot.
- Feature-critical: camera, fingerprint, NFC, Bluetooth, GNSS, touch input, cellular data and most USB functions.
- Safety/operational critical: battery/charging and thermal handling. These may not block boot, but must be correct before daily-use validation.

## Repair order

1. Phase264: FastRPC + service locator/notifier + PDR foundation.
2. Audio + SSC sensors on the proven Phase264 transport.
3. IPA/RMNET/QMI modem data path while preserving modern QRTR/RPMSG.
4. Fingerprint, NFC/eSE and battery/charging producers.
5. Camera and Wi-Fi larger producer stacks after shared infrastructure is stable.
6. Touch and remaining thermal/performance/vendor USB parity.
