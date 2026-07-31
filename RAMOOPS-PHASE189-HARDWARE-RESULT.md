# A52 phase 189 hardware result

Capture time: 2026-07-31 10:13 Asia/Jerusalem.

The raw collector produced an untouched 1 MiB frozen RAMOOPS snapshot. The later
raw export is byte-for-byte identical to the frozen copy. The ZIP command
reported return code 4, but the resulting archive is readable and contains both
raw images and the pstore evidence.

The near-header Reed-Solomon decoder recovered 166 consecutive records,
sequences 17 through 182, with no gaps in that recovered range. The console
persistent-RAM signature required the same one-bit repair used in phase 188. The
ftrace signature was intact.

GPIO-core registration completed these stages:

- GPIO device and descriptor allocation
- global GPIO list insertion
- line-name setup
- valid-mask allocation
- OF GPIO-chip registration
- valid-mask initialization
- direction-scan entry

Every eager direction read from GPIO 0 through GPIO 12 returned normally with
`rc=1`. The final persistent record is:

`GPIOCORE dir-read enter pin=13`

There is no matching `GPIOCORE dir-read exit pin=13 ...` record. The stop is
therefore inside the Lagoon `get_direction()` MMIO access for GPIO 13, whose
control-register offset is `0x0d000` from the TLMM base.

The Lagoon vendor source already groups GPIOs 13-16 under the secure-fingerprint
reservation. In the GKI reconstruction, the Samsung-only
`CONFIG_FINGERPRINT_SECURE` and `CONFIG_SEC_FACTORY` symbols are absent, so that
reservation compiles out and generic Linux 5.10 attempts to read GPIO 13 during
`gpiochip_add_data_with_key()`.

Phase 190 should restore the reservation for GPIOs 13-16 independently of those
Samsung-only Kconfig symbols, while retaining the phase-189 trace. The expected
next trace is pin 12 exit followed by pin 17 entry, with pins 13-16 skipped.
