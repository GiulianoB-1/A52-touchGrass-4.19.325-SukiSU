#!/usr/bin/env python3
"""Diagnose metadata-only A52 REFGEN and display RAMOOPS evidence."""
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

RANK = {"exact": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}
CRITICAL = ("REFGEN ", "DISP ", "HB ", "WDT ")
RX = {
    "register": re.compile(r"REFGEN driver_register rc=(-?\d+)$"),
    "probe": re.compile(r"REFGEN probe enter compat=(\S+)$"),
    "failure": re.compile(r"REFGEN probe fail stage=(\S+) rc=(-?\d+)$"),
    "mapped": re.compile(r"REFGEN mapped start=0x([0-9a-fA-F]+) size=(\d+) raw=0x([0-9a-fA-F]+)$"),
    "ready": re.compile(r"REFGEN probe ready initial_enabled=(-?\d+)$"),
    "enable": re.compile(r"REFGEN kona_enable raw=0x([0-9a-fA-F]+)->0x([0-9a-fA-F]+) enabled=([01])$"),
    "heartbeat": re.compile(r"HB tick=(\d+) online=(\d+) run=(\d+) j=(\d+)$"),
    "watchdog": re.compile(r"WDT disarm before=([01]) after=([01])$"),
    "enter": re.compile(r"DISP enter fn=(\S+)$"),
    "exit": re.compile(r"DISP exit fn=(\S+) us=(\d+)$"),
}


@dataclass(frozen=True)
class Event:
    order: int
    seq: int | None
    ns: int | None
    message: str
    confidence: str
    source: str
    offset: int | None = None

    def sort_key(self) -> tuple[int, int, int]:
        if self.ns is not None:
            return 0, self.ns, self.order
        if self.seq is not None:
            return 1, self.seq, self.order
        return 2, self.order, self.order


