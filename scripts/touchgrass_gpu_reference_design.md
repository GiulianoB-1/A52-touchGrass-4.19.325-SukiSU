# TouchGrass GPU reference recorder v1

## Goal

Capture the earliest successful GPU initialization events from the known-good A52 TouchGrass Linux 4.19.200 stack so the sequence can be compared directly with the ACK bring-up.

## Baseline invariants

- Reconstruct the pinned TouchGrass source through Linux 4.19.200.
- Keep the original `a52xq_defconfig` build path.
- Require the working GPU stack symbols, including ARM-SMMU, KGSL IOMMU, Lagoon GCC/GPUCC, GDSC and KSU.
- Repack into the exact checksum-locked 96 MiB boot source whose SHA-256 is `41ae3b24771c70747c26aa17a18d254ffcb1c0d742b96f4f1f1fff20a6638554`.
- Preserve the source boot image ramdisk, DTB, command line and boot-header layout.

## Recorder semantics

The recorder is observation-only. It uses a static 8192-entry buffer. Entries are allocated monotonically and never overwritten, so later Android GPU activity cannot erase the initial boot sequence. The buffer is readable through `/proc/tg_gpu_reference` after boot.

Each event carries:

- monotonically increasing sequence number
- monotonic nanosecond timestamp
- CPU
- event tag
- optional clock, regulator or context name
- return code
- four numeric payload fields

## Instrumented families

### ARM-SMMU

Power-resource lifecycle, per-regulator enable, per-clock prepare/enable, domain initialization, device addition and device attach.

### KGSL IOMMU

DT probe, MMU initialization, pagetable initialization, context attach, MMU start and pagetable switching.

### GMU

GMU context-bank domain allocation and attach, GMU IOMMU initialization, permanent mappings, memory probe, GMU probe and GMU start.

### HFI

HFI startup and command traffic, including queue, message ID, message size and sequence number.

### Adreno / A6xx / KGSL

Adreno probe and start, A6xx initialization and optional later A6xx startup helpers, KGSL platform probe and power-state transitions.

## Comparison strategy

After collecting a successful TouchGrass trace, normalize the TouchGrass and ACK events into the same subsystem/stage vocabulary. Compare prerequisites and return values in chronological order. The first prerequisite or semantic transition present in TouchGrass but absent or failing in ACK becomes the next porting target.
