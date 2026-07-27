#!/usr/bin/env python3
from __future__ import annotations

# Rebuild marker: compile the QSEECOM reserved-memory shmbridge compatibility stage.
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
    rb"A52USR2 (?P<kind>BOOT_EARLY|BOOT_BEGIN|BOOT_READY) (?P<body>.*?) commit="
    + COMMIT.encode() + rb"(?:\r?\n|\x00)",
    re.DOTALL,
)
ION_RESULT_RE = re.compile(
    r"^ION result fd=(?P<fd>-?\d+) len=(?P<len>\d+) "
    r"heap=(?P<heap>[0-9a-fA-F]+) flags=(?P<flags>[0-9a-fA-F]+)$"
)
QSEE_API_RE = re.compile(
    r"^QSEE SEND api req=(?P<req>\d+) rsp=(?P<rsp>\d+)$"
)
QSEE_CORE_RE = re.compile(
    r"^QSEE SEND core id=(?P<appid>\d+) app=(?P<app>\S+) "
    r"req=(?P<req>\d+) rsp=(?P<rsp>\d+)$"
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
                controls.append(
                    {
                        "source": source,
                        "kind": text(found.group("kind")),
                        "body": text(found.group("body")),
                    }
                )
            for found in EVENT_RE.finditer(data):
                matched = True
                raw_copies += 1
                seq = int(found.group("seq"))
                candidate: dict[str, object] = {
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
                current_sources = current["sources"]
                assert isinstance(current_sources, set)
                current_sources.add(source)
                if len(str(candidate["message"])) > len(str(current["message"])):
                    candidate["copies"] = current["copies"]
                    candidate["sources"] = current_sources
                    by_seq[seq] = candidate
            matched_files += int(matched)

    events = [
        Event(
            seq=int(item["seq"]),
            monotonic_ns=int(item["monotonic_ns"]),
            pid=int(item["pid"]),
            tgid=int(item["tgid"]),
            cpu=int(item["cpu"]),
            comm=str(item["comm"]),
            message=str(item["message"]),
            copies=int(item["copies"]),
            sources=tuple(sorted(item["sources"])),  # type: ignore[arg-type]
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
    for prefix in (
        "ION",
        "QSEEINIT",
        "QSEE",
        "DMABUF",
        "SHMBRIDGE",
        "SECUREBUF",
        "SCM",
    ):
        if message.startswith(prefix + " "):
            return prefix
    return "OTHER"


def parameter_probe(events: list[Event]) -> dict[str, object]:
    ion_results: list[dict[str, object]] = []
    qsee_api: list[dict[str, object]] = []
    qsee_core: list[dict[str, object]] = []
    for event in events:
        match = ION_RESULT_RE.fullmatch(event.message)
        if match:
            ion_results.append(
                {
                    "seq": event.seq,
                    "fd": int(match.group("fd")),
                    "len": int(match.group("len")),
                    "heap": int(match.group("heap"), 16),
                    "flags": int(match.group("flags"), 16),
                }
            )
            continue
        match = QSEE_API_RE.fullmatch(event.message)
        if match:
            qsee_api.append(
                {
                    "seq": event.seq,
                    "req": int(match.group("req")),
                    "rsp": int(match.group("rsp")),
                }
            )
            continue
        match = QSEE_CORE_RE.fullmatch(event.message)
        if match:
            qsee_core.append(
                {
                    "seq": event.seq,
                    "appid": int(match.group("appid")),
                    "app": match.group("app"),
                    "req": int(match.group("req")),
                    "rsp": int(match.group("rsp")),
                }
            )
    return {
        "status": "observed" if ion_results or qsee_api or qsee_core else "not-observed",
        "ion_allocation_results": ion_results,
        "qsee_send_api_requests": qsee_api,
        "qsee_send_core_requests": qsee_core,
        "counts": {
            "ion_allocation_results": len(ion_results),
            "qsee_send_api_requests": len(qsee_api),
            "qsee_send_core_requests": len(qsee_core),
        },
        "payload_capture": False,
    }


def output(
    root: Path,
    events: list[Event],
    controls: list[dict[str, str]],
    stats: dict[str, int],
    inputs: list[Path],
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    first_ns = events[0].monotonic_ns if events else 0
    with (root / "secure-events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "seq",
                "monotonic_ns",
                "delta_ms",
                "pid",
                "tgid",
                "cpu",
                "comm",
                "domain",
                "message",
                "copies",
                "sources",
            ]
        )
        for event in events:
            writer.writerow(
                [
                    event.seq,
                    event.monotonic_ns,
                    f"{(event.monotonic_ns - first_ns) / 1_000_000:.3f}"
                    if first_ns
                    else "",
                    event.pid,
                    event.tgid,
                    event.cpu,
                    event.comm,
                    domain(event.message),
                    event.message,
                    event.copies,
                    " | ".join(event.sources),
                ]
            )
    with (root / "secure-events.txt").open("w", encoding="utf-8") as handle:
        for item in controls:
            handle.write(
                f"CONTROL {item['kind']} {item['body']} source={item['source']}\n"
            )
        for event in events:
            delta = (
                (event.monotonic_ns - first_ns) / 1_000_000 if first_ns else 0
            )
            handle.write(
                f"{event.seq:04d} +{delta:10.3f} ms pid={event.pid} "
                f"tgid={event.tgid} cpu={event.cpu} comm={event.comm} "
                f"copies={event.copies} [{domain(event.message)}] "
                f"{event.message}\n"
            )

    domains: dict[str, int] = {}
    for event in events:
        key = domain(event.message)
        domains[key] = domains.get(key, 0) + 1
    gaps: list[int] = []
    if events:
        gaps = sorted(
            set(range(events[0].seq, events[-1].seq + 1))
            - {event.seq for event in events}
        )
    mirrored_copies = sum(event.copies >= 2 for event in events)
    mirrored_sources = sum(len(event.sources) >= 2 for event in events)
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
            "events_with_two_or_more_copies": mirrored_copies,
            "events_seen_in_two_or_more_sources": mirrored_sources,
            "events_seen_once": len(events) - mirrored_copies,
        },
        "domains": dict(sorted(domains.items())),
        "controls": controls,
        "parameter_probe": parameter_probe(events),
        "privacy": (
            "metadata-only; no command/response buffers, keys, tokens, "
            "or process memory"
        ),
    }
    (root / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def self_test() -> None:
    lines = (
        b"A52USR2 BOOT_EARLY stage=mm_init backend=early-mirrored "
        b"metadata_only=1 commit=5a52c0de\n"
        b"A52USR2 seq=1 ns=100 pid=10 tgid=10 cpu=3 comm=skeymast "
        b"msg=ION result fd=7 len=4096 heap=1 flags=0 commit=5a52c0de\n"
        b"A52USR2 seq=2 ns=200 pid=10 tgid=10 cpu=3 comm=skeymast "
        b"msg=QSEE SEND core id=4 app=keymaster req=128 rsp=256 "
        b"commit=5a52c0de\n"
    )
    raw = io.BytesIO(lines).read()
    controls = list(CONTROL_RE.finditer(raw))
    if len(controls) != 1 or text(controls[0].group("kind")) != "BOOT_EARLY":
        raise SystemExit("decoder early-control self-test failed")
    matches = list(EVENT_RE.finditer(raw))
    if len(matches) != 2:
        raise SystemExit("decoder event self-test failed")
    events = [
        Event(
            seq=int(found.group("seq")),
            monotonic_ns=int(found.group("ns")),
            pid=int(found.group("pid")),
            tgid=int(found.group("tgid")),
            cpu=int(found.group("cpu")),
            comm=text(found.group("comm")),
            message=text(found.group("msg")),
            copies=1,
            sources=("self-test",),
        )
        for found in matches
    ]
    probe = parameter_probe(events)
    if probe["counts"] != {
        "ion_allocation_results": 1,
        "qsee_send_api_requests": 0,
        "qsee_send_core_requests": 1,
    }:
        raise SystemExit("decoder parameter-probe self-test failed")
    if probe["payload_capture"] is not False:
        raise SystemExit("decoder privacy self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decode A52 unified secure-recorder RAMOOPS data"
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("a52-unified-secure-decoded")
    )
    args = parser.parse_args()
    self_test()
    missing = [str(path) for path in args.inputs if not path.exists()]
    if missing:
        raise SystemExit("missing input: " + ", ".join(missing))
    events, controls, stats = parse(args.inputs)
    output(args.output, events, controls, stats, args.inputs)
    print(
        json.dumps(
            {"status": "ok", **stats, "output": str(args.output)},
            sort_keys=True,
        )
    )
    return 0 if events else 2


if __name__ == "__main__":
    raise SystemExit(main())
