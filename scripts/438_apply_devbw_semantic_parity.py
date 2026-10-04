#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE438_DEVBW_SEMANTIC_PARITY_V1"
DEVBW = Path("drivers/devfreq/devfreq_devbw.c")


def die(msg: str) -> None:
    raise SystemExit("Phase438: " + msg)


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        die(f"{label}: expected exactly one anchor, found {n}")
    return text.replace(old, new, 1)


def patch_devbw(text: str) -> str:
    if MARK in text:
        return text

    old = """int devfreq_suspend_devbw(struct device *dev)
{
	struct dev_data *d = dev_get_drvdata(dev);

	return devfreq_suspend_device(d->df);
}

int devfreq_resume_devbw(struct device *dev)
{
	struct dev_data *d = dev_get_drvdata(dev);

	return devfreq_resume_device(d->df);
}
"""
    new = """/* A52_PHASE438_DEVBW_SEMANTIC_PARITY_V1
 *
 * This driver is imported from the TouchGrass 4.19 downstream tree.
 * TouchGrass devfreq uses a boolean dev_suspended state: duplicate suspend
 * and resume calls are idempotent. Android common 5.10 instead uses the
 * nested suspend_count counter. Calling devfreq_resume_device() at count 0
 * decrements it to -1 and still runs the governor RESUME callback.
 *
 * KGSL's first AXI-on can legitimately call devfreq_resume_devbw() before any
 * matching devfreq_suspend_devbw(). Preserve the downstream boolean contract
 * locally rather than changing the generic 5.10 devfreq core.
 */
int devfreq_suspend_devbw(struct device *dev)
{
	struct dev_data *d = dev_get_drvdata(dev);
	int count;

	if (!d || !d->df)
		return -ENODEV;

	count = atomic_read(&d->df->suspend_count);
	if (count > 0)
		return 0;

	return devfreq_suspend_device(d->df);
}

int devfreq_resume_devbw(struct device *dev)
{
	struct dev_data *d = dev_get_drvdata(dev);
	int count;

	if (!d || !d->df)
		return -ENODEV;

	count = atomic_read(&d->df->suspend_count);
	if (count <= 0)
		return 0;

	return devfreq_resume_device(d->df);
}
"""
    text = one(text, old, new, "devbw suspend/resume wrappers")

    # Build-time identity only: no logging or extra work in the critical AXI path.
    text += (
        '\n/* ' + MARK + ': TouchGrass boolean suspend/resume semantics on 5.10. */\n'
        'static const char a52_p438_devbw_build_tag[] __used = "' + MARK + '";\n'
    )
    return text


def validate(root: Path) -> None:
    p = root / DEVBW
    if not p.is_file():
        die("missing " + str(DEVBW))
    text = p.read_text(errors="replace")

    for token in (
        MARK,
        "atomic_read(&d->df->suspend_count)",
        "if (count > 0)",
        "if (count <= 0)",
        "return devfreq_suspend_device(d->df);",
        "return devfreq_resume_device(d->df);",
        "a52_p438_devbw_build_tag",
    ):
        if token not in text:
            die("missing token: " + token)

    # We must never patch the generic 5.10 devfreq core for this compatibility fix.
    core = root / "drivers/devfreq/devfreq.c"
    if not core.is_file():
        die("generic devfreq core missing")
    if MARK in core.read_text(errors="replace"):
        die("generic devfreq core was modified")

    # Exactly one local wrapper pair should remain.
    if text.count("int devfreq_suspend_devbw(struct device *dev)") != 1:
        die("devfreq_suspend_devbw definition count mismatch")
    if text.count("int devfreq_resume_devbw(struct device *dev)") != 1:
        die("devfreq_resume_devbw definition count mismatch")

    print("Phase438 DEVBW semantic parity: PASS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()
    root = ns.root.resolve()

    if not ns.check_only:
        p = root / DEVBW
        if not p.is_file():
            die("missing " + str(DEVBW))
        p.write_text(patch_devbw(p.read_text(errors="replace")))

    validate(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
