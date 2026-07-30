Phase 188 hardware capture 2026-07-30 23:27 Asia/Jerusalem

Decoded 141 fresh mirrored records from the untouched 1 MiB RAMOOPS snapshot. The console persistent-RAM signature required one-bit recovery; all 141 records decoded without sequence gaps.

Last confirmed stages:

- Lagoon PDC probe exit rc=0
- QPNP AMOLED registration exit rc=0
- normal DSI/display EPROBE_DEFER preserved
- Lagoon TLMM allocation, MMIO mapping, PM/reset setup, IRQ lookup and pinctrl registration completed
- wakeup-parent domain found
- GPIO IRQ parent allocation completed
- final record: `PINCTRL gpio chip-add enter irq=176`

The persistent-RAM header reported 36,043 used bytes in a 256 KiB bank. The trace did not fill the physical recorder region, so the final record is a real stopping point rather than capacity exhaustion.

TouchGrass comparison: its 4.19 GPIO-core registration initializes GPIO descriptor direction flags without reading every GPIO register. Common 5.10 calls `gc->get_direction()` for every valid GPIO during `gpiochip_add_data_with_key()`. Phase 189 will instrument the generic GPIO-core path to identify whether the reset occurs during OF registration, valid-mask initialization, an eager direction read, IRQ-chip setup or GPIO device setup before applying any compatibility change.
