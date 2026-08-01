#!/usr/bin/env python3
"""Phase 199 CRC32C format adapter for the Phase 179 RS decoder core."""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import random
import struct
import sys
from pathlib import Path
from typing import Sequence


def load_legacy():
    path = Path(__file__).with_name("decode-a52-r179-rs-recorder.py")
    if not path.is_file():
        raise RuntimeError(f"missing RS decoder core beside this file: {path}")
    spec = importlib.util.spec_from_file_location("a52_r179_core_for_r199", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load RS decoder core: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_legacy()

PHASE = 199
PREFIX = b"R99"
TEXT_BYTES = CORE.TEXT_BYTES
BASE64_BYTES = CORE.BASE64_BYTES
DATA_BYTES = CORE.DATA_BYTES
PARITY_BYTES = CORE.PARITY_BYTES
CODE_BYTES = CORE.CODE_BYTES
MAGIC = b"A52R0199"
VERSION = 2
HEADER_LEN = 60
COMMIT = 0x5A52C199
RECORD_STRUCT = struct.Struct("<8sHHQQIIIHH16s89sII")
CRC_OFFSET = DATA_BYTES - 8
assert RECORD_STRUCT.size == DATA_BYTES

DecodeFailure = CORE.DecodeFailure
DecodedRecord = CORE.DecodedRecord
RS = CORE.RS
PERSISTENT_RAM_SIG = CORE.PERSISTENT_RAM_SIG
BANK_BYTES = CORE.BANK_BYTES
BANK_HEADER_BYTES = CORE.BANK_HEADER_BYTES
RAMOOPS_TOTAL_BYTES = CORE.RAMOOPS_TOTAL_BYTES


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def encode_record_for_test(
    seq: int, message: str, *, monotonic_ns: int = 123456789, bank: str = "test"
) -> bytes:
    message_bytes = message.encode("utf-8")[:89]
    message_padded = message_bytes.ljust(89, b"\0")
    comm = b"self-test".ljust(16, b"\0")
    data = bytearray(
        RECORD_STRUCT.pack(
            MAGIC,
            VERSION,
            HEADER_LEN,
            seq,
            monotonic_ns,
            10,
            10,
            2,
            0,
            len(message_bytes),
            comm,
            message_padded,
            0,
            COMMIT,
        )
    )
    struct.pack_into("<I", data, CRC_OFFSET, crc32c(bytes(data[:CRC_OFFSET])))
    codeword = RS.encode(bytes(data))
    transport = PREFIX + base64.b64encode(codeword)
    assert len(transport) == TEXT_BYTES
    return transport


def parse_record_data(
    data: bytes,
    *,
    bank: str,
    offset: int,
    prefix_distance: int,
    erasures: int,
    corrected_symbols: int,
) -> DecodedRecord:
    if len(data) != DATA_BYTES:
        raise DecodeFailure("incorrect data length")
    (
        magic,
        version,
        header_len,
        seq,
        monotonic_ns,
        pid,
        tgid,
        cpu,
        kind,
        message_len,
        comm_raw,
        message_raw,
        stored_crc32c,
        commit,
    ) = RECORD_STRUCT.unpack(data)
    if magic != MAGIC:
        raise DecodeFailure("record magic mismatch")
    if version != VERSION:
        raise DecodeFailure("record version mismatch")
    if header_len != HEADER_LEN:
        raise DecodeFailure("record header length mismatch")
    if commit != COMMIT:
        raise DecodeFailure("record commit mismatch")
    if stored_crc32c != crc32c(data[:CRC_OFFSET]):
        raise DecodeFailure("record CRC32C mismatch")
    if not (1 <= seq <= 10_000_000_000):
        raise DecodeFailure("implausible sequence number")
    if message_len > len(message_raw):
        raise DecodeFailure("invalid message length")
    comm = comm_raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")
    message = message_raw[:message_len].decode("utf-8", errors="replace")
    return DecodedRecord(
        bank=bank,
        offset=offset,
        prefix_distance=prefix_distance,
        erasures=erasures,
        corrected_symbols=corrected_symbols,
        seq=seq,
        monotonic_ns=monotonic_ns,
        pid=pid,
        tgid=tgid,
        cpu=cpu,
        kind=kind,
        comm=comm,
        message=message,
        raw_data_hex=data.hex(),
    )


CORE.PHASE = PHASE
CORE.PREFIX = PREFIX
CORE.MAGIC = MAGIC
CORE.VERSION = VERSION
CORE.HEADER_LEN = HEADER_LEN
CORE.COMMIT = COMMIT
CORE.RECORD_STRUCT = RECORD_STRUCT
CORE.parse_record_data = parse_record_data
CORE.encode_record_for_test = encode_record_for_test

decode_base64_with_erasures = CORE.decode_base64_with_erasures
hamming_prefix = CORE.hamming_prefix
try_decode_transport = CORE.try_decode_transport
chronological_ring = CORE.chronological_ring
candidate_offsets = CORE.candidate_offsets
decode_bank = CORE.decode_bank
select_best = CORE.select_best
find_snapshots = CORE.find_snapshots


def self_test() -> None:
    rng = random.Random(0xA52199)
    for errors in (0, 1, 5, 16):
        for iteration in range(20):
            transport = encode_record_for_test(
                iteration + 1, f"DRMPOST self errors={errors} iter={iteration}"
            )
            codeword = bytearray(base64.b64decode(transport[3:]))
            for position in rng.sample(range(CODE_BYTES), errors):
                codeword[position] ^= rng.randrange(1, 256)
            damaged = PREFIX + base64.b64encode(codeword)
            record = try_decode_transport(damaged, bank="test", offset=0)
            if record.seq != iteration + 1:
                raise AssertionError("RS recovery returned the wrong sequence")

    transport = encode_record_for_test(100, "DRMPOST CRC rejection")
    codeword = bytearray(base64.b64decode(transport[3:]))
    codeword[CRC_OFFSET - 1] ^= 0x01
    reencoded = RS.encode(bytes(codeword[:DATA_BYTES]))
    try:
        try_decode_transport(PREFIX + base64.b64encode(reencoded), bank="crc", offset=0)
    except DecodeFailure as exc:
        if "CRC32C" not in str(exc):
            raise
    else:
        raise AssertionError("CRC32C mismatch was accepted")
    print("phase199 CRC32C decoder self-test: PASS")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test the Phase 199 CRC32C RS decoder core")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if not args.self_test:
        parser.error("use the triple decoder for captures, or pass --self-test")
    self_test()
    print(json.dumps({"status": "ok", "phase": PHASE, "crc": "CRC32C"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
