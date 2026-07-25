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


def function_bounds(text: str, signature: str) -> tuple[int, int]:
    start = text.find(signature)
    opening = text.find("{", start)
    if start < 0 or opening < 0:
        raise SystemExit(f"missing function: {signature}")
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return opening, index
    raise SystemExit(f"unterminated function: {signature}")


def patch_ion(gki: Path) -> dict[str, int]:
    path = gki / "drivers/staging/android/ion/ion.c"
    text = read(path)
    changes = 0

    if MARKER not in text:
        signature = "static long ion_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)"
        anchor = signature + "\n{"
        if anchor not in text:
            raise SystemExit("ion_ioctl anchor missing")
        text = text.replace(
            anchor,
            anchor + "\n\t/* " + MARKER + " */\n"
            "\tpr_info(\"A52ION enter pid=%d comm=%s cmd=0x%x arg=0x%lx\\n\",\n"
            "\t\tcurrent->pid, current->comm, cmd, arg);",
            1,
        )
        changes += 1

        opening, closing = function_bounds(text, signature)
        body = text[opening + 1 : closing]
        matches = list(re.finditer(r"(?m)^\treturn ret;\s*$", body))
        if not matches:
            raise SystemExit("ion_ioctl return anchor missing")
        rel = matches[-1].start()
        pos = opening + 1 + rel
        text = text[:pos] + (
            "\tpr_info(\"A52ION exit pid=%d comm=%s cmd=0x%x ret=%d\\n\",\n"
            "\t\tcurrent->pid, current->comm, cmd, ret);\n"
        ) + text[pos:]
        changes += 1

        alloc_anchor = "struct dma_buf *ion_alloc(size_t len, unsigned int heap_id_mask, unsigned int flags)\n{"
        if alloc_anchor not in text:
            raise SystemExit("ion_alloc anchor missing")
        text = text.replace(
            alloc_anchor,
            alloc_anchor + "\n\tpr_info(\"A52IONALLOC enter pid=%d comm=%s len=%zu heaps=0x%x flags=0x%x\\n\",\n"
            "\t\tcurrent->pid, current->comm, len, heap_id_mask, flags);",
            1,
        )
        changes += 1

    write(path, text)
    return {"changes": changes}


def inject_function_entry(text: str, name: str) -> tuple[str, int]:
    pattern = re.compile(
        r"(?m)^(?P<sig>(?:static\s+)?(?:long|int|void)\s+" + re.escape(name) + r"\s*\([^;]*?\))\s*\n\{"
    )
    match = pattern.search(text)
    if not match:
        return text, 0
    insert = (
        match.group(0)
        + "\n\tpr_info(\"A52QSEE enter fn=%s pid=%d comm=%s\\n\", "
        + "__func__, current->pid, current->comm);"
    )
    return text[: match.start()] + insert + text[match.end() :], 1


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

        for name in (
            "qseecom_open",
            "qseecom_release",
            "qseecom_ioctl",
            "qseecom_load_app",
            "qseecom_unload_app",
            "qseecom_send_cmd",
            "qseecom_send_modfd_cmd",
        ):
            text, n = inject_function_entry(text, name)
            if n:
                found.append(name)
                changes += n

        if "qseecom_ioctl" not in found or "qseecom_release" not in found:
            raise SystemExit("required qseecom ioctl/release trace anchors missing")

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
