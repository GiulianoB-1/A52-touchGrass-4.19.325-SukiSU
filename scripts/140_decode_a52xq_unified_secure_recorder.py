#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

COMMIT = "5a52c0de"
EVENT_RE = re.compile(
    rb"A52USR2 seq=(?P<seq>\d+) ns=(?P<ns>\d+) pid=(?P<pid>-?\d+) "
    rb"tgid=(?P<tgid>-?\d+) cpu=(?P<cpu>\d+) comm=(?P<comm>[^\x00\r\n ]{1,16}) "
    rb"msg=(?P<msg>.*?) commit=" + COMMIT.encode() + rb"(?:\r?\n|\x00)",
    re.DOTALL,
)
CONTROL_RE = re.compile(
    rb"A52USR2 (?P<kind>BOOT_BEGIN|BOOT_READY) (?P<body>.*?) commit="
    + COMMIT.encode()
    + rb"(?:\r?\n|\x00)",
    re.DOTALL,
)


@dataclass(frozen=True)
class Event:
    seq: int
    monotonic_ns: int
    pid: int
    tgid: int
    cpu: int
    comm: str
    message: str
    sources: tuple[str, ...]


def iter_regular_files(path: Path) -> Iterable[tuple[str, bytes]]:
    if path.is_dir():
        for item in sorted(path.rglob("*")):
            if item.is_file():
                try:
                    yield str(item), item.read_bytes()
                except OSError:
                    continue
        return
    suffixes = "".join(path.suffixes[-2:]).lower()
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                try:
                    yield f"{path}!{info.filename}", archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    continue
        return
    if suffixes in {".tar.gz", ".tgz"} or path.suffix.lower() == ".tar":
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                yield f"{path}!{member.name}", handle.read()
        return
    yield str(path), path.read_bytes()


def clean_text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").replace("\x00", "").strip()


def parse_inputs(paths: list[Path]) -> tuple[list[Event], list[dict[str, str]], dict[str, int]]:
    by_seq: dict[int, dict[str, object]] = {}
    controls: list[dict[str, str]] = []
    file_count = 0
    matched_files = 0
    raw_matches = 0

    for path in paths:
        for source, data in iter_regular_files(path):
            file_count += 1
            source_matched = False
            for match in CONTROL_RE.finditer(data):
                source_matched = True
                controls.append(
                    {
                        "source": source,
                        "kind": clean_text(match.group("kind")),
                        "body": clean_text(match.group("body")),
                    }
                )
            for match in EVENT_RE.finditer(data):
                source_matched = True
                raw_matches += 1
                seq = int(match.group("seq"))
                candidate = {
                    "seq": seq,
                    "monotonic_ns": int(match.group("ns")),
                    "pid": int(match.group("pid")),
                    "tgid": int(match.group("tgid")),
                    "cpu": int(match.group("cpu")),
                    "comm": clean_text(match.group("comm")),
                    "message": clean_text(match.group("msg")),
                }
                current = by_seq.get(seq)
                if current is None:
                    candidate["sources"] = {source}
                    by_seq[seq] = candidate
                else:
                    current["sources"].add(source)  # type: ignore[index]
                    # Preserve the longest valid copy when one bank was truncated.
                    if len(str(candidate["message"])) > len(str(current["message"])):
                        sources = current["sources"]
                        candidate["sources"] = sources
                        by_seq[seq] = candidate
            if source_matched:
                matched_files += 1

    events = [
        Event(
            seq=int(item["seq"]),
            monotonic_ns=int(item["monotonic_ns"]),
            pid=int(item["pid"]),
            tgid=int(item["tgid"]),
            cpu=int(item["cpu"]),
            comm=str(item["comm"]),
            message=str(item["message"]),
            sources=tuple(sorted(item["sources"])),  # type: ignore[arg-type]
        )
        for _, item in sorted(by_seq.items())
    ]
    stats = {
        "files_scanned": file_count,
        "files_with_recorder_data": matched_files,
        "raw_event_copies": raw_matches,
        "unique_events": len(events),
        "control_records": len(controls),
    }
    return events, controls, stats


