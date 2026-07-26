#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

ION_REL = Path("drivers/staging/android/ion/ion.c")
QSEE_REL = Path("drivers/a52_secure/qseecom.c")
GENERATOR_NAME = "140_apply_a52xq_unified_secure_startup_recorder.py"
ION_MARKER = "ION result fd=%d len=%llu heap=%x flags=%x"
QSEE_API_MARKER = "QSEE SEND api req=%u rsp=%u"
QSEE_CORE_MARKER = "QSEE SEND core id=%u app=%s req=%u rsp=%u"
PR_FMT_OLD = '#define pr_fmt(fmt) "A52USR2: " fmt'
PR_FMT_NEW = '#undef pr_fmt\n#define pr_fmt(fmt) "A52USR2: " fmt'
AUDIT_OLD = (
    '        "bounded": f"#define A52_USR2_CAPACITY {CAPACITY}U" in source,\n'
)
AUDIT_NEW = AUDIT_OLD + (
    '        "pr_fmt_reset": "#undef pr_fmt\\n#define pr_fmt" in source,\n'
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def replace_call_once(text: str, old: str, new: str, label: str) -> tuple[str, str]:
    new_count = text.count(new)
    if new_count:
        if new_count != 1:
            raise SystemExit(f"{label}: expected one staged marker, found {new_count}")
        return text, "already-present"
    old_count = text.count(old)
    if old_count != 1:
        raise SystemExit(f"{label}: expected one generic marker, found {old_count}")
    return text.replace(old, new, 1), "inserted"


def patch_unified_generator(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise SystemExit(f"missing unified recorder generator: {path}")
    text = read(path)

    if PR_FMT_NEW in text:
        pr_fmt_state = "already-present"
    else:
        count = text.count(PR_FMT_OLD)
        if count != 1:
            raise SystemExit(
                f"unified recorder pr_fmt anchor mismatch: expected 1, found {count}"
            )
        text = text.replace(PR_FMT_OLD, PR_FMT_NEW, 1)
        pr_fmt_state = "inserted"

    if AUDIT_NEW in text:
        audit_state = "already-present"
    else:
        count = text.count(AUDIT_OLD)
        if count != 1:
            raise SystemExit(
                f"unified recorder audit anchor mismatch: expected 1, found {count}"
            )
        text = text.replace(AUDIT_OLD, AUDIT_NEW, 1)
        audit_state = "inserted"

    if text.count(PR_FMT_NEW) != 1:
        raise SystemExit("unified recorder pr_fmt reset count is not one")
    if text.count(AUDIT_NEW) != 1:
        raise SystemExit("unified recorder pr_fmt audit count is not one")
    write(path, text)
    return {
        "source": path.name,
        "pr_fmt_reset": pr_fmt_state,
        "source_audit": audit_state,
    }


def patch_ion(root: Path) -> dict[str, object]:
    path = root / ION_REL
    text = read(path)
    if text.count(ION_MARKER) == 1:
        return {"source": str(ION_REL), "allocation_result": "already-present"}
    if ION_MARKER in text:
        raise SystemExit("ION allocation-result marker is duplicated")

    pattern = re.compile(
        r"(?m)^(?P<indent>[ \t]*)data\.allocation\.fd[ \t]*=[ \t]*fd;[ \t]*$"
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise SystemExit(
            f"ION allocation-result anchor count mismatch: expected 1, found {len(matches)}"
        )
    match = matches[0]
    indent = match.group("indent")
    addition = (
        match.group(0)
        + "\n"
        + f"{indent}a52_ackfr_record(\n"
        + f'{indent}\t"{ION_MARKER}",\n'
        + f"{indent}\tfd, (unsigned long long)data.allocation.len,\n"
        + f"{indent}\tdata.allocation.heap_id_mask, data.allocation.flags);"
    )
    text = text[: match.start()] + addition + text[match.end() :]
    write(path, text)
    return {"source": str(ION_REL), "allocation_result": "inserted"}


def patch_qsee(root: Path) -> dict[str, object]:
    path = root / QSEE_REL
    text = read(path)

    api_old = 'a52_ackfr_record("QSEE enter fn=qseecom_send_command")'
    api_new = (
        f'a52_ackfr_record("{QSEE_API_MARKER}", '
        "sbuf_len, rbuf_len)"
    )
    text, api_state = replace_call_once(
        text, api_old, api_new, "QSEECOM kernel SEND_CMD API"
    )

    core_old = 'a52_ackfr_record("QSEE enter fn=__qseecom_send_cmd")'
    core_new = (
        f'a52_ackfr_record("{QSEE_CORE_MARKER}", '
        'data ? data->client.app_id : 0, '
        'data ? data->client.app_name : "<null>", '
        'req ? req->cmd_req_len : 0, req ? req->resp_len : 0)'
    )
    text, core_state = replace_call_once(
        text, core_old, core_new, "QSEECOM core SEND_CMD"
    )

    if text.count(QSEE_API_MARKER) != 1:
        raise SystemExit("QSEECOM kernel SEND_CMD API marker count is not one")
    if text.count(QSEE_CORE_MARKER) != 1:
        raise SystemExit("QSEECOM core SEND_CMD marker count is not one")
    write(path, text)
    return {
        "source": str(QSEE_REL),
        "send_api": api_state,
        "send_core": core_state,
    }


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ion = root / ION_REL
        qsee = root / QSEE_REL
        generator = root / GENERATOR_NAME
        ion.parent.mkdir(parents=True, exist_ok=True)
        qsee.parent.mkdir(parents=True, exist_ok=True)
        ion.write_text(
            "static void f(void)\n{\n\tdata.allocation.fd = fd;\n}\n",
            encoding="utf-8",
        )
        qsee.write_text(
            "int a = (a52_ackfr_record(\"QSEE enter fn=qseecom_send_command\"), 0);\n"
            "int b = (a52_ackfr_record(\"QSEE enter fn=__qseecom_send_cmd\"), 0);\n",
            encoding="utf-8",
        )
        generator.write_text(
            "SOURCE = r'''\n"
            + PR_FMT_OLD
            + "\n'''\n"
            + "checks = {\n"
            + AUDIT_OLD
            + "}\n",
            encoding="utf-8",
        )
        first_generator = patch_unified_generator(generator)
        second_generator = patch_unified_generator(generator)
        first_ion = patch_ion(root)
        first_qsee = patch_qsee(root)
        second_ion = patch_ion(root)
        second_qsee = patch_qsee(root)
        if first_generator["pr_fmt_reset"] != "inserted":
            raise SystemExit("unified recorder generator self-test did not insert")
        if second_generator["pr_fmt_reset"] != "already-present":
            raise SystemExit("unified recorder generator patch is not idempotent")
        if first_ion["allocation_result"] != "inserted":
            raise SystemExit("ION parameter-probe self-test did not insert")
        if first_qsee["send_api"] != "inserted" or first_qsee["send_core"] != "inserted":
            raise SystemExit("QSEECOM parameter-probe self-test did not insert")
        if second_ion["allocation_result"] != "already-present":
            raise SystemExit("ION parameter probe is not idempotent")
        if second_qsee["send_api"] != "already-present" or second_qsee["send_core"] != "already-present":
            raise SystemExit("QSEECOM parameter probe is not idempotent")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    self_test()

    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    missing = [str(item) for item in (ION_REL, QSEE_REL) if not (root / item).is_file()]
    if missing:
        raise SystemExit("missing staged ACK parameter-probe sources: " + ", ".join(missing))

    generator = Path(__file__).with_name(GENERATOR_NAME)
    report = {
        "status": "ack-secure-parameter-probe-141-staged",
        "hardware_validated": False,
        "payload_capture": False,
        "unified_generator": patch_unified_generator(generator),
        "ion": patch_ion(root),
        "qsee": patch_qsee(root),
        "markers": {
            "ion_allocation_result": ION_MARKER,
            "qsee_send_api": QSEE_API_MARKER,
            "qsee_send_core": QSEE_CORE_MARKER,
        },
        "scope": (
            "record ION allocation fd/length/heap/flags and QSEECOM SEND_CMD "
            "buffer lengths plus app identity without copying secure payload bytes"
        ),
    }
    (output / "phase18-ack-secure-parameter-probe-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
