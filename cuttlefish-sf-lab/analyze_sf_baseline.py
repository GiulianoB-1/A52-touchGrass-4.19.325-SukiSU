#!/usr/bin/env python3
import re
import sys
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} <capture-dir>")

root = Path(sys.argv[1])
log = root / "logcat-after.txt"
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

out = root / "sf-relevant-logcat.txt"
with out.open("w") as f:
    f.write("SF-Q1 relevant logcat lines\n")
    f.write("=" * 72 + "\n")
    for n, labels, line in matches:
        f.write(f"{n:07d} [{labels}] {line}\n")

before = (root / "surfaceflinger-pid-before.txt").read_text().strip() if (root / "surfaceflinger-pid-before.txt").exists() else "?"
after = (root / "surfaceflinger-pid-after.txt").read_text().strip() if (root / "surfaceflinger-pid-after.txt").exists() else "?"

summary = root / "sfq1-summary.txt"
summary.write_text(
    "SF-Q1 baseline summary\n"
    "======================\n"
    f"surfaceflinger_pid_before={before}\n"
    f"surfaceflinger_pid_after={after}\n"
    f"relevant_logcat_lines={len(matches)}\n"
    f"perfetto_trace={(root / 'sfq1.perfetto-trace').name}\n"
    f"filtered_log={out.name}\n"
)

print(summary.read_text(), end="")
