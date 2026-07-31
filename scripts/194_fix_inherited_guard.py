#!/usr/bin/env python3
from pathlib import Path

path = Path("scripts/194_apply.py")
text = path.read_text(encoding="utf-8")

stale = '        "A52GDSC DISABLE_KEEP_ON",\n'
replacement = (
    '        "static int a52_legacy_gdsc_disable(struct regulator_dev *rdev)",\n'
    '        "boot-critical UFS GDSC",\n'
)

count = text.count(stale)
if count == 1:
    text = text.replace(stale, replacement, 1)
elif count == 0 and all(
    marker in text
    for marker in (
        "static int a52_legacy_gdsc_disable(struct regulator_dev *rdev)",
        "boot-critical UFS GDSC",
    )
):
    pass
else:
    raise SystemExit(
        f"phase194 inherited-source guard layout changed unexpectedly: stale_count={count}"
    )

path.write_text(text, encoding="utf-8")
print("phase194 inherited UFS guard normalized")
