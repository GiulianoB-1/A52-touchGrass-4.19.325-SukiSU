# Phase 222 paired SG/RPMB reference trace

## Goal

Compare Samsung `librpmb.so` against the same userspace and storage hardware under a working TouchGrass kernel and the GKI candidate. The first differing SG ioctl, SCSI opcode, UFS LUN, result code, or sense status becomes the next repair target.

## GKI trace

`SGPOST 222` is emitted only when `current->comm` is exactly `qseecomd`.

Recorded metadata:

- SG device index and SCSI host, channel, ID, LUN and type
- ioctl command and final return value
- SG_IO opcode, command length, direction, transfer length and timeout
- SCSI, host and driver status
- residual byte count
- sense length, sense key, ASC and ASCQ

The trace never records CDB bytes beyond the opcode, transfer buffers, RPMB frames, keys or userspace memory.

## Later progress

`BOOTPOST 222` records bounded exec, exec-return and exit milestones for Android init, service managers, storage, graphics and security services. The existing recorder heartbeat, QSEECom, ION, all-open and exit evidence remains active.

This gives the same boot attempt enough coverage to identify a later blocker after RPMB is repaired.

## TouchGrass reference

The same patcher supports `--backend printk` and emits `TGSG 222` and `TGBOOT 222`. A TouchGrass reference build should apply it to commit `6bf351bdf18bdb228db79e66f14a7a9c0178e5d7`, boot with the same ROM userspace, and preserve full dmesg or pstore.

## Comparison

`222_compare_runtime_traces.py` accepts the TouchGrass kernel log and the decoded GKI `events.csv`. It reports the first SG divergence and later boot milestones in JSON, text and normalized CSV forms.
