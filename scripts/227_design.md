# Phase 227: retain ODSPOST records after recorder capacity

## Hardware evidence

The Phase 226 hardware capture contains 1,028 CRC-validated RS48 records and
reaches `/system/bin/odsign` at about 89.816 seconds. The retained timeline has
odsign exec-entry at sequence 2833 and exec-return at sequence 2835. Sequence
2834 was allocated between those two events but was not persisted. A further
44 sequence numbers are allocated but absent while odsign is active.

The recorder's initial buffered capacity is 896 records. After that boundary it
persists only messages accepted by its critical-message allowlist. Phase 226
compiled and executed the `ODSPOST 226` probes, but the allowlist contains the
older BOOTPOST, GFXPOST, KEYPOST, IONPOST, SGPOST and UFPOST families and omits
ODSPOST. Every Phase 226 event after capacity is therefore assigned a sequence
number and then discarded before transport persistence.

## Phase 227 change

Add exactly one prefix to the existing post-capacity critical-message allowlist:

```c
!strncmp(message, "ODSPOST ", 8)
```

The complete Phase 226 odsign/odrefresh tracing and the retained Phase 225 KGSL
tracing remain unchanged. CI requires the allowlist entry exactly once in the
patched recorder source and requires all Phase 225/226 marker strings in the
compiled ARM64 Image.

## Safety

This phase changes recorder retention only. It does not alter odsign,
odrefresh, fs-verity, ART, APEX, Keystore, Binder, storage, KGSL, init, or any
userspace-visible return value. It does not bypass verification, publish a
completion property, change files, capture buffers, record key material, or
expand path logging. Existing Phase 226 limits and fixed numeric path classes
remain intact.

## Expected decision evidence

The next capture should retain `ODSPOST 226` exec/open/ioctl/connect/task-state
records after sequence 896. Those records will distinguish a stable sleeping or
futex wait from Binder/Keystore dependency, storage I/O wait, ART/APEX activity,
or a successful odsign exit followed by the later apexd and KGSL boundary.
