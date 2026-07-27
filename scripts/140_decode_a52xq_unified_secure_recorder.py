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
    + COMMIT.encode() + rb"(?:\r?\n|\x00)", re.DOTALL,
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
    copies: int
    sources: tuple[str, ...]


def files(path: Path) -> Iterable[tuple[str, bytes]]:
    if path.is_dir():
        for item in sorted(path.rglob("*")):
            if item.is_file():
                try:
                    yield str(item), item.read_bytes()
                except OSError:
                    pass
        return
    suffixes = "".join(path.suffixes[-2:]).lower()
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    try:
                        yield f"{path}!{info.filename}", archive.read(info)
                    except (OSError, RuntimeError, zipfile.BadZipFile):
                        pass
        return
    if suffixes in {".tar.gz", ".tgz"} or path.suffix.lower() == ".tar":
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                if member.isfile():
                    handle = archive.extractfile(member)
                    if handle is not None:
                        yield f"{path}!{member.name}", handle.read()
        return
    yield str(path), path.read_bytes()


def text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").replace("\x00", "").strip()


def parse(paths: list[Path]) -> tuple[list[Event], list[dict[str, str]], dict[str, int]]:
    by_seq: dict[int, dict[str, object]] = {}
    controls: list[dict[str, str]] = []
    scanned = matched_files = raw_copies = 0
    for path in paths:
        for source, data in files(path):
            scanned += 1
            matched = False
            for found in CONTROL_RE.finditer(data):
                matched = True
                controls.append({
                    "source": source,
                    "kind": text(found.group("kind")),
                    "body": text(found.group("body")),
                })
            for found in EVENT_RE.finditer(data):
                matched = True
                raw_copies += 1
                seq = int(found.group("seq"))
                candidate = {
                    "seq": seq,
                    "monotonic_ns": int(found.group("ns")),
                    "pid": int(found.group("pid")),
                    "tgid": int(found.group("tgid")),
                    "cpu": int(found.group("cpu")),
                    "comm": text(found.group("comm")),
                    "message": text(found.group("msg")),
                }
                current = by_seq.get(seq)
                if current is None:
                    candidate["copies"] = 1
                    candidate["sources"] = {source}
                    by_seq[seq] = candidate
                    continue
                current["copies"] = int(current["copies"]) + 1
                current["sources"].add(source)  # type: ignore[index]
                if len(str(candidate["message"])) > len(str(current["message"])):
                    candidate["copies"] = current["copies"]
                    candidate["sources"] = current["sources"]
                    by_seq[seq] = candidate
            matched_files += int(matched)

    events = [
        Event(
            seq=int(item["seq"]), monotonic_ns=int(item["monotonic_ns"]),
            pid=int(item["pid"]), tgid=int(item["tgid"]), cpu=int(item["cpu"]),
            comm=str(item["comm"]), message=str(item["message"]),
            copies=int(item["copies"]), sources=tuple(sorted(item["sources"])),  # type: ignore[arg-type]
        )
        for _, item in sorted(by_seq.items())
    ]
    return events, controls, {
        "files_scanned": scanned,
        "files_with_recorder_data": matched_files,
        "raw_event_copies": raw_copies,
        "unique_events": len(events),
        "control_records": len(controls),
    }


def domain(message: str) -> str:
    for prefix in ("ION", "QSEEINIT", "QSEE", "DMABUF", "SHMBRIDGE", "SECUREBUF", "SCM"):
        if message.startswith(prefix + " "):
            return prefix
    return "OTHER"


def output(root: Path, events: list[Event], controls: list[dict[str, str]],
           stats: dict[str, int], inputs: list[Path]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    first_ns = events[0].monotonic_ns if events else 0
    with (root / "secure-events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["seq", "monotonic_ns", "delta_ms", "pid", "tgid", "cpu",
                         "comm", "domain", "message", "copies", "sources"])
        for event in events:
            writer.writerow([
                event.seq, event.monotonic_ns,
                f"{(event.monotonic_ns - first_ns) / 1_000_000:.3f}" if first_ns else "",
                event.pid, event.tgid, event.cpu, event.comm, domain(event.message),
                event.message, event.copies, " | ".join(event.sources),
            ])
    with (root / "secure-events.txt").open("w", encoding="utf-8") as handle:
        for item in controls:
            handle.write(f"CONTROL {item['kind']} {item['body']} source={item['source']}\n")
        for event in events:
            delta = (event.monotonic_ns - first_ns) / 1_000_000 if first_ns else 0
            handle.write(
                f"{event.seq:04d} +{delta:10.3f} ms pid={event.pid} tgid={event.tgid} "
                f"cpu={event.cpu} comm={event.comm} copies={event.copies} "
                f"[{domain(event.message)}] {event.message}\n"
            )

    domains: dict[str, int] = {}
    for event in events:
        key = domain(event.message)
        domains[key] = domains.get(key, 0) + 1
    gaps: list[int] = []
    if events:
        gaps = sorted(set(range(events[0].seq, events[-1].seq + 1)) - {e.seq for e in events})
    mirrored = sum(event.copies >= 2 for event in events)
    summary = {
        "status": "decoded" if events else "no-valid-events-found",
        "format": "A52USR2",
        "commit_marker": COMMIT,
        "inputs": [str(path) for path in inputs],
        "input_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in inputs if path.is_file()
        },
        "stats": stats,
        "sequence": {
            "first": events[0].seq if events else None,
            "last": events[-1].seq if events else None,
            "gaps": gaps,
        },
        "mirroring": {
            "events_with_two_or_more_copies": mirrored,
            "events_seen_in_two_or_more_sources": mirrored,
            "events_seen_once": len(events) - mirrored,
        },
        "domains": dict(sorted(domains.items())),
        "controls": controls,
        "privacy": "metadata-only; no command/response buffers, keys, tokens, or process memory",
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


def self_test() -> None:
    line = (
        b"A52USR2 seq=2 ns=200 pid=10 tgid=10 cpu=3 comm=skeymast "
        b"msg=QSEE exit fn=qseecom_ioctl cmd=0xc0209703 ret=0 commit=5a52c0de\n"
    )
    match = EVENT_RE.search(io.BytesIO(line).read())
    if not match or int(match.group("seq")) != 2 or text(match.group("msg"))[-5:] != "ret=0":
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
    events, controls, stats = parse(args.inputs)
    output(args.output, events, controls, stats, args.inputs)
    print(json.dumps({"status": "ok", **stats, "output": str(args.output)}, sort_keys=True))
    return 0 if events else 2


if __name__ == "__main__":
    raise SystemExit(main())
