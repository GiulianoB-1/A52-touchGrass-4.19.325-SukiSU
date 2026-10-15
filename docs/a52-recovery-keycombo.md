# A52 boot-time recovery key combination

This feature adds a built-in Linux input handler for the Samsung Galaxy A52 5G
(`a52xq`, `SM-A526B`). During the first 30 seconds after the handler initializes,
holding the physical Volume Up and Power buttons together for 800 ms calls:

```c
kernel_restart("recovery");
```

The handler deliberately accepts keys only from:

- `gpio_keys` for `KEY_VOLUMEUP`
- `qpnp_pon` for `KEY_POWER`

The device also advertises `KEY_VOLUMEUP` through its headset button interface.
Restricting the input-device names prevents that interface from satisfying the
boot combination.

Recovery mode is detected through `androidboot.boot_recovery=1`, with two common
fallback checks. This prevents a held combination from repeatedly rebooting an
already-running recovery kernel.

## Apply to a hydrated source tree

```bash
python3 scripts/add_a52_recovery_keycombo.py \
  workspace/touchgrass-a52xq \
  --report artifacts/a52-recovery-keycombo.json
```

The script creates and enables:

```text
CONFIG_INPUT_RECOVERY_KEYCOMBO=y
```

It is fail-closed and refuses to replace an unrelated driver or continue when
expected kernel-source anchors are missing.
