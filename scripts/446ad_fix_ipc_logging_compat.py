#!/usr/bin/env python3
"""Phase446AD: complete the pinned GKI IPC logging compatibility stub.

The Phase446S source snapshot supplies a small ipc_logging.h stub.  The
Qualcomm qsee_ipc_irq_bridge driver calls ipc_log_context_destroy(), but
that stub only declares ipc_log_context_create().  This is a no-op cleanup
because the compatibility header does not allocate a real IPC log context.

Apply after restoring the pinned source, before the strict final-tree audit.
Do not overwrite a real Qualcomm IPC logging implementation.
"""
import argparse
from pathlib import Path
import re

MARKER = "A52_PHASE446AD_IPC_LOG_CONTEXT_DESTROY_COMPAT_V1"
HEADER = Path("include/linux/ipc_logging.h")
IMPLEMENTATION = (
    "/* " + MARKER + ": no allocation in the IPC logging stub. */\n"
    "static inline void ipc_log_context_destroy(void *context)\n"
    "{\n"
    "    (void)context;\n"
    "}\n\n"
)


def audit(text: str) -> None:
    if text.count(MARKER) != 1:
        raise RuntimeError("P446AD: missing or duplicate compatibility marker")
    if not re.search(
        r"static\s+inline\s+void\s+ipc_log_context_destroy\s*\(\s*void\s*\*",
        text,
    ):
        raise RuntimeError("P446AD: destroy() stub is missing")
    if not re.search(
        r"static\s+inline\s+void\s*\*\s*ipc_log_context_create\s*\(",
        text,
    ):
        raise RuntimeError("P446AD: expected create() compatibility stub missing")
    print("P446AD IPC_LOG_STUB PASS: create/destroy available, no-op cleanup")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    path = args.root.resolve() / HEADER
    original = path.read_text()
    if not args.check_only and MARKER not in original:
        if re.search(r"\bipc_log_context_destroy\b", original):
            raise RuntimeError("P446AD: existing native destroy() must be preserved")
        if not re.search(
            r"static\s+inline\s+void\s*\*\s*ipc_log_context_create\s*\(",
            original,
        ):
            raise RuntimeError("P446AD: not the expected IPC logging stub")
        closing = list(re.finditer(r"(?m)^#endif\b[^\n]*$", original))
        if not closing:
            raise RuntimeError("P446AD: no header guard to patch safely")
        pos = closing[-1].start()
        revised = original[:pos] + IMPLEMENTATION + original[pos:]
        path.write_text(revised)
        original = revised
    audit(original)


if __name__ == "__main__":
    main()
