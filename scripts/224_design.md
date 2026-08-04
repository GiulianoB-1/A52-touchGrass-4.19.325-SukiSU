# Phase 224: early-userspace storage boundary recorder

## Hardware evidence

Phase 223 hardware validation fixed the Samsung UFS query compatibility gap.
`qseecomd` now completes `UFS_IOCTL_QUERY`, REQUEST SENSE, SECURITY PROTOCOL
OUT (`0xB5`) and SECURITY PROTOCOL IN (`0xA2`) with clean SCSI, host, driver,
residual and sense results.

The kernel remains alive beyond 75 seconds, but Android does not execute
`zygote`, `surfaceflinger`, `bootanim` or `system_server`. The last relevant
bootstrap helper observed is `/system/bin/vdc` at approximately 18.95 seconds.

## Scope

Phase 224 is observation-only. It adds bounded `USRPOST 224` kernel metadata
for the `vdc`/`vold` boundary:

- `vdc` exec path, argument count and a whitelisted command namespace,
- lengths only for later `vdc` arguments,
- socket `connect()` entry and return, including address family but no address,
- native `ioctl()` entry and return, including fd, command and result.

An entry without a matching return identifies a blocking syscall. A matching
negative return identifies an immediate userspace-visible failure.

## Privacy

The recorder does not store passwords, keys, argument payloads, socket paths,
socket addresses, ioctl buffers, Binder transactions, process memory or file
contents. Only known-safe `vdc` command namespaces may be printed. Unknown
argument text is reported as `other`; later arguments are represented only by
bounded lengths.

## Behavior

No UFS, RPMB, QSEE, SG, storage, Binder, init, scheduling or power behavior is
changed. Existing Phase 222 and Phase 223 tracing remains present.

## Expected decision

The next hardware capture should show one of these boundaries:

1. `vdc` enters a socket connect and never returns,
2. socket connect returns a concrete error,
3. `vdc` enters a Binder or device ioctl and never returns,
4. ioctl returns a concrete error,
5. `vdc` performs no connect/ioctl, indicating the next probe must move into a
   different userspace wait path.
