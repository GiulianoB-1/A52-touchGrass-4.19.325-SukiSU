#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PHASE = "A52_PHASE12_DRM_LEGACY_FIELDS"


def read(path: Path) -> str:
    return path.read_text(errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text)


def struct_bounds(text: str, name: str) -> tuple[int, int]:
    match = re.search(rf"\bstruct\s+{re.escape(name)}\s*\{{", text)
    if not match:
        raise SystemExit(f"struct {name} not found")
    brace = text.find("{", match.start())
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                semi = text.find(";", index)
                if semi < 0:
                    raise SystemExit(f"struct {name} has no terminating semicolon")
                return match.start(), semi + 1
    raise SystemExit(f"struct {name} has no closing brace")


def struct_text(text: str, name: str) -> str:
    start, end = struct_bounds(text, name)
    return text[start:end]


def insert_in_struct_after(
    text: str, name: str, marker: str, anchor: str, block: str
) -> tuple[str, int]:
    start, end = struct_bounds(text, name)
    body = text[start:end]
    if marker in body:
        return text, 0
    if body.count(anchor) != 1:
        raise SystemExit(
            f"expected one {name} insertion anchor for {marker}, found {body.count(anchor)}"
        )
    body = body.replace(anchor, anchor + "\n\n" + block.rstrip(), 1)
    return text[:start] + body + text[end:], 1


def insert_in_struct_before(
    text: str, name: str, marker: str, anchor: str, block: str
) -> tuple[str, int]:
    start, end = struct_bounds(text, name)
    body = text[start:end]
    if marker in body:
        return text, 0
    if body.count(anchor) != 1:
        raise SystemExit(
            f"expected one {name} insertion anchor for {marker}, found {body.count(anchor)}"
        )
    body = body.replace(anchor, block.rstrip() + "\n" + anchor, 1)
    return text[:start] + body + text[end:], 1


def patch_display_mode(gki: Path) -> dict[str, int]:
    path = gki / "include/drm/drm_modes.h"
    text = read(path)
    result = {"mode_object_include": 0, "base": 0, "private": 0}

    if "#include <drm/drm_mode_object.h>" not in text:
        include_anchor = "#include <linux/hdmi.h>"
        if include_anchor not in text:
            raise SystemExit("drm_modes.h include anchor not found")
        text = text.replace(
            include_anchor,
            include_anchor + "\n\n#include <drm/drm_mode_object.h>",
            1,
        )
        result["mode_object_include"] = 1

    base_marker = f"/* {PHASE}_MODE_BASE */"
    base_block = f"""\t{base_marker}
\tstruct drm_mode_object base;"""
    mode = struct_text(text, "drm_display_mode")
    if not re.search(r"\bstruct\s+drm_mode_object\s+base\s*;", mode):
        text, result["base"] = insert_in_struct_after(
            text,
            "drm_display_mode",
            base_marker,
            "\tstruct list_head head;",
            base_block,
        )

    private_marker = f"/* {PHASE}_MODE_PRIVATE */"
    private_block = f"""\t{private_marker}
\tint *private;"""
    mode = struct_text(text, "drm_display_mode")
    if not re.search(r"\bint\s*\*\s*private\s*;", mode):
        anchors = ("\tint private_flags;", "\tint vrefresh;")
        anchor = next((candidate for candidate in anchors if candidate in mode), None)
        if not anchor:
            raise SystemExit("drm_display_mode private insertion anchor not found")
        text, result["private"] = insert_in_struct_before(
            text,
            "drm_display_mode",
            private_marker,
            anchor,
            private_block,
        )

    write(path, text)
    return result


