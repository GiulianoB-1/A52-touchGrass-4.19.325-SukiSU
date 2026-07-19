#!/usr/bin/env python3
from __future__ import annotations

from a52_diag94_common import declare_helper, triplet


def _function_bounds(text: str, anchor: str, label: str) -> tuple[int, int]:
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


def _line_span(
    text: str,
    function_anchor: str,
    candidates: tuple[str, ...],
    label: str,
) -> tuple[int, int]:
    start, end = _function_bounds(text, function_anchor, label)
    wanted = {candidate.strip() for candidate in candidates}
    matches: list[tuple[int, int]] = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        line_end = cursor + len(line)
        if line.strip() in wanted:
            matches.append((cursor, line_end))
        cursor = line_end
    if len(matches) != 1:
        raise SystemExit(
            f"{label}: expected one normalized statement in function, found {len(matches)}"
        )
    return matches[0]


def _insert_after(
    text: str,
    function_anchor: str,
    candidates: tuple[str, ...],
    insertion: str,
    label: str,
) -> str:
    _, end = _line_span(text, function_anchor, candidates, label)
    return text[:end] + insertion + text[end:]


def _insert_before(
    text: str,
    function_anchor: str,
    candidates: tuple[str, ...],
    insertion: str,
    label: str,
) -> str:
    start, _ = _line_span(text, function_anchor, candidates, label)
    return text[:start] + insertion + text[start:]


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

    sd = _insert_after(
        sd,
        "sd_probe(",
        ("struct scsi_device *sdp = to_scsi_device(dev);",),
        triplet(
            "SD stage=probe dev=%s host=%d channel=%u id=%u lun=%llu type=%d",
            "dev_name(dev), sdp->host->host_no, sdp->channel, sdp->id, "
            "(unsigned long long)sdp->lun, sdp->type",
            "\t",
        ),
        "instrument SCSI disk probe",
    )

    sd = _insert_after(
        sd,
        "sd_probe_async(",
        ("index = sdkp->index;",),
        triplet(
            "SD stage=async_begin host=%d lun=%llu disk=%s index=%u",
            "sdp->host->host_no, (unsigned long long)sdp->lun, gd->disk_name, index",
            "\t",
        ),
        "instrument async SCSI disk setup",
    )

    if "sd_revalidate_disk(gd);" in sd:
        sd = _insert_after(
            sd,
            "sd_probe_async(",
            ("sd_revalidate_disk(gd);",),
            triplet(
                "SD stage=revalidate disk=%s capacity=%llu sector_size=%u",
                "gd->disk_name, (unsigned long long)get_capacity(gd), sdp->sector_size",
                "\t",
            ),
            "instrument SCSI disk revalidation",
        )

    add_candidates = (
        "device_add_disk(dev, gd, NULL);",
        "device_add_disk(dev, gd);",
    )
    sd = _insert_before(
        sd,
        "sd_probe_async(",
        add_candidates,
        triplet(
            "SD stage=before_add_disk disk=%s major=%d first_minor=%d capacity=%llu",
            "gd->disk_name, gd->major, gd->first_minor, "
            "(unsigned long long)get_capacity(gd)",
            "\t",
        ),
        "instrument SCSI disk pre-registration",
    )
    sd = _insert_after(
        sd,
        "sd_probe_async(",
        add_candidates,
        triplet(
            "SD stage=device_add_disk disk=%s major=%d first_minor=%d capacity=%llu",
            "gd->disk_name, gd->major, gd->first_minor, "
            "(unsigned long long)get_capacity(gd)",
            "\t",
        ),
        "instrument SCSI disk registration",
    )

    if "scsi_autopm_put_device(sdp);" in sd:
        sd = _insert_before(
            sd,
            "sd_probe_async(",
            ("scsi_autopm_put_device(sdp);",),
            triplet(
                "SD stage=attached disk=%s capacity=%llu sector_size=%u",
                "gd->disk_name, (unsigned long long)get_capacity(gd), sdp->sector_size",
                "\t",
            ),
            "instrument attached SCSI disk",
        )

    return sd
