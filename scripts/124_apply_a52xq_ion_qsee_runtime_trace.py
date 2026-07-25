#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

MARKER = "A52_ION_QSEE_RUNTIME_TRACE"


def read(path: Path) -> str:
    return path.read_text(errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text)


def function_bounds(text: str, name: str) -> tuple[int, int] | None:
    """Return opening/closing brace offsets for a C function definition."""
    for match in re.finditer(r"\b" + re.escape(name) + r"\s*\(", text):
        paren = match.end() - 1
        depth = 0
        close_paren = -1
        for index in range(paren, len(text)):
            char = text[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    close_paren = index
                    break
        if close_paren < 0:
            continue
        between = text[close_paren + 1 : close_paren + 80]
        brace_rel = between.find("{")
        semicolon_rel = between.find(";")
        if brace_rel < 0 or (semicolon_rel >= 0 and semicolon_rel < brace_rel):
            continue
        opening = close_paren + 1 + brace_rel
        brace_depth = 0
        for index in range(opening, len(text)):
            if text[index] == "{":
                brace_depth += 1
            elif text[index] == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    return opening, index
    return None


def inject_entry(text: str, name: str, prefix: str) -> tuple[str, int]:
    bounds = function_bounds(text, name)
    if bounds is None:
        return text, 0
    opening, _ = bounds
    marker = f'{prefix} enter fn={name}'
    if marker in text[opening : opening + 300]:
        return text, 0
    line = (
        f'\n\tpr_info("{prefix} enter fn={name} pid=%d comm=%s\\n", '
        "current->pid, current->comm);"
    )
    return text[: opening + 1] + line + text[opening + 1 :], 1


def patch_ion(gki: Path) -> dict[str, int]:
    path = gki / "drivers/staging/android/ion/ion.c"
    text = read(path)
    changes = 0

    if MARKER not in text:
        bounds = function_bounds(text, "ion_ioctl")
        if bounds is None:
            raise SystemExit("ion_ioctl function missing")
        opening, closing = bounds
        entry = (
            "\n\t/* " + MARKER + " */\n"
            "\tpr_info(\"A52ION enter pid=%d comm=%s cmd=0x%x arg=0x%lx\\n\",\n"
            "\t\tcurrent->pid, current->comm, cmd, arg);"
        )
        text = text[: opening + 1] + entry + text[opening + 1 :]
        changes += 1

        bounds = function_bounds(text, "ion_ioctl")
        if bounds is None:
            raise SystemExit("ion_ioctl vanished after entry patch")
        opening, closing = bounds
        body = text[opening + 1 : closing]
        returns = list(re.finditer(r"(?m)^\treturn ret;\s*$", body))
        if not returns:
            raise SystemExit("ion_ioctl final return ret anchor missing")
        position = opening + 1 + returns[-1].start()
        exit_line = (
            "\tpr_info(\"A52ION exit pid=%d comm=%s cmd=0x%x ret=%d\\n\",\n"
            "\t\tcurrent->pid, current->comm, cmd, ret);\n"
        )
        text = text[:position] + exit_line + text[position:]
        changes += 1

        text, added = inject_entry(text, "ion_alloc", "A52IONALLOC")
        if not added:
            raise SystemExit("ion_alloc trace anchor missing")
        changes += added

    write(path, text)
    return {"changes": changes}


def patch_qsee(gki: Path) -> dict[str, object]:
    path = gki / "drivers/a52_secure/qseecom.c"
    text = read(path)
    changes = 0
    found: list[str] = []

    if MARKER not in text:
        include_anchor = "#include <linux/module.h>\n"
        if include_anchor in text:
            text = text.replace(include_anchor, include_anchor + "/* " + MARKER + " */\n", 1)
        else:
            text = "/* " + MARKER + " */\n" + text

        names = (
            "qseecom_open",
            "qseecom_release",
            "qseecom_ioctl",
            "qseecom_start_app",
            "qseecom_shutdown_app",
            "qseecom_send_command",
            "qseecom_send_modfd_cmd",
            "qseecom_load_app",
            "qseecom_unload_app",
            "__qseecom_load_fw",
            "__qseecom_send_cmd",
        )
        for name in names:
            text, added = inject_entry(text, name, "A52QSEE")
            if added:
                found.append(name)
                changes += added

        required_groups = (
            {"qseecom_open"},
            {"qseecom_release"},
            {"qseecom_ioctl"},
            {"qseecom_start_app", "qseecom_load_app", "__qseecom_load_fw"},
            {"qseecom_send_command", "qseecom_send_modfd_cmd", "__qseecom_send_cmd"},
        )
        missing = [sorted(group) for group in required_groups if not group.intersection(found)]
        if missing:
            raise SystemExit("required qseecom trace groups missing: " + repr(missing))

    write(path, text)
    return {"changes": changes, "functions": found}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    report = {
        "status": "ion-qsee-runtime-trace-staged",
        "hardware_validated": False,
        "ion": patch_ion(args.gki.resolve()),
        "qsee": patch_qsee(args.gki.resolve()),
        "markers": ["A52ION", "A52IONALLOC", "A52QSEE"],
    }
    (args.output / "phase16-ion-qsee-runtime-trace-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
