#!/usr/bin/env python3
"""Diagnose Galaxy A52 REFGEN/display RAMOOPS recorder evidence.

The tool consumes the CSV output from the standard A52USR2 decoder and the
corruption-tolerant mirrored decoder. When a raw capture is supplied it can run
both sibling decoders automatically. It never reads secure payload contents.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

CONFIDENCE = {"exact": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
CRITICAL = ("REFGEN ", "DISP ", "HB ", "WDT ")

PATTERNS = {
    "register": re.compile(r"REFGEN driver_register rc=(-?\d+)$"),
    "probe": re.compile(r"REFGEN probe enter compat=(\S+)$"),
    "failure": re.compile(r"REFGEN probe fail stage=(\S+) rc=(-?\d+)$"),
    "mapped": re.compile(r"REFGEN mapped start=0x([0-9a-fA-F]+) size=(\d+) raw=0x([0-9a-fA-F]+)$"),
    "ready": re.compile(r"REFGEN probe ready initial_enabled=(-?\d+)$"),
    "state": re.compile(r"REFGEN kona_state raw=0x([0-9a-fA-F]+) enabled=([01])$"),
    "enable": re.compile(r"REFGEN kona_enable raw=0x([0-9a-fA-F]+)->0x([0-9a-fA-F]+) enabled=([01])$"),
    "disable": re.compile(r"REFGEN kona_disable raw=0x([0-9a-fA-F]+)->0x([0-9a-fA-F]+) enabled=([01])$"),
    "heartbeat": re.compile(r"HB tick=(\d+) online=(\d+) run=(\d+) j=(\d+)$"),
    "watchdog": re.compile(r"WDT disarm before=([01]) after=([01])$"),
    "enter": re.compile(r"DISP enter fn=(\S+)$"),
    "exit": re.compile(r"DISP exit fn=(\S+) us=(\d+)$"),
}


@dataclass(frozen=True)
class Event:
    order: int
    seq: int | None
    monotonic_ns: int | None
    message: str
    confidence: str
    source: str
    offset: int | None = None

    def key(self) -> tuple[int, int, int]:
        if self.monotonic_ns is not None:
            return (0, self.monotonic_ns, self.order)
        if self.seq is not None:
            return (1, self.seq, self.order)
        return (2, self.order, self.order)


def integer(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def read_events(path: Path, source: str, default_confidence: str) -> list[Event]:
    if not path.is_file():
        return []
    rows: list[Event] = []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        for order, row in enumerate(csv.DictReader(handle)):
            message = (row.get("message") or "").strip()
            if not message:
                continue
            confidence_value = (row.get("confidence") or default_confidence).lower()
            rows.append(Event(
                order=order,
                seq=integer(row.get("seq")),
                monotonic_ns=integer(row.get("monotonic_ns")),
                message=message,
                confidence=confidence_value if confidence_value in CONFIDENCE else "unknown",
                source=source,
                offset=integer(row.get("offset")),
            ))
    return rows


def merge_events(standard_csv: Path, mirrored_csv: Path) -> tuple[list[Event], dict[str, int]]:
    exact = read_events(standard_csv, "standard", "exact")
    mirrored = read_events(mirrored_csv, "mirrored", "high")
    selected = list(exact)
    exact_pairs = {(item.seq, item.message) for item in exact}
    exact_sequences = {item.seq for item in exact if item.seq is not None}
    exact_messages = {item.message for item in exact}
    for item in mirrored:
        if item.confidence == "low":
            continue
        if (item.seq, item.message) in exact_pairs:
            continue
        if item.seq is not None and item.seq in exact_sequences:
            continue
        if item.seq is None and item.message in exact_messages:
            continue
        selected.append(item)
    selected.sort(key=Event.key)
    ordered = [Event(index, item.seq, item.monotonic_ns, item.message,
                     item.confidence, item.source, item.offset)
               for index, item in enumerate(selected)]
    return ordered, {
        "standard_events": len(exact),
        "mirrored_events": len(mirrored),
        "mirrored_high": sum(item.confidence == "high" for item in mirrored),
        "mirrored_medium": sum(item.confidence == "medium" for item in mirrored),
        "mirrored_low": sum(item.confidence == "low" for item in mirrored),
        "selected_events": len(ordered),
    }


def found(events: Iterable[Event], name: str) -> list[tuple[Event, re.Match[str]]]:
    pattern = PATTERNS[name]
    answer = []
    for event in events:
        match = pattern.fullmatch(event.message)
        if match:
            answer.append((event, match))
    return answer


def event_dict(item: tuple[Event, re.Match[str]] | None) -> dict[str, object] | None:
    return asdict(item[0]) if item else None


def confidence(events: Iterable[Event]) -> str:
    values = [item.confidence for item in events]
    return min(values, key=lambda value: CONFIDENCE.get(value, 99)) if values else "none"


def display_summary(events: list[Event]) -> dict[str, object]:
    stack: list[tuple[str, Event]] = []
    completed: list[dict[str, object]] = []
    last: Event | None = None
    for event in events:
        enter = PATTERNS["enter"].fullmatch(event.message)
        exit_match = PATTERNS["exit"].fullmatch(event.message)
        if enter:
            last = event
            stack.append((enter.group(1), event))
            continue
        if not exit_match:
            continue
        last = event
        function = exit_match.group(1)
        index = next((i for i in range(len(stack) - 1, -1, -1)
                      if stack[i][0] == function), None)
        if index is None:
            completed.append({"function": function, "orphan_exit": True,
                              "duration_us": int(exit_match.group(2)),
                              "exit_order": event.order})
            continue
        _, entered = stack.pop(index)
        completed.append({"function": function, "orphan_exit": False,
                          "duration_us": int(exit_match.group(2)),
                          "enter_order": entered.order, "exit_order": event.order})
    unmatched = [{"function": function, **asdict(event)} for function, event in stack]
    return {
        "event_count": sum(item.message.startswith("DISP ") for item in events),
        "completed": completed,
        "unmatched_entries": unmatched,
        "last_event": asdict(last) if last else None,
    }


def summaries(events: list[Event]) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    refgen_events = [item for item in events if item.message.startswith("REFGEN ")]
    register = found(events, "register")
    probe = found(events, "probe")
    failure = found(events, "failure")
    mapped = found(events, "mapped")
    ready = found(events, "ready")
    states = found(events, "state")
    enables = found(events, "enable")
    disables = found(events, "disable")
    refgen = {
        "event_count": len(refgen_events),
        "confidence": confidence(refgen_events),
        "driver_register": {"observed": bool(register), "rc": int(register[-1][1].group(1)) if register else None,
                            "event": event_dict(register[-1] if register else None)},
        "probe_enter": {"observed": bool(probe), "compat": probe[-1][1].group(1) if probe else None,
                        "event": event_dict(probe[-1] if probe else None)},
        "probe_failure": {"observed": bool(failure), "stage": failure[-1][1].group(1) if failure else None,
                          "rc": int(failure[-1][1].group(2)) if failure else None,
                          "event": event_dict(failure[-1] if failure else None)},
        "mapping": {"observed": bool(mapped),
                    "start": int(mapped[-1][1].group(1), 16) if mapped else None,
                    "size": int(mapped[-1][1].group(2)) if mapped else None,
                    "raw": int(mapped[-1][1].group(3), 16) if mapped else None},
        "probe_ready": {"observed": bool(ready), "initial_enabled": int(ready[-1][1].group(1)) if ready else None,
                        "event": event_dict(ready[-1] if ready else None)},
        "state_checks": [{"raw": int(match.group(1), 16), "enabled": int(match.group(2)),
                          "event": asdict(event)} for event, match in states],
        "enable_calls": [{"before": int(match.group(1), 16), "after": int(match.group(2), 16),
                          "enabled": int(match.group(3)), "event": asdict(event)} for event, match in enables],
        "disable_calls": [{"before": int(match.group(1), 16), "after": int(match.group(2), 16),
                           "enabled": int(match.group(3)), "event": asdict(event)} for event, match in disables],
    }
    heartbeat_rows = found(events, "heartbeat")
    heartbeat = {
        "count": len(heartbeat_rows),
        "first_tick": int(heartbeat_rows[0][1].group(1)) if heartbeat_rows else None,
        "last_tick": int(heartbeat_rows[-1][1].group(1)) if heartbeat_rows else None,
        "latest_event": event_dict(heartbeat_rows[-1] if heartbeat_rows else None),
    }
    watchdog_rows = found(events, "watchdog")
    watchdog = {
        "observed": bool(watchdog_rows),
        "before": int(watchdog_rows[-1][1].group(1)) if watchdog_rows else None,
        "after": int(watchdog_rows[-1][1].group(2)) if watchdog_rows else None,
        "disarmed": bool(watchdog_rows and watchdog_rows[-1][1].group(2) == "0"),
        "event": event_dict(watchdog_rows[-1] if watchdog_rows else None),
    }
    return refgen, heartbeat, watchdog


def classify(events: list[Event], refgen: dict[str, object], display: dict[str, object],
             heartbeat: dict[str, object], screen: str) -> dict[str, str]:
    if not events:
        return verdict("no_recorder_events", "No usable A52USR2 events were recovered", "low",
                       "The capture is missing, incomplete, or too damaged.",
                       "Repeat collection and preserve the untouched 1 MiB RAMOOPS image.")
    driver = refgen["driver_register"]
    probe = refgen["probe_enter"]
    failure = refgen["probe_failure"]
    ready = refgen["probe_ready"]
    enables = refgen["enable_calls"]
    assert isinstance(driver, dict) and isinstance(probe, dict)
    assert isinstance(failure, dict) and isinstance(ready, dict) and isinstance(enables, list)
    conf = str(refgen.get("confidence", "unknown"))
    if not driver["observed"]:
        return verdict("refgen_registration_missing", "REFGEN registration was not recorded", conf,
                       "The initcall did not run, its record was lost, or another image was flashed.",
                       "Verify the boot.img SHA-256 and inspect BOOT_EARLY and heartbeat records.")
    if driver["rc"] not in (0, None):
        return verdict("refgen_registration_failed", f"REFGEN registration failed with rc={driver['rc']}", conf,
                       "The platform driver never became available.",
                       "Resolve platform_driver_register before changing DSI code.")
    if not probe["observed"]:
        return verdict("refgen_probe_missing", "REFGEN registered but its DT probe never started", conf,
                       "The stock provider node did not populate or match.",
                       "Audit node status, compatible, parent population, and platform-device creation.")
    if failure["observed"]:
        return verdict("refgen_probe_failed", f"REFGEN probe failed at {failure['stage']} with rc={failure['rc']}", conf,
                       "The provider matched but could not finish registration.",
                       f"Fix only the recorded probe stage {failure['stage']}.")
    if not ready["observed"]:
        return verdict("refgen_probe_incomplete", "REFGEN probe did not reach ready", conf,
                       "The probe stalled or the final record was lost.",
                       "Instrument the interval after the last REFGEN marker.")
    successful_enable = any(isinstance(item, dict) and item.get("enabled") == 1 for item in enables)
    if not successful_enable:
        return verdict("refgen_not_enabled", "REFGEN registered but no successful enable was recorded", conf,
                       "The DSI consumer may not have requested the supply or the write did not latch.",
                       "Inspect refgen-supply acquisition and regulator_bulk_enable around DSI prepare.")
    unmatched = display.get("unmatched_entries")
    assert isinstance(unmatched, list)
    if screen == "stable":
        return verdict("refgen_hypothesis_supported", "REFGEN enabled and the display remained stable", conf,
                       "The missing REFGEN provider was the leading black-screen cause.",
                       "Repeat one controlled boot, then prepare a recorder-free candidate with the same REFGEN port.")
    if screen == "black" and unmatched:
        function = str(unmatched[-1].get("function"))
        return verdict("refgen_operational_display_scope_stalled",
                       f"REFGEN enabled, then display stalled in {function}", conf,
                       "REFGEN is operational; the remaining failure is inside the next recorded display boundary.",
                       f"Add narrow markers inside {function}; do not change unrelated display code.")
    if screen == "black":
        return verdict("refgen_operational_black_screen_later",
                       "REFGEN enabled but the black screen occurred after recorded display scopes", conf,
                       "The remaining fault is later than the current probes or outside their coverage.",
                       "Use the final critical timeline event to add one deeper probe layer.")
    return verdict("refgen_operational_result_unknown", "REFGEN appears operational; physical display result is unknown", conf,
                   "The capture alone cannot prove whether the screen remained usable.",
                   "Reclassify the same capture with --screen-result stable or black.")


def verdict(code: str, title: str, confidence_value: str, meaning: str, next_action: str) -> dict[str, str]:
    return {"code": code, "title": title, "confidence": confidence_value,
            "meaning": meaning, "next_action": next_action}


def run_decoder(script: Path, capture: Path, output: Path) -> dict[str, object]:
    if not script.is_file():
        return {"status": "missing", "script": str(script), "returncode": None}
    result = subprocess.run([sys.executable, str(script), str(capture), "--output", str(output)],
                            text=True, capture_output=True, check=False)
    return {"status": "ok" if result.returncode == 0 else "no-events" if result.returncode == 2 else "failed",
            "script": str(script), "returncode": result.returncode,
            "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def write_outputs(output: Path, report: dict[str, object], events: list[Event]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "diagnosis.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    result = report["verdict"]
    assert isinstance(result, dict)
    lines = ["# A52 REFGEN display diagnosis", "", f"**Verdict:** {result['title']}", "",
             f"**Code:** `{result['code']}`  ", f"**Confidence:** `{result['confidence']}`", "",
             "## Meaning", "", str(result["meaning"]), "", "## Next action", "",
             str(result["next_action"]), "", "## Evidence", "",
             f"- Selected events: {report['decoder']['selected_events']}",
             f"- REFGEN: {report['refgen']}", f"- Display: {report['display']}",
             f"- Heartbeat: {report['heartbeat']}", f"- Watchdog: {report['watchdog']}", ""]
    (output / "diagnosis.md").write_text("\n".join(lines), encoding="utf-8")
    with (output / "critical-timeline.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order", "seq", "monotonic_ns", "message", "confidence", "source", "offset"])
        for item in events:
            if item.message.startswith(CRITICAL):
                writer.writerow([item.order, item.seq or "", item.monotonic_ns or "", item.message,
                                 item.confidence, item.source, item.offset or ""])


def diagnose(standard_csv: Path, mirrored_csv: Path, output: Path, screen: str,
             decoder_runs: dict[str, object] | None = None) -> dict[str, object]:
    events, decoder = merge_events(standard_csv, mirrored_csv)
    display = display_summary(events)
    refgen, heartbeat, watchdog = summaries(events)
    last_display = display.get("last_event")
    latest_hb = heartbeat.get("latest_event")
    if isinstance(last_display, dict) and isinstance(latest_hb, dict):
        d_ns, h_ns = last_display.get("monotonic_ns"), latest_hb.get("monotonic_ns")
        heartbeat["alive_500ms_after_last_display"] = bool(
            isinstance(d_ns, int) and isinstance(h_ns, int) and h_ns - d_ns >= 500_000_000)
    else:
        heartbeat["alive_500ms_after_last_display"] = False
    result = classify(events, refgen, display, heartbeat, screen)
    report: dict[str, object] = {
        "status": "diagnosed" if events else "no-events",
        "screen_result": screen,
        "privacy": "metadata-only; no secure payloads, keys, tokens, or process memory",
        "decoder": decoder,
        "decoder_runs": decoder_runs or {},
        "verdict": result,
        "refgen": refgen,
        "display": display,
        "heartbeat": heartbeat,
        "watchdog": watchdog,
    }
    write_outputs(output, report, events)
    return report


def fixture(path: Path, messages: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["seq", "monotonic_ns", "message"])
        writer.writeheader()
        for index, message in enumerate(messages, 1):
            writer.writerow({"seq": index, "monotonic_ns": index * 1_000_000, "message": message})


def self_test() -> None:
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        base = ["WDT disarm before=1 after=0", "REFGEN driver_register rc=0",
                "REFGEN probe enter compat=qcom,refgen-kona-regulator",
                "REFGEN mapped start=0x88e7000 size=132 raw=0x0",
                "REFGEN probe ready initial_enabled=0",
                "REFGEN kona_enable raw=0x0->0x1 enabled=1"]
        fixture(root / "standard.csv", base + ["DISP enter fn=dsi_display_enable", "HB tick=1 online=8 run=2 j=100"])
        report = diagnose(root / "standard.csv", root / "missing.csv", root / "black", "black")
        if report["verdict"]["code"] != "refgen_operational_display_scope_stalled":
            raise SystemExit("black-screen self-test failed")
        fixture(root / "failed.csv", ["REFGEN driver_register rc=0",
                                      "REFGEN probe enter compat=qcom,refgen-kona-regulator",
                                      "REFGEN probe fail stage=ioremap rc=-16"])
        report = diagnose(root / "failed.csv", root / "missing.csv", root / "failed", "unknown")
        if report["verdict"]["code"] != "refgen_probe_failed":
            raise SystemExit("probe-failure self-test failed")
        fixture(root / "stable.csv", base)
        report = diagnose(root / "stable.csv", root / "missing.csv", root / "stable", "stable")
        if report["verdict"]["code"] != "refgen_hypothesis_supported":
            raise SystemExit("stable-screen self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", nargs="?", type=Path)
    parser.add_argument("--output", type=Path, default=Path("a52-refgen-display-diagnosis"))
    parser.add_argument("--screen-result", choices=("unknown", "stable", "black"), default="unknown")
    parser.add_argument("--standard-csv", type=Path)
    parser.add_argument("--mirrored-csv", type=Path)
    parser.add_argument("--standard-decoder", type=Path)
    parser.add_argument("--mirrored-decoder", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    self_test()
    if args.self_test:
        print(json.dumps({"status": "self-test-passed"}, sort_keys=True))
        return 0

    output = args.output.resolve()
    standard_csv = args.standard_csv
    mirrored_csv = args.mirrored_csv
    runs: dict[str, object] = {}
    if args.capture:
        tools = Path(__file__).resolve().parent
        standard_dir = output / "decoded-standard"
        mirrored_dir = output / "decoded-mirrored"
        standard_script = args.standard_decoder or tools / "decode-a52-unified-secure-recorder.py"
        mirrored_script = args.mirrored_decoder or tools / "decode-a52-mirrored-ramoops-v2.py"
        runs = {"standard": run_decoder(standard_script, args.capture.resolve(), standard_dir),
                "mirrored": run_decoder(mirrored_script, args.capture.resolve(), mirrored_dir)}
        standard_csv = standard_csv or standard_dir / "secure-events.csv"
        mirrored_csv = mirrored_csv or mirrored_dir / "recovered-events.csv"
    if standard_csv is None and mirrored_csv is None:
        parser.error("provide a capture or at least one decoded CSV")
    standard_csv = (standard_csv or Path("missing-standard.csv")).resolve()
    mirrored_csv = (mirrored_csv or Path("missing-mirrored.csv")).resolve()
    report = diagnose(standard_csv, mirrored_csv, output, args.screen_result, runs)
    print(json.dumps({"status": report["status"], "verdict": report["verdict"]["code"],
                      "output": str(output)}, sort_keys=True))
    return 0 if report["status"] == "diagnosed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