def classify(message: str) -> str:
    if message.startswith("ION "):
        return "ION"
    if message.startswith("QSEE "):
        return "QSEE"
    if message.startswith("SHMBRIDGE ") or "qtee_shmbridge" in message:
        return "SHMBRIDGE"
    if message.startswith("SECUREBUF ") or "hyp_assign" in message or "secure_buffer" in message:
        return "SECUREBUF"
    if message.startswith("SCM ") or "scm_call" in message:
        return "SCM"
    return "OTHER"


def write_outputs(output: Path, events: list[Event], controls: list[dict[str, str]],
                  stats: dict[str, int], inputs: list[Path]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "secure-events.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["seq", "monotonic_ns", "delta_ms", "pid", "tgid", "cpu", "comm", "domain", "message", "copies", "sources"]
        )
        first_ns = events[0].monotonic_ns if events else 0
        for event in events:
            writer.writerow(
                [
                    event.seq,
                    event.monotonic_ns,
                    f"{(event.monotonic_ns - first_ns) / 1_000_000:.3f}" if first_ns else "",
                    event.pid,
                    event.tgid,
                    event.cpu,
                    event.comm,
                    classify(event.message),
                    event.message,
                    len(event.sources),
                    " | ".join(event.sources),
                ]
            )

    text_path = output / "secure-events.txt"
    with text_path.open("w", encoding="utf-8") as handle:
        for control in controls:
            handle.write(f"CONTROL {control['kind']} {control['body']} source={control['source']}\n")
        first_ns = events[0].monotonic_ns if events else 0
        for event in events:
            delta_ms = (event.monotonic_ns - first_ns) / 1_000_000 if first_ns else 0
            handle.write(
                f"{event.seq:04d} +{delta_ms:10.3f} ms pid={event.pid} tgid={event.tgid} "
                f"cpu={event.cpu} comm={event.comm} [{classify(event.message)}] {event.message}\n"
            )

    domains: dict[str, int] = {}
    mirrored = 0
    for event in events:
        domain = classify(event.message)
        domains[domain] = domains.get(domain, 0) + 1
        if len(event.sources) >= 2:
            mirrored += 1
    gaps = []
    if events:
        expected = set(range(events[0].seq, events[-1].seq + 1))
        gaps = sorted(expected - {event.seq for event in events})
    summary = {
        "status": "decoded" if events else "no-valid-events-found",
        "format": "A52USR2",
        "commit_marker": COMMIT,
        "inputs": [str(path) for path in inputs],
        "input_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in inputs
            if path.is_file()
        },
        "stats": stats,
        "sequence": {
            "first": events[0].seq if events else None,
            "last": events[-1].seq if events else None,
            "gaps": gaps,
        },
        "mirroring": {
            "events_seen_in_two_or_more_sources": mirrored,
            "events_seen_once": len(events) - mirrored,
        },
        "domains": dict(sorted(domains.items())),
        "controls": controls,
        "privacy": "metadata-only; no command/response buffers, keys, tokens, or process memory",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


def self_test() -> None:
    line = (
        b"xxA52USR2 seq=2 ns=200 pid=10 tgid=10 cpu=3 comm=skeymast "
        b"msg=QSEE exit fn=qseecom_ioctl cmd=0xc0209703 ret=0 commit=5a52c0de\n"
    )
    with io.BytesIO(line) as handle:
        match = EVENT_RE.search(handle.read())
    if not match or int(match.group("seq")) != 2 or match.group("msg").decode()[-5:] != "ret=0":
        raise SystemExit("decoder self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode A52 unified secure-recorder RAMOOPS data")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("a52-unified-secure-decoded"))
    args = parser.parse_args()
    self_test()
    missing = [str(path) for path in args.inputs if not path.exists()]
    if missing:
        raise SystemExit("missing input: " + ", ".join(missing))
    events, controls, stats = parse_inputs(args.inputs)
    write_outputs(args.output, events, controls, stats, args.inputs)
    print(json.dumps({"status": "ok", **stats, "output": str(args.output)}, sort_keys=True))
    return 0 if events else 2


if __name__ == "__main__":
    raise SystemExit(main())
