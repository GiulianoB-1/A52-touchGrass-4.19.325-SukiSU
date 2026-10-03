#!/usr/bin/env python3
import csv
import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <capture-dir>")

root = Path(sys.argv[1])
log = root / "logcat-after.txt"
timeline = root / "sf-restart-timeline.tsv"

if not log.exists():
    raise SystemExit(f"missing {log}")

patterns = [
    ("SF", re.compile(r"SurfaceFlinger|surfaceflinger", re.I)),
    ("COMPOSER", re.compile(r"composer|hwcomposer|HWC", re.I)),
    ("BOOTANIM", re.compile(r"bootanim", re.I)),
    ("DRM", re.compile(r"\bdrm\b|virtio.*gpu|goldfish.*gpu|gfxstream", re.I)),
    ("FENCE", re.compile(r"fence|presentDisplay|validateDisplay|setClientTarget", re.I)),
]

matches = []
for n, line in enumerate(log.read_text(errors="replace").splitlines(), 1):
    labels = [name for name, rx in patterns if rx.search(line)]
    if labels:
        matches.append((n, ",".join(labels), line))

filtered = root / "sf-relevant-logcat.txt"
with filtered.open("w") as f:
    f.write("SF-Q1 relevant logcat lines\n")
    f.write("=" * 72 + "\n")
    for n, labels, line in matches:
        f.write(f"{n:07d} [{labels}] {line}\n")

before = (root / "surfaceflinger-pid-before.txt").read_text().strip() if (root / "surfaceflinger-pid-before.txt").exists() else "?"
after = (root / "surfaceflinger-pid-after.txt").read_text().strip() if (root / "surfaceflinger-pid-after.txt").exists() else "?"
restart_ms = None
rp = root / "restart-request-epoch-ms.txt"
if rp.exists():
    try:
        restart_ms = int(rp.read_text().strip())
    except ValueError:
        pass

rows = []
if timeline.exists():
    with timeline.open(newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            try:
                row["host_epoch_ms"] = int(row["host_epoch_ms"])
            except (ValueError, TypeError):
                continue
            rows.append(row)

first_old = next((r for r in rows if before not in ("", "?") and r.get("sf_pid") == before), None)
last_old = next((r for r in reversed(rows) if before not in ("", "?") and r.get("sf_pid") == before), None)
first_new = next((r for r in rows if after not in ("", "?") and r.get("sf_pid") == after), None)
first_running_after_restart = next(
    (r for r in rows if restart_ms is not None and r["host_epoch_ms"] >= restart_ms and r.get("sf_state") == "running" and r.get("sf_pid")),
    None,
)

restart_to_new_ms = None
if restart_ms is not None and first_new:
    restart_to_new_ms = first_new["host_epoch_ms"] - restart_ms

pid_gap_ms = None
if last_old and first_new:
    pid_gap_ms = first_new["host_epoch_ms"] - last_old["host_epoch_ms"]

summary_lines = [
    "SF-Q1 baseline summary",
    "======================",
    f"surfaceflinger_pid_before={before}",
    f"surfaceflinger_pid_after={after}",
    f"relevant_logcat_lines={len(matches)}",
    f"timeline_samples={len(rows)}",
    f"perfetto_trace={(root / 'sfq1.perfetto-trace').name}",
    f"filtered_log={filtered.name}",
]
if restart_ms is not None:
    summary_lines.append(f"restart_request_epoch_ms={restart_ms}")
if restart_to_new_ms is not None:
    summary_lines.append(f"restart_request_to_new_sf_pid_ms={restart_to_new_ms}")
if pid_gap_ms is not None:
    summary_lines.append(f"last_old_pid_to_first_new_pid_ms={pid_gap_ms}")
if first_running_after_restart:
    summary_lines.append(
        f"first_running_sf_after_restart_epoch_ms={first_running_after_restart['host_epoch_ms']}"
    )

summary = root / "sfq1-summary.txt"
summary.write_text("\n".join(summary_lines) + "\n")

md = root / "sfq1-summary.md"
md_lines = [
    "# SF-Q1 baseline summary",
    "",
    f"- SurfaceFlinger PID before restart: `{before}`",
    f"- SurfaceFlinger PID after restart: `{after}`",
    f"- Relevant SF/Composer/fence log lines: **{len(matches)}**",
    f"- 100 ms timeline samples: **{len(rows)}**",
]
if restart_to_new_ms is not None:
    md_lines.append(f"- Restart request → new SurfaceFlinger PID: **{restart_to_new_ms} ms**")
if pid_gap_ms is not None:
    md_lines.append(f"- Last sample with old PID → first sample with new PID: **{pid_gap_ms} ms**")
md_lines += [
    "",
    "## Files",
    "",
    "- `sfq1.perfetto-trace`: Perfetto timeline",
    "- `sf-restart-timeline.tsv`: 100 ms SurfaceFlinger/init/bootanimation timeline",
    "- `sf-relevant-logcat.txt`: filtered SurfaceFlinger/Composer/HWC/fence log",
    "- `surfaceflinger-before.txt` and `surfaceflinger-after.txt`: full dumps",
    "",
    "## Interpretation",
    "",
    "This is the virtual known-good baseline. The important comparison against the A52 is where the A52 stops progressing between SurfaceFlinger, Composer/HWC, fence handling, and the first DRM-facing operation. A fast Cuttlefish restart does not prove the A52 framework is healthy; it gives us a reference state machine and timing sequence to compare against Phase432.",
]
md.write_text("\n".join(md_lines) + "\n")

print(summary.read_text(), end="")
