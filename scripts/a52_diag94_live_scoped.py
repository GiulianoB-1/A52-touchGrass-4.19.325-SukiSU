#!/usr/bin/env python3
from __future__ import annotations

from a52_diag94_extra import build_live_source as _build_live_source


def build_live_source() -> str:
    """Make delayed UFS snapshot scheduling valid on pinned Android 5.10."""
    source = _build_live_source()

    old_delays = r'''static void a52_ufs_delayed_work(struct work_struct *work)
{
	static const unsigned long delays[] = {
		msecs_to_jiffies(1500),
		msecs_to_jiffies(4000),
	};
'''
    new_delays = r'''static void a52_ufs_delayed_work(struct work_struct *work)
{
	static const unsigned int delay_ms[] = {
		1500,
		4000,
	};
'''
    if source.count(old_delays) != 1:
        raise SystemExit(
            "runtime delayed-snapshot conversion: expected one delay table"
        )
    source = source.replace(old_delays, new_delays, 1)

    old_schedule = r'''if (a52_delayed_round <= ARRAY_SIZE(delays))
		schedule_delayed_work(to_delayed_work(work),
				      delays[a52_delayed_round - 1]);'''
    new_schedule = r'''if (a52_delayed_round <= ARRAY_SIZE(delay_ms))
		schedule_delayed_work(to_delayed_work(work),
				      msecs_to_jiffies(
					      delay_ms[a52_delayed_round - 1]));'''
    if source.count(old_schedule) != 1:
        raise SystemExit(
            "runtime delayed-snapshot conversion: expected one scheduling block"
        )
    source = source.replace(old_schedule, new_schedule, 1)

    if "static const unsigned int delay_ms[]" not in source:
        raise SystemExit("runtime delayed-snapshot conversion audit failed")
    if "msecs_to_jiffies(1500)" in source or "msecs_to_jiffies(4000)" in source:
        raise SystemExit("static msecs_to_jiffies initializer remains")
    return source
