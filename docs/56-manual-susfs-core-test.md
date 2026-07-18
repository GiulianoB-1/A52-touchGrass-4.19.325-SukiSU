# A52XQ Linux 4.19.200 ReSukiSU manual-hook SUSFS core test

This experiment starts from the hardware-booted Linux 4.19.200 ReSukiSU configuration dumped from the target Galaxy A52 5G.

Hardware-booted identities:

- boot image SHA-256: `41ae3b24771c70747c26aa17a18d254ffcb1c0d742b96f4f1f1fff20a6638554`
- kernel Image SHA-256: `2650a964fa4525af6350c9f03aaa1d4bfd169193ccf2fb889a6f0e99131c625c`
- final config SHA-256: `d2c21f394ec477a975ce96f59959fa265acde60a4a28ef4d200c9912dfb624d1`
- non-SUSFS canonical config SHA-256: `494a12a758bec9a7500b3370c4059c989254a468aaf895d43a8e6ea9a0441b92`

The test retains ReSukiSU manual hooks and all three working automatic hooks. SUSFS is enabled only as a separate core feature layer. Every SUSFS hiding, spoofing, redirect, mapping, and logging feature is disabled for the first boot test.

The workflow fails unless all 5,427 non-SUSFS configuration symbols exactly match the hardware-booted baseline.

The generated AnyKernel package replaces only the kernel payload of the currently installed boot image. It is not hardware validated until a controlled device test succeeds.