def patch_connector_header(gki: Path) -> dict[str, object]:
    path = gki / "include/drm/drm_connector.h"
    text = read(path)
    result: dict[str, object] = {
        "panel_forward_declaration": 0,
        "encoder_limit": 0,
        "compatibility_fields": [],
    }

    if not re.search(r"^struct drm_panel;\s*$", text, flags=re.M):
        anchors = ("struct drm_encoder;", "struct drm_device;")
        anchor = next((candidate for candidate in anchors if candidate in text), None)
        if not anchor:
            raise SystemExit("drm_connector.h forward-declaration anchor not found")
        text = text.replace(anchor, anchor + "\nstruct drm_panel;", 1)
        result["panel_forward_declaration"] = 1

    if not re.search(r"^#define\s+DRM_CONNECTOR_MAX_ENCODER\s+3\s*$", text, flags=re.M):
        struct_start, _ = struct_bounds(text, "drm_connector")
        text = (
            text[:struct_start]
            + f"#define DRM_CONNECTOR_MAX_ENCODER 3\n\n"
            + text[struct_start:]
        )
        result["encoder_limit"] = 1

    expected_fields = (
        ("encoder_ids", "uint32_t encoder_ids[DRM_CONNECTOR_MAX_ENCODER];"),
        ("color_enc_fmt", "u32 color_enc_fmt;"),
        ("hdr_eotf", "u32 hdr_eotf;"),
        ("hdr_metadata_type_one", "bool hdr_metadata_type_one;"),
        ("hdr_max_luminance", "u32 hdr_max_luminance;"),
        ("hdr_avg_luminance", "u32 hdr_avg_luminance;"),
        ("hdr_min_luminance", "u32 hdr_min_luminance;"),
        ("hdr_supported", "bool hdr_supported;"),
        ("hdr_plus_app_ver", "u8 hdr_plus_app_ver;"),
        ("panel", "struct drm_panel *panel;"),
    )

    connector = struct_text(text, "drm_connector")
    missing: list[tuple[str, str]] = []
    for name, declaration in expected_fields:
        if declaration not in connector:
            missing.append((name, declaration))

    if missing:
        marker = f"/* {PHASE}_CONNECTOR_FIELDS */"
        lines = [f"\t{marker}"] + [f"\t{declaration}" for _, declaration in missing]
        block = "\n".join(lines) + "\n"
        start, end = struct_bounds(text, "drm_connector")
        body = text[start:end]
        if marker in body:
            raise SystemExit("connector compatibility marker exists but fields are missing")
        close = body.rfind("}")
        if close < 0:
            raise SystemExit("drm_connector closing brace not found")
        body = body[:close] + "\n" + block + body[close:]
        text = text[:start] + body + text[end:]
        result["compatibility_fields"] = [name for name, _ in missing]

    write(path, text)
    return result


def function_bounds(text: str, signature: str) -> tuple[int, int]:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"function signature not found: {signature}")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit(f"function opening brace not found: {signature}")
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return start, index + 1
    raise SystemExit(f"function closing brace not found: {signature}")


def patch_connector_core(gki: Path) -> dict[str, int]:
    path = gki / "drivers/gpu/drm/drm_connector.c"
    text = read(path)
    result = {"encoder_array_mirror": 0}
    signature = "int drm_connector_attach_encoder(struct drm_connector *connector,"
    start, end = function_bounds(text, signature)
    function = text[start:end]
    marker = f"/* {PHASE}_ENCODER_MIRROR */"
    if marker in function:
        return result

    update = "\tconnector->possible_encoders |= drm_encoder_mask(encoder);"
    if function.count(update) != 1:
        raise SystemExit(
            "expected Android 5.10 possible_encoders update in drm_connector_attach_encoder"
        )

    opening = function.find("{")
    prefix = function[: opening + 1]
    body = function[opening + 1 :]
    if not re.search(r"\bint\s+i\s*;", body):
        body = "\n\tint i;" + body

    replacement = f"""\t{marker}
\tfor (i = 0; i < ARRAY_SIZE(connector->encoder_ids); i++) {{
\t\tif (connector->encoder_ids[i] == 0) {{
\t\t\tconnector->encoder_ids[i] = encoder->base.id;
\t\t\tconnector->possible_encoders |= drm_encoder_mask(encoder);
\t\t\treturn 0;
\t\t}}
\t}}

\treturn -ENOMEM;"""
    body = body.replace(update + "\n\n\treturn 0;", replacement, 1)
    if marker not in body:
        raise SystemExit("failed to replace possible_encoders update")

    function = prefix + body
    text = text[:start] + function + text[end:]
    write(path, text)
    result["encoder_array_mirror"] = 1
    return result


