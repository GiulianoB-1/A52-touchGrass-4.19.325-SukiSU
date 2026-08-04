# Phase 223: Samsung UFS query ioctl compatibility

## Hardware evidence

Phase 222 captured 13 identical qseecomd attempts on GKI. Every attempt:

1. discovers `sg1` as SCSI LUN `0xC144` (UPIU RPMB LUN `0xC4`),
2. completes `SCSI_IOCTL_GET_IDLUN` (`0x5382`),
3. calls `UFS_IOCTL_QUERY` (`0x5388`),
4. receives `-EINVAL`,
5. exits with userspace status 255 before any `SG_IO` (`0x2285`).

The TouchGrass reference returns 0 for the same `0x5388` call and then reaches REQUEST SENSE, SECURITY PROTOCOL OUT (`0xB5`), and SECURITY PROTOCOL IN (`0xA2`).

## Change

Add the missing Samsung UFS query ABI and a read-only UFS host ioctl callback to ACK 5.10:

- `include/uapi/scsi/ufs/ioctl.h` defines `UFS_IOCTL_QUERY` and `ufs_ioctl_query_data`.
- `drivers/scsi/ufs/ufshcd.c` implements read descriptor, read attribute, and read flag queries using existing ACK UFS query helpers.
- the callback is registered in `ufshcd_driver_template` for native and compat callers.
- runtime PM is balanced around each query.

This is a real query bridge. It does not fake a successful return.

## Recorder contract

`UFPOST 223` records qseecomd-only metadata:

- SCSI and converted UPIU LUN,
- ioctl command,
- query opcode and IDN,
- requested or returned size,
- return code.

No descriptor bytes, RPMB frames, keys, CDB payloads, or userspace memory are recorded. The trace is capped at 96 events.

## Expected next result

The first `0x5388` on `sg1` should return 0. qseecomd should then issue `SG_IO` and Phase 222 should capture the first REQUEST SENSE or security-protocol result. If the query itself fails, `UFPOST 223` identifies the exact opcode, IDN, LUN, size, and return code.
