# A52 P1 boot image

## Goal

Create a hardware-testable Galaxy A52 5G `boot.img` by repacking the successful P0 kernel into the verified UN1CA boot layout.

## Inputs

- Successful P0 `Image.gz`
- Original working UN1CA `boot.img`
- Original UN1CA boot metadata and checksums

## Boot contract

The repack must preserve:

- boot header version 2
- 4096-byte page size
- original ramdisk
- original kernel command line
- embedded Lagoon DTB arrangement
- original `dtbo.img` as an external untouched asset
- 96 MiB boot partition limit

## P1 output

The workflow may produce a test `boot.img`, but it must be labeled `NOT-HARDWARE-VALIDATED` until a controlled device test succeeds.

The workflow must also produce:

- original and repacked image metadata
- partition-size audit
- SHA-256 checksums
- a byte-level component comparison
- a clear statement that no flashing occurred in CI

## Safety boundaries

P1 must not:

- flash a device
- modify `dtbo.img`
- modify vendor boot, recovery, super, vbmeta, or other partitions
- silently change the ramdisk, command line, page size, header version, or embedded DTB layout
- publish a recovery ZIP or Odin package before the raw repack is independently audited

## Planned implementation

1. Validate the supplied original `boot.img` against the recorded UN1CA contract.
2. Extract the original image using a pinned boot-image tool.
3. Replace only the kernel payload with the successful P0 `Image.gz`.
4. Repack deterministically with the original metadata.
5. Re-extract the result and verify all preserved components.
6. Enforce the 96 MiB partition cap.
7. Upload the result and diagnostics as `NOT-HARDWARE-VALIDATED`.
