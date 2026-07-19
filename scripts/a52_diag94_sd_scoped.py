#!/usr/bin/env python3
from __future__ import annotations

from a52_diag94_common import declare_helper, triplet


def _function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
    """Return one function definition, skipping matching prototypes."""
    search = 0
    while True:
        start = text.find(anchor, search)
        if start < 0:
            raise SystemExit(f"{label}: function definition anchor not found")
        brace = text.find("{", start)
        semicolon = text.find(";", start)
        if brace >= 0 and (semicolon < 0 or brace < semicolon):
            break
        search = start + len(anchor)

    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit(f"{label}: function closing brace not found")


def _normalized_line_spans(
    text: str,
    candidates: tuple[str, ...],
    start: int = 0,
    end: int | None = None,
) -> list[tuple[int, int]]:
    """Find statement lines while ignoring indentation and trailing whitespace."""
    if end is None:
        end = len(text)
    wanted = {candidate.strip() for candidate in candidates}
    matches: list[tuple[int, int]] = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        line_end = cursor + len(line)
        if line.strip() in wanted:
            matches.append((cursor, line_end))
        cursor = line_end
    return matches


def _line_span_in_function(
    text: str,
    function_anchor: str,
    candidates: tuple[str, ...],
    label: str,
) -> tuple[int, int]:
    start, end = _function_bounds(text, function_anchor, label)
    matches = _normalized_line_spans(text, candidates, start, end)
    if len(matches) != 1:
        raise SystemExit(
            f"{label}: expected one normalized statement in function, found {len(matches)}"
        )
    return matches[0]


def _global_line_span(
    text: str, candidates: tuple[str, ...], label: str
) -> tuple[int, int]:
    matches = _normalized_line_spans(text, candidates)
    if len(matches) != 1:
        raise SystemExit(
            f"{label}: expected one normalized statement in file, found {len(matches)}"
        )
    return matches[0]


def instrument_sd(sd: str) -> str:
    sd = declare_helper(
        sd,
        (
            "#include <linux/blkdev.h>\n",
            "#include <linux/genhd.h>\n",
            "#include <scsi/scsi_driver.h>\n",
        ),
        "declare persistent diagnostic helper in sd.c",
    )

    # Keep all declarations at the beginning of sd_probe(). The pinned kernel is
    # built as GNU89 with declaration-after-statement treated as an error.
    _, probe_insert = _line_span_in_function(
        sd,
        "sd_probe(",
        ("int error;",),
        "instrument SCSI disk probe after declarations",
    )
    sd = sd[:probe_insert] + triplet(
        "SD stage=probe dev=%s host=%d channel=%u id=%u lun=%llu type=%d",
        "dev_name(dev), sdp->host->host_no, sdp->channel, sdp->id, "
        "(unsigned long long)sdp->lun, sdp->type",
        "\t",
    ) + sd[probe_insert:]

    # Android common revisions move disk setup between sd_probe() and a separate
    # asynchronous helper. The single device_add_disk() call is the stable event
    # required by the recorder, so instrument that normalized line directly.
    add_candidates = (
        "device_add_disk(dev, gd, NULL);",
        "device_add_disk(dev, gd);",
    )
    add_start, add_end = _global_line_span(
        sd, add_candidates, "instrument SCSI disk registration"
    )
    actual_add_line = sd[add_start:add_end]
    before = triplet(
        "SD stage=before_add_disk disk=%s major=%d first_minor=%d capacity=%llu",
        "gd->disk_name, gd->major, gd->first_minor, "
        "(unsigned long long)get_capacity(gd)",
        "\t",
    )
    after = triplet(
        "SD stage=device_add_disk disk=%s major=%d first_minor=%d capacity=%llu",
        "gd->disk_name, gd->major, gd->first_minor, "
        "(unsigned long long)get_capacity(gd)",
        "\t",
    )
    sd = sd[:add_start] + before + actual_add_line + after + sd[add_end:]

    return sd
