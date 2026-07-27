#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

REPORT_NAME = "phase28-a52-active-display-scopes-report.json"
MARKER = "A52_ACTIVE_DISPLAY_SCOPES_V2"
INCLUDE = "#include <linux/a52_ack_secure_flight_recorder.h>"
CAPTURE_SHA256 = "afce7a237eb723f3b87c0326b6242e6a2816975057d193281e91bf958abd3614"

TARGETS: dict[Path, tuple[str, ...]] = {
    Path("drivers/a52_display/msm/dsi/dsi_drm.c"): (
        "dsi_bridge_pre_enable",
        "dsi_bridge_enable",
        "dsi_bridge_disable",
        "dsi_bridge_post_disable",
    ),
    Path("drivers/a52_display/msm/dsi/dsi_display.c"): (
        "dsi_display_prepare",
        "dsi_display_enable",
        "dsi_display_disable",
        "dsi_display_unprepare",
    ),
    Path("drivers/a52_display/msm/dsi/dsi_panel.c"): (
        "dsi_panel_prepare",
        "dsi_panel_enable",
        "dsi_panel_disable",
        "dsi_panel_unprepare",
    ),
    Path("drivers/a52_display/msm/sde/sde_crtc.c"): (
        "sde_crtc_commit_kickoff",
    ),
    Path("drivers/a52_display/msm/sde/sde_encoder.c"): (
        "sde_encoder_kickoff",
    ),
    Path("drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"): (
        "ss_panel_attach_set",
        "ss_panel_data_read_gpara",
        "ss_panel_on_pre",
        "ss_panel_on_post",
        "ss_panel_off_pre",
        "ss_panel_off_post",
    ),
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def add_include(text: str) -> tuple[str, bool]:
    if INCLUDE in text:
        return text, False
    matches = list(re.finditer(r"^#include[^\n]*\n", text, flags=re.M))
    if not matches:
        raise SystemExit("no include anchor found")
    pos = matches[-1].end()
    return text[:pos] + INCLUDE + "\n" + text[pos:], True


def mask_c(text: str) -> str:
    out = list(text)
    state = "normal"
    escaped = False
    i = 0
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ""
        if state == "normal":
            if c == "/" and n == "/":
                out[i] = out[i + 1] = " "
                state = "line-comment"
                i += 2
                continue
            if c == "/" and n == "*":
                out[i] = out[i + 1] = " "
                state = "block-comment"
                i += 2
                continue
            if c == '"':
                out[i] = " "
                state = "string"
                escaped = False
            elif c == "'":
                out[i] = " "
                state = "char"
                escaped = False
        elif state == "line-comment":
            if c == "\n":
                state = "normal"
            else:
                out[i] = " "
        elif state == "block-comment":
            if c == "*" and n == "/":
                out[i] = out[i + 1] = " "
                state = "normal"
                i += 2
                continue
            if c != "\n":
                out[i] = " "
        else:
            quote = '"' if state == "string" else "'"
            if c == "\n":
                escaped = False
            else:
                out[i] = " "
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == quote:
                    state = "normal"
        i += 1
    return "".join(out)


def definition_openings(text: str, name: str) -> list[int]:
    """Return opening braces for definitions, ignoring calls and prototypes.

    Do not use whole-file brace depth here. The downstream Samsung sources have
    mutually exclusive preprocessor branches whose raw braces are intentionally
    unbalanced until preprocessing.
    """
    masked = mask_c(text)
    openings: list[int] = []
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", masked):
        paren = match.end() - 1
        depth = 0
        close_paren = -1
        for i in range(paren, len(masked)):
            c = masked[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    close_paren = i
                    break
        if close_paren < 0:
            continue

        tail = masked[close_paren + 1 : close_paren + 4097]
        brace_rel = tail.find("{")
        semi_rel = tail.find(";")
        if brace_rel < 0 or (semi_rel >= 0 and semi_rel < brace_rel):
            continue
        openings.append(close_paren + 1 + brace_rel)
    return openings


def scope_name(function: str) -> str:
    return f"a52.{function}"


def inject_scope(text: str, function: str) -> tuple[str, str]:
    statement = f'A52_ACKFR_SCOPE("DISP", "{scope_name(function)}");'
    if statement in text:
        return text, "already-present"
    openings = definition_openings(text, function)
    if len(openings) != 1:
        raise SystemExit(
            f"active display definition count mismatch: {function}: {len(openings)}"
        )
    opening = openings[0]
    return text[: opening + 1] + "\n\t" + statement + text[opening + 1 :], "inserted"


def audit_file(path: Path, functions: tuple[str, ...]) -> None:
    text = read(path)
    if INCLUDE not in text:
        raise SystemExit(f"recorder include missing: {path}")
    for function in functions:
        statement = f'A52_ACKFR_SCOPE("DISP", "{scope_name(function)}");'
        count = text.count(statement)
        if count != 1:
            raise SystemExit(f"scope audit failed for {path}:{function}: count={count}")


def run(gki: Path, output: Path) -> dict[str, object]:
    results: list[dict[str, object]] = []
    inserted = 0
    for rel, functions in TARGETS.items():
        path = gki / rel
        if not path.is_file():
            raise SystemExit(f"compiled A52 display source missing: {path}")
        text, include_changed = add_include(read(path))
        states: list[dict[str, str]] = []
        for function in functions:
            text, state = inject_scope(text, function)
            inserted += int(state == "inserted")
            states.append(
                {
                    "function": function,
                    "scope": scope_name(function),
                    "state": state,
                }
            )
        write(path, text)
        audit_file(path, functions)
        results.append(
            {
                "path": str(rel),
                "include_changed": include_changed,
                "functions": states,
            }
        )

    total = sum(len(functions) for functions in TARGETS.values())
    report = {
        "status": "a52-active-display-scopes-v1-staged",
        "hardware_validated": False,
        "functional_change": "instrumentation-only",
        "refgen_logic_unchanged": True,
        "recorder_policy_unchanged": True,
        "active_tree": "drivers/a52_display",
        "inactive_tree_previously_instrumented": "techpack/display",
        "scope_domain": "DISP",
        "scope_prefix": "a52.",
        "parser": "definition-based-preprocessor-safe",
        "target_file_count": len(TARGETS),
        "target_function_count": total,
        "inserted_function_count": inserted,
        "payload_capture": False,
        "evidence_basis": {
            "capture_sha256": CAPTURE_SHA256,
            "screen_result": "black",
            "refgen_driver_register_rc": 0,
            "refgen_probe_ready_initial_enabled": 1,
            "heartbeat_last_tick": 103,
            "heartbeat_last_monotonic_ns": 52724026229,
            "recovered_display_scope_events": 0,
            "finding": (
                "the compiled objects came from drivers/a52_display while the prior "
                "scope patch targeted the inactive techpack/display duplicate"
            ),
        },
        "files": results,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-active-display-scopes-") as tmp:
        root = Path(tmp)
        for rel, functions in TARGETS.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            body = ["#include <linux/kernel.h>\n\n"]
            for index, function in enumerate(functions):
                qualifier = "static " if index % 2 else ""
                body.append(
                    f"{qualifier}int {function}(void *arg);\n"
                    f"{qualifier}int {function}(void *arg)\n"
                    "{\n"
                    "#if defined(TEST_A)\n"
                    "\tif (arg) { return 1; }\n"
                    "#else\n"
                    "\tif (!arg) { return 0; }\n"
                    "#endif\n"
                    "\treturn arg != 0;\n"
                    "}\n\n"
                )
            path.write_text("".join(body), encoding="utf-8")
        report = run(root, root / "report")
        expected = sum(len(v) for v in TARGETS.values())
        if report["target_function_count"] != expected:
            raise SystemExit("target-count self-test failed")
        if report["inserted_function_count"] != expected:
            raise SystemExit("insertion self-test failed")
        second = run(root, root / "report2")
        if second["inserted_function_count"] != 0:
            raise SystemExit("idempotence self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Instrument the compiled Galaxy A52 display tree with persistent "
            "metadata-only DISP scopes."
        )
    )
    parser.add_argument("--gki", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print(json.dumps({"status": "self-test-passed"}, sort_keys=True))
        return 0
    if args.gki is None or args.output is None:
        parser.error("--gki and --output are required unless --self-test is used")

    report = run(args.gki.resolve(), args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
