#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

MARK = "A52_PHASE343_DRM_VREFRESH_ROUNDTRIP_PARITY_V1"
MODES_REL = Path("drivers/gpu/drm/drm_modes.c")


def one(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"Phase343 {label}: expected 1 match, found {n}")
    return text.replace(old, new, 1)


def function_bounds(text: str, needle: str) -> tuple[int, int]:
    start = text.find(needle)
    if start < 0:
        raise SystemExit(f"Phase343 function not found: {needle}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"Phase343 function brace not found: {needle}")
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise SystemExit(f"Phase343 unterminated function: {needle}")


def patch(text: str) -> str:
    conv_start, conv_end = function_bounds(text, "int drm_mode_convert_umode(")
    conv = text[conv_start:conv_end]

    if "out->vrefresh = in->vrefresh;" not in conv:
        old = """\tout->vtotal = in->vtotal;
\tout->vscan = in->vscan;
\tout->flags = in->flags;
"""
        new = """\tout->vtotal = in->vtotal;
\tout->vscan = in->vscan;
\t/* A52_PHASE343_DRM_VREFRESH_ROUNDTRIP_PARITY_V1
\t * TouchGrass keeps an explicit legacy vrefresh field in
\t * drm_display_mode and copies the userspace MODE_ID value into it.
\t * Android 5.10 removed that field, so after restoring the field for the
\t * downstream display stack we must also restore its conversion semantic.
\t */
\tout->vrefresh = in->vrefresh;
\tout->flags = in->flags;
"""
        conv = one(conv, old, new, "drm_mode_convert_umode vrefresh restore")
        text = text[:conv_start] + conv + text[conv_end:]

    vr_start, vr_end = function_bounds(text, "int drm_mode_vrefresh(")
    vr = text[vr_start:vr_end]

    if "if (mode->vrefresh > 0)" not in vr:
        old = """{
\tunsigned int num, den;

\tif (mode->htotal == 0 || mode->vtotal == 0)
"""
        new = """{
\tunsigned int num, den;

\t/* A52_PHASE343_DRM_VREFRESH_ROUNDTRIP_PARITY_V1 */
\tif (mode->vrefresh > 0)
\t\treturn mode->vrefresh;

\tif (mode->htotal == 0 || mode->vtotal == 0)
"""
        vr = one(vr, old, new, "drm_mode_vrefresh explicit-field precedence")
        text = text[:vr_start] + vr + text[vr_end:]

    return text


def validate(before: str, after: str) -> None:
    conv_start, conv_end = function_bounds(after, "int drm_mode_convert_umode(")
    conv = after[conv_start:conv_end]
    vr_start, vr_end = function_bounds(after, "int drm_mode_vrefresh(")
    vr = after[vr_start:vr_end]

    required = (
        "out->vrefresh = in->vrefresh;",
        "if (mode->vrefresh > 0)",
        "return mode->vrefresh;",
    )
    for token in required:
        if token not in after:
            raise SystemExit("Phase343 required token missing: " + token)

    if conv.count("out->vrefresh = in->vrefresh;") != 1:
        raise SystemExit("Phase343 expected one inbound vrefresh assignment")
    if vr.count("if (mode->vrefresh > 0)") != 1:
        raise SystemExit("Phase343 expected one explicit vrefresh precedence check")

    if after.count("out->vrefresh = in->vrefresh;") != before.count(
        "out->vrefresh = in->vrefresh;"
    ) + 1:
        raise SystemExit("Phase343 changed unexpected inbound vrefresh sites")

    protected = (
        "drm_mode_validate_driver(dev, out);",
        "drm_mode_set_crtcinfo(out, CRTC_INTERLACE_HALVE_V);",
        "out->clock = in->clock;",
        "out->hdisplay = in->hdisplay;",
        "out->vdisplay = in->vdisplay;",
        "out->flags = in->flags;",
    )
    for token in protected:
        if after.count(token) != before.count(token):
            raise SystemExit("Phase343 changed protected mode conversion token: " + token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--check-only", action="store_true")
    ns = ap.parse_args()

    path = Path(ns.root) / MODES_REL
    if not path.is_file():
        raise SystemExit("Phase343 drm_modes.c missing")

    before = path.read_text(encoding="utf-8")

    if MARK in before:
        for token in (
            "out->vrefresh = in->vrefresh;",
            "if (mode->vrefresh > 0)",
            "return mode->vrefresh;",
        ):
            if token not in before:
                raise SystemExit("Phase343 check-only token missing: " + token)
        print("Phase343 DRM vrefresh roundtrip parity audit: PASS")
        return 0

    if ns.check_only:
        raise SystemExit("Phase343 marker missing in check-only mode")

    header = Path(ns.root) / "include/drm/drm_modes.h"
    htext = header.read_text(encoding="utf-8")
    if "int vrefresh;" not in htext:
        raise SystemExit("Phase343 requires restored legacy drm_display_mode.vrefresh")

    after = patch(before)
    validate(before, after)
    path.write_text(after, encoding="utf-8")
    print("Phase343 DRM vrefresh roundtrip parity applied: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
