#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path


def fnv1a32(s: str) -> int:
    h = 2166136261
    for b in s.encode():
        h ^= b
        h = (h * 16777619) & 0xffffffff
    return h


root = Path(sys.argv[1])
out = Path(sys.argv[2])
tags = set()
patterns = [
    re.compile(r'TG_BOOT_REF(?:0)?\(\s*"([^"]+)"'),
    re.compile(r'TG_GPU_REF(?:0)?\(\s*"([^"]+)"'),
    re.compile(r'TG_FDR_TAG\([^,]+,\s*"([^"]+)"'),
]

for path in root.rglob('*'):
    if path.suffix not in {'.c', '.h'}:
        continue
    try:
        text = path.read_text(errors='ignore')
    except OSError:
        continue
    for pattern in patterns:
        tags.update(pattern.findall(text))

events = {}
collisions = {}
for tag in sorted(tags):
    event_id = fnv1a32(tag)
    key = str(event_id)
    if key in events and events[key] != tag:
        collisions.setdefault(key, [events[key]]).append(tag)
    else:
        events[key] = tag

if collisions:
    raise SystemExit('FNV32 collision(s): ' + json.dumps(collisions, indent=2))

out.write_text(json.dumps({'hash': 'fnv1a32', 'events': events}, indent=2, sort_keys=True))
print(f'dictionary_events={len(events)}')