def validate(gki: Path) -> dict[str, bool]:
    modes = read(gki / "include/drm/drm_modes.h")
    connector_h = read(gki / "include/drm/drm_connector.h")
    connector_c = read(gki / "drivers/gpu/drm/drm_connector.c")
    mode = struct_text(modes, "drm_display_mode")
    connector = struct_text(connector_h, "drm_connector")
    attach_start, attach_end = function_bounds(
        connector_c, "int drm_connector_attach_encoder(struct drm_connector *connector,"
    )
    attach = connector_c[attach_start:attach_end]

    checks = {
        "mode_object_type_available": "#include <drm/drm_mode_object.h>" in modes,
        "mode_base": bool(
            re.search(r"\bstruct\s+drm_mode_object\s+base\s*;", mode)
        ),
        "mode_private": bool(re.search(r"\bint\s*\*\s*private\s*;", mode)),
        "mode_private_flags_preserved": bool(
            re.search(r"\bint\s+private_flags\s*;", mode)
        ),
        "encoder_limit": bool(
            re.search(
                r"^#define\s+DRM_CONNECTOR_MAX_ENCODER\s+3\s*$",
                connector_h,
                flags=re.M,
            )
        ),
        "connector_encoder_ids": (
            "uint32_t encoder_ids[DRM_CONNECTOR_MAX_ENCODER];" in connector
        ),
        "connector_color_enc_fmt": "u32 color_enc_fmt;" in connector,
        "connector_hdr_eotf": "u32 hdr_eotf;" in connector,
        "connector_hdr_metadata_type_one": (
            "bool hdr_metadata_type_one;" in connector
        ),
        "connector_hdr_max_luminance": "u32 hdr_max_luminance;" in connector,
        "connector_hdr_avg_luminance": "u32 hdr_avg_luminance;" in connector,
        "connector_hdr_min_luminance": "u32 hdr_min_luminance;" in connector,
        "connector_hdr_supported": "bool hdr_supported;" in connector,
        "connector_hdr_plus_app_ver": "u8 hdr_plus_app_ver;" in connector,
        "connector_panel": "struct drm_panel *panel;" in connector,
        "possible_encoders_preserved": (
            "connector->possible_encoders |= drm_encoder_mask(encoder);" in attach
        ),
        "legacy_encoder_ids_populated": (
            "connector->encoder_ids[i] = encoder->base.id;" in attach
        ),
        "encoder_capacity_failure": "return -ENOMEM;" in attach,
    }
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    report = {
        "status": "phase12-drm-legacy-fields-staged",
        "flashable": False,
        "hardware_validated": False,
        "display_mode": patch_display_mode(gki),
        "connector_header": patch_connector_header(gki),
        "connector_core": patch_connector_core(gki),
        "semantic_source": "exact-touchgrass-drm-fields-with-android-5.10-encoder-mask-preserved",
        "scope": [
            "drm_display_mode legacy object id storage",
            "drm_display_mode downstream private pointer",
            "drm_connector legacy encoder id array",
            "drm_connector downstream HDR metadata",
            "drm_connector panel linkage",
            "dual-write encoder association to legacy array and 5.10 bitmask",
        ],
        "explicitly_deferred": [
            "DRM helper callback signature migration",
            "DMA-BUF mapping API migration",
            "IOMMU fault and domain attribute ABI",
            "hardware validation and flashable packaging",
        ],
    }
    report["validation"] = validate(gki)

    expected_first_application = {
        "display_mode.base": report["display_mode"]["base"] == 1,
        "display_mode.private": report["display_mode"]["private"] == 1,
        "connector_header.encoder_limit": (
            report["connector_header"]["encoder_limit"] == 1
        ),
        "connector_header.compatibility_fields": (
            report["connector_header"]["compatibility_fields"]
            == [
                "encoder_ids",
                "color_enc_fmt",
                "hdr_eotf",
                "hdr_metadata_type_one",
                "hdr_max_luminance",
                "hdr_avg_luminance",
                "hdr_min_luminance",
                "hdr_supported",
                "hdr_plus_app_ver",
                "panel",
            ]
        ),
        "connector_core.encoder_array_mirror": (
            report["connector_core"]["encoder_array_mirror"] == 1
        ),
    }
    failures = [
        name for name, passed in report["validation"].items() if not passed
    ]
    failures.extend(
        name for name, passed in expected_first_application.items() if not passed
    )

    (output / "phase12-drm-legacy-fields-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    if failures:
        raise SystemExit(
            "Workflow 114 staging validation failed: " + ", ".join(failures)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