def number(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def read_csv(path: Path, source: str, default_confidence: str) -> list[Event]:
    if not path.is_file():
        return []
    events = []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        for order, row in enumerate(csv.DictReader(handle)):
            message = (row.get("message") or "").strip()
            if not message:
                continue
            confidence = (row.get("confidence") or default_confidence).lower()
            events.append(Event(order, number(row.get("seq")), number(row.get("monotonic_ns")),
                                message, confidence if confidence in RANK else "unknown",
                                source, number(row.get("offset"))))
    return events


def merge(standard: Path, mirrored: Path) -> tuple[list[Event], dict[str, int]]:
    exact = read_csv(standard, "standard", "exact")
    recovered = read_csv(mirrored, "mirrored", "high")
    selected = list(exact)
    exact_seq = {item.seq for item in exact if item.seq is not None}
    exact_pairs = {(item.seq, item.message) for item in exact}
    exact_messages = {item.message for item in exact}
    for item in recovered:
        if item.confidence == "low" or (item.seq, item.message) in exact_pairs:
            continue
        if item.seq is not None and item.seq in exact_seq:
            continue
        if item.seq is None and item.message in exact_messages:
            continue
        selected.append(item)
    selected.sort(key=Event.sort_key)
    selected = [Event(i, e.seq, e.ns, e.message, e.confidence, e.source, e.offset)
                for i, e in enumerate(selected)]
    return selected, {
        "standard_events": len(exact), "mirrored_events": len(recovered),
        "mirrored_high": sum(e.confidence == "high" for e in recovered),
        "mirrored_medium": sum(e.confidence == "medium" for e in recovered),
        "mirrored_low": sum(e.confidence == "low" for e in recovered),
        "selected_events": len(selected),
    }


def matches(events: list[Event], key: str) -> list[tuple[Event, re.Match[str]]]:
    return [(event, match) for event in events if (match := RX[key].fullmatch(event.message))]


def best_confidence(events: list[Event]) -> str:
    return min((e.confidence for e in events), key=lambda x: RANK.get(x, 99), default="none")


def display_summary(events: list[Event]) -> dict[str, object]:
    open_scopes: list[tuple[str, Event]] = []
    completed: list[dict[str, object]] = []
    last = None
    for event in events:
        enter, exit_match = RX["enter"].fullmatch(event.message), RX["exit"].fullmatch(event.message)
        if enter:
            last = event
            open_scopes.append((enter.group(1), event))
        elif exit_match:
            last = event
            function = exit_match.group(1)
            index = next((i for i in range(len(open_scopes) - 1, -1, -1)
                          if open_scopes[i][0] == function), None)
            if index is None:
                completed.append({"function": function, "orphan_exit": True,
                                  "duration_us": int(exit_match.group(2)), "exit_order": event.order})
            else:
                _, entered = open_scopes.pop(index)
                completed.append({"function": function, "orphan_exit": False,
                                  "duration_us": int(exit_match.group(2)),
                                  "enter_order": entered.order, "exit_order": event.order})
    return {
        "event_count": sum(e.message.startswith("DISP ") for e in events),
        "completed": completed,
        "unmatched_entries": [{"function": fn, **asdict(event)} for fn, event in open_scopes],
        "last_event": asdict(last) if last else None,
    }


def refgen_summary(events: list[Event]) -> dict[str, object]:
    register, probe = matches(events, "register"), matches(events, "probe")
    failure, mapped = matches(events, "failure"), matches(events, "mapped")
    ready, enables = matches(events, "ready"), matches(events, "enable")
    size = int(mapped[-1][1].group(2)) if mapped else None
    return {
        "event_count": sum(e.message.startswith("REFGEN ") for e in events),
        "confidence": best_confidence([e for e in events if e.message.startswith("REFGEN ")]),
        "driver_register": {"observed": bool(register),
                            "rc": int(register[-1][1].group(1)) if register else None,
                            "event": asdict(register[-1][0]) if register else None},
        "probe_enter": {"observed": bool(probe),
                        "compat": probe[-1][1].group(1) if probe else None,
                        "event": asdict(probe[-1][0]) if probe else None},
        "probe_failure": {"observed": bool(failure),
                          "stage": failure[-1][1].group(1) if failure else None,
                          "rc": int(failure[-1][1].group(2)) if failure else None,
                          "event": asdict(failure[-1][0]) if failure else None},
        "mapping": {"observed": bool(mapped),
                    "start": int(mapped[-1][1].group(1), 16) if mapped else None,
                    "size": size, "raw": int(mapped[-1][1].group(3), 16) if mapped else None,
                    "ctrl5_offset": 0x80, "resource_covers_ctrl5": bool(size is not None and size >= 0x84),
                    "stock_lagoon_0x60_layout": size == 0x60},
        "probe_ready": {"observed": bool(ready),
                        "initial_enabled": int(ready[-1][1].group(1)) if ready else None,
                        "event": asdict(ready[-1][0]) if ready else None},
        "enable_calls": [{"before": int(m.group(1), 16), "after": int(m.group(2), 16),
                          "enabled": int(m.group(3)), "event": asdict(e)} for e, m in enables],
    }


def simple_summaries(events: list[Event]) -> tuple[dict[str, object], dict[str, object]]:
    heartbeats, watchdogs = matches(events, "heartbeat"), matches(events, "watchdog")
    heartbeat = {"count": len(heartbeats),
                 "first_tick": int(heartbeats[0][1].group(1)) if heartbeats else None,
                 "last_tick": int(heartbeats[-1][1].group(1)) if heartbeats else None,
                 "latest_event": asdict(heartbeats[-1][0]) if heartbeats else None}
    watchdog = {"observed": bool(watchdogs),
                "before": int(watchdogs[-1][1].group(1)) if watchdogs else None,
                "after": int(watchdogs[-1][1].group(2)) if watchdogs else None,
                "disarmed": bool(watchdogs and watchdogs[-1][1].group(2) == "0"),
                "event": asdict(watchdogs[-1][0]) if watchdogs else None}
    return heartbeat, watchdog


def result(code: str, title: str, confidence: str, meaning: str, action: str) -> dict[str, str]:
    return {"code": code, "title": title, "confidence": confidence,
            "meaning": meaning, "next_action": action}


def classify(events: list[Event], refgen: dict[str, object], display: dict[str, object], screen: str) -> dict[str, str]:
    if not events:
        return result("no_recorder_events", "No usable A52USR2 events were recovered", "low",
                      "The capture is missing, incomplete, or too damaged.",
                      "Repeat collection and preserve the untouched 1 MiB RAMOOPS image.")
    driver, probe = refgen["driver_register"], refgen["probe_enter"]
    failure, ready, enables = refgen["probe_failure"], refgen["probe_ready"], refgen["enable_calls"]
    assert isinstance(driver, dict) and isinstance(probe, dict)
    assert isinstance(failure, dict) and isinstance(ready, dict) and isinstance(enables, list)
    confidence = str(refgen.get("confidence", "unknown"))
    if not driver["observed"]:
        return result("refgen_registration_missing", "REFGEN registration was not recorded", confidence,
                      "The initcall did not run, its record was lost, or another image was flashed.",
                      "Verify the boot.img hash and inspect BOOT_EARLY and heartbeat records.")
    if driver["rc"] not in (0, None):
        return result("refgen_registration_failed", f"REFGEN registration failed with rc={driver['rc']}", confidence,
                      "The platform driver never became available.",
                      "Resolve platform_driver_register before changing DSI code.")
    if not probe["observed"]:
        return result("refgen_probe_missing", "REFGEN registered but its DT probe never started", confidence,
                      "The stock provider node did not populate or match.",
                      "Audit node status, compatible, parent population, and platform-device creation.")
    if failure["observed"]:
        return result("refgen_probe_failed", f"REFGEN probe failed at {failure['stage']} with rc={failure['rc']}", confidence,
                      "The provider matched but could not finish registration.",
                      f"Fix only the recorded probe stage {failure['stage']}.")
    if not ready["observed"]:
        return result("refgen_probe_incomplete", "REFGEN probe did not reach ready", confidence,
                      "The probe stalled or the final record was lost.",
                      "Instrument the interval after the last REFGEN marker.")
    if not any(isinstance(x, dict) and x.get("enabled") == 1 for x in enables):
        return result("refgen_not_enabled", "REFGEN registered but no successful enable was recorded", confidence,
                      "The DSI consumer may not have requested the supply or the write did not latch.",
                      "Inspect refgen-supply acquisition and regulator enable around DSI prepare.")
    unmatched = display.get("unmatched_entries")
    assert isinstance(unmatched, list)
    if screen == "stable":
        return result("refgen_hypothesis_supported", "REFGEN enabled and the display remained stable", confidence,
                      "The missing REFGEN provider was the leading black-screen cause.",
                      "Repeat one controlled boot, then prepare a recorder-free candidate with the same REFGEN port.")
    if screen == "black" and unmatched:
        function = str(unmatched[-1].get("function"))
        return result("refgen_operational_display_scope_stalled", f"REFGEN enabled, then display stalled in {function}", confidence,
                      "REFGEN is operational; the remaining failure is inside the next recorded display boundary.",
                      f"Add narrow markers inside {function}; do not change unrelated display code.")
    if screen == "black":
        return result("refgen_operational_black_screen_later", "REFGEN enabled but the black screen occurred later", confidence,
                      "The remaining fault is later than the current probes or outside their coverage.",
                      "Use the final critical timeline event to add one deeper probe layer.")
    return result("refgen_operational_result_unknown", "REFGEN appears operational; screen result is unknown", confidence,
                  "The capture alone cannot prove whether the screen remained usable.",
                  "Reclassify the same capture with --screen-result stable or black.")


def run_decoder(script: Path, capture: Path, output: Path) -> dict[str, object]:
    if not script.is_file():
        return {"status": "missing", "script": str(script), "returncode": None}
    done = subprocess.run([sys.executable, str(script), str(capture), "--output", str(output)],
                          text=True, capture_output=True, check=False)
    return {"status": "ok" if done.returncode == 0 else "no-events" if done.returncode == 2 else "failed",
            "script": str(script), "returncode": done.returncode,
            "stdout": done.stdout.strip(), "stderr": done.stderr.strip()}


def write_outputs(output: Path, report: dict[str, object], events: list[Event]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "diagnosis.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    verdict = report["verdict"]
    assert isinstance(verdict, dict)
    lines = ["# A52 REFGEN display diagnosis", "", f"**Verdict:** {verdict['title']}", "",
             f"**Code:** `{verdict['code']}`  ", f"**Confidence:** `{verdict['confidence']}`", "",
             "## Meaning", "", str(verdict["meaning"]), "", "## Next action", "",
             str(verdict["next_action"]), "", "## Warnings", ""]
    warnings = report.get("warnings")
    lines.extend([f"- {item}" for item in warnings] if isinstance(warnings, list) and warnings else ["- None"])
    lines.extend(["", "See `diagnosis.json` and `critical-timeline.csv` for complete evidence.", ""])
    (output / "diagnosis.md").write_text("\n".join(lines))
    with (output / "critical-timeline.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order", "seq", "monotonic_ns", "message", "confidence", "source", "offset"])
        for event in events:
            if event.message.startswith(CRITICAL):
                writer.writerow([event.order, event.seq or "", event.ns or "", event.message,
                                 event.confidence, event.source, event.offset or ""])


def diagnose(standard: Path, mirrored: Path, output: Path, screen: str,
             decoder_runs: dict[str, object] | None = None) -> dict[str, object]:
    events, decoder = merge(standard, mirrored)
    refgen, display = refgen_summary(events), display_summary(events)
    heartbeat, watchdog = simple_summaries(events)
    last_display, latest_hb = display.get("last_event"), heartbeat.get("latest_event")
    heartbeat["alive_500ms_after_last_display"] = bool(
        isinstance(last_display, dict) and isinstance(latest_hb, dict)
        and isinstance(last_display.get("ns"), int) and isinstance(latest_hb.get("ns"), int)
        and latest_hb["ns"] - last_display["ns"] >= 500_000_000)
    mapping, warnings = refgen.get("mapping"), []
    if isinstance(mapping, dict) and mapping.get("observed") and not mapping.get("resource_covers_ctrl5"):
        warnings.append("REFGEN CTRL5 offset 0x80 is outside the declared resource span; the A52 Lagoon stock layout declares 0x60 and the downstream driver uses the same offset.")
    report: dict[str, object] = {
        "status": "diagnosed" if events else "no-events", "screen_result": screen,
        "privacy": "metadata-only; no secure payloads, keys, tokens, or process memory",
        "decoder": decoder, "decoder_runs": decoder_runs or {}, "warnings": warnings,
        "verdict": classify(events, refgen, display, screen), "refgen": refgen,
        "display": display, "heartbeat": heartbeat, "watchdog": watchdog,
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
                "REFGEN mapped start=0x88e7000 size=96 raw=0x0",
                "REFGEN probe ready initial_enabled=0", "REFGEN kona_enable raw=0x0->0x1 enabled=1"]
        fixture(root / "black.csv", base + ["DISP enter fn=dsi_display_enable", "HB tick=1 online=8 run=2 j=100"])
        report = diagnose(root / "black.csv", root / "none.csv", root / "black", "black")
        assert report["verdict"]["code"] == "refgen_operational_display_scope_stalled"
        assert report["refgen"]["mapping"]["stock_lagoon_0x60_layout"] is True
        assert report["warnings"]
        fixture(root / "failed.csv", ["REFGEN driver_register rc=0",
                                      "REFGEN probe enter compat=qcom,refgen-kona-regulator",
                                      "REFGEN probe fail stage=ioremap rc=-16"])
        assert diagnose(root / "failed.csv", root / "none.csv", root / "failed", "unknown")["verdict"]["code"] == "refgen_probe_failed"
        fixture(root / "stable.csv", base)
        assert diagnose(root / "stable.csv", root / "none.csv", root / "stable", "stable")["verdict"]["code"] == "refgen_hypothesis_supported"


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
    output, standard, mirrored, runs = args.output.resolve(), args.standard_csv, args.mirrored_csv, {}
    if args.capture:
        tools, capture = Path(__file__).resolve().parent, args.capture.resolve()
        standard_dir, mirrored_dir = output / "decoded-standard", output / "decoded-mirrored"
        runs = {"standard": run_decoder(args.standard_decoder or tools / "decode-a52-unified-secure-recorder.py", capture, standard_dir),
                "mirrored": run_decoder(args.mirrored_decoder or tools / "decode-a52-mirrored-ramoops-v2.py", capture, mirrored_dir)}
        standard, mirrored = standard or standard_dir / "secure-events.csv", mirrored or mirrored_dir / "recovered-events.csv"
    if standard is None and mirrored is None:
        parser.error("provide a capture or at least one decoded CSV")
    report = diagnose((standard or Path("missing-standard.csv")).resolve(),
                      (mirrored or Path("missing-mirrored.csv")).resolve(),
                      output, args.screen_result, runs)
    print(json.dumps({"status": report["status"], "verdict": report["verdict"]["code"], "output": str(output)}, sort_keys=True))
    return 0 if report["status"] == "diagnosed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
