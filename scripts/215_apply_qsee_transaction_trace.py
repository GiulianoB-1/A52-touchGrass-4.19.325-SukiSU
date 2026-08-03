#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

SOURCE = Path("drivers/a52_secure/qseecom.c")
INCLUDE_ANCHOR = "#include <linux/kernel.h>\n"
MACRO_ANCHOR = "#include <soc/qcom/qtee_shmbridge.h>\n\n"
MARKER = "A52_R215_QSEE_TRACE_LIMIT"

REPLACEMENTS = [
    ("SCM A52USR2 enter fn=__qseecom_scm_call2_locked smc=0x%x arginfo=0x%x", "scm smc=%x ai=%x", 1),
    ("SCM exit fn=__qseecom_scm_call2_locked ret=%ld", "scm-ret rc=%ld", 1),
    ("SCM A52USR2 enter fn=qseecom_scm_call2 svc=0x%x tz_cmd=0x%x", "scm2 svc=%x cmd=%x", 1),
    ("SCM exit fn=qseecom_scm_call2 ret=%ld", "scm2-ret rc=%ld", 9),
    ("DMABUF A52USR2 enter fn=qseecom_dmabuf_cache_operations cache_op=%d", "cache op=%d", 1),
    ("DMABUF exit fn=qseecom_dmabuf_cache_operations ret=%ld", "cache-ret rc=%ld", 1),
    ("DMABUF A52USR2 enter fn=qseecom_create_bridge_for_secbuf fd=%d", "bridge fd=%d", 1),
    ("DMABUF exit fn=qseecom_create_bridge_for_secbuf ret=%ld", "bridge-ret rc=%ld", 4),
    ("DMABUF A52USR2 enter fn=qseecom_dmabuf_map fd=%d", "map fd=%d", 1),
    ("DMABUF exit fn=qseecom_dmabuf_map ret=%ld", "map-ret rc=%ld", 2),
    ("DMABUF A52USR2 enter fn=qseecom_dmabuf_unmap stage=unmap", "unmap", 1),
    ("QSEE enter fn=qseecom_load_app", "load", 1),
    ("QSEE exit fn=qseecom_load_app ret=%ld", "load-ret rc=%ld", 5),
    ("QSEE SEND core id=%u app=%s req=%u rsp=%u", "send id=%u app=%.16s q=%u r=%u", 1),
    ("QSEE exit fn=__qseecom_send_cmd ret=%ld", "send-ret rc=%ld", 5),
    ("QSEE enter fn=qseecom_start_app app=%s", "start app=%.24s", 1),
    ("QSEE exit fn=qseecom_start_app ret=%ld", "start-ret rc=%ld", 7),
    ("QSEE enter fn=qseecom_ioctl cmd=0x%x arg=0x%lx", "ioctl cmd=%x arg=%lx", 1),
    ("QSEE exit fn=qseecom_ioctl cmd=0x%x ret=%ld", "ioctl-ret cmd=%x rc=%ld", 14),
    ("QSEE enter fn=qseecom_open", "open", 1),
    ("QSEE exit fn=qseecom_open ret=%ld", "open-ret rc=%ld", 3),
    ("QSEE enter fn=qseecom_release", "release", 1),
    ("QSEE exit fn=qseecom_release ret=%ld", "release-ret rc=%ld", 1),
]

MACRO = r'''#define A52_R215_QSEE_TRACE_LIMIT 256U
static atomic_t a52_r215_qsee_trace_sequence = ATOMIC_INIT(0);

#define A52_R215_TRACE(fmt, ...) ({                                      \
	unsigned int __a52_r215_id = (unsigned int)atomic_inc_return(      \
		&a52_r215_qsee_trace_sequence);                              \
	if (__a52_r215_id <= A52_R215_QSEE_TRACE_LIMIT)                    \
		a52_ackfr_record("IONPOST 215 n=%u " fmt,                    \
			__a52_r215_id, ##__VA_ARGS__);                         \
	0;                                                                 \
})

'''


def apply_text(text: str) -> str:
    if MARKER in text:
        raise RuntimeError("Phase 215 trace already applied")
    if text.count(INCLUDE_ANCHOR) != 1:
        raise RuntimeError("kernel include anchor count mismatch")
    if text.count(MACRO_ANCHOR) != 1:
        raise RuntimeError("QSEECOM macro anchor count mismatch")

    text = text.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + "#include <linux/atomic.h>\n", 1)
    text = text.replace(MACRO_ANCHOR, MACRO_ANCHOR + MACRO, 1)

    for old_message, new_message, expected in REPLACEMENTS:
        old = f'a52_ackfr_record("{old_message}"'
        new = f'A52_R215_TRACE("{new_message}"'
        actual = text.count(old)
        if actual != expected:
            raise RuntimeError(
                f"trace anchor mismatch for {old_message!r}: expected {expected}, got {actual}"
            )
        text = text.replace(old, new)

    required = [
        "#define A52_R215_QSEE_TRACE_LIMIT 256U",
        'A52_R215_TRACE("ioctl cmd=%x arg=%lx"',
        'A52_R215_TRACE("ioctl-ret cmd=%x rc=%ld"',
        'A52_R215_TRACE("scm smc=%x ai=%x"',
        'A52_R215_TRACE("scm-ret rc=%ld"',
        'A52_R215_TRACE("send id=%u app=%.16s q=%u r=%u"',
        'A52_R215_TRACE("cache op=%d"',
        'A52_R215_TRACE("bridge fd=%d"',
        'A52_R215_TRACE("open"',
        'A52_R215_TRACE("release"',
    ]
    for item in required:
        if item not in text:
            raise RuntimeError(f"missing patched marker: {item}")

    for old_message, _, _ in REPLACEMENTS:
        if f'a52_ackfr_record("{old_message}"' in text:
            raise RuntimeError(f"unreplaced Phase 215 anchor: {old_message}")

    return text


def self_test() -> None:
    fixture = (
        INCLUDE_ANCHOR
        + MACRO_ANCHOR
        + "\n".join(
            f'void f_{index}_{copy}(void) {{ a52_ackfr_record("{old}"); }}'
            for index, (old, _, expected) in enumerate(REPLACEMENTS)
            for copy in range(expected)
        )
        + "\n"
    )
    patched = apply_text(fixture)
    if patched.count("IONPOST 215 n=%u ") != 1:
        raise AssertionError("Phase 215 macro prefix count mismatch")
    expected_calls = sum(expected for _, _, expected in REPLACEMENTS)
    if patched.count("A52_R215_TRACE(") != expected_calls + 1:
        raise AssertionError("Phase 215 replacement count mismatch")

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "qseecom.c"
        path.write_text(fixture, encoding="utf-8")
        path.write_text(apply_text(path.read_text(encoding="utf-8")), encoding="utf-8")
        if MARKER not in path.read_text(encoding="utf-8"):
            raise AssertionError("Phase 215 self-test marker missing")
    print("phase215 bounded QSEECOM transaction trace self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    path = args.root / SOURCE
    if not path.is_file():
        raise SystemExit(f"missing QSEECOM source: {path}")
    original = path.read_text(encoding="utf-8")
    patched = apply_text(original)
    path.write_text(patched, encoding="utf-8")
    print(f"Phase 215 bounded QSEECOM transaction trace applied: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
