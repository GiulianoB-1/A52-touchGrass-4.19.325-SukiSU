#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

MARKER = "A52_LEGACY_ION_FREE_IOCTL_COMPAT"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    gki = args.gki.resolve()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    path = gki / "drivers/staging/android/ion/ion.c"
    if not path.is_file():
        raise SystemExit(f"missing ACK ION source: {path}")

    text = path.read_text(errors="replace")
    if "static long ion_ioctl(" not in text:
        raise SystemExit("ACK ion_ioctl function was not found")
    if "struct dma_buf *ion_alloc(size_t len" not in text:
        raise SystemExit("expected ACK dma-buf ION allocator is missing")

    changed = False
    if MARKER not in text:
        anchor = "static long ion_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)\n{"
        if anchor not in text:
            raise SystemExit("exact ion_ioctl anchor was not found")

        old = "\tint ret = 0;\n\tunion ion_ioctl_arg data;\n"
        if old not in text:
            raise SystemExit("ion_ioctl local-variable anchor was not found")

        new = old + "\n\t/* " + MARKER + "\n" \
            "\t * Samsung's legacy libion still releases an opaque 32-bit handle with\n" \
            "\t * _IOW('I', 1, __u32), observed as 0xc0044901. ACK 5.10 returns\n" \
            "\t * dma-buf file descriptors directly, so closing the fd already owns the\n" \
            "\t * allocation lifetime. Accept the obsolete handle-release notification\n" \
            "\t * without recreating the removed per-client handle subsystem.\n" \
            "\t */\n" \
            "\tif (cmd == _IOW('I', 1, __u32)) {\n" \
            "\t\tpr_info_ratelimited(\"a52_ion_compat: accepted legacy ION_IOC_FREE\\n\");\n" \
            "\t\treturn 0;\n" \
            "\t}\n"
        text = text.replace(old, new, 1)
        path.write_text(text)
        changed = True

    final = path.read_text(errors="replace")
    checks = {
        "marker_present": MARKER in final,
        "exact_legacy_command": "cmd == _IOW('I', 1, __u32)" in final,
        "success_return": "accepted legacy ION_IOC_FREE" in final and "return 0;" in final,
        "ack_allocator_preserved": "struct dma_buf *ion_alloc(size_t len" in final,
        "no_legacy_handle_subsystem": "struct ion_handle" not in final,
        "modern_ioctl_preserved": "case ION_IOC_ALLOC:" in final and "case ION_IOC_HEAP_QUERY:" in final,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit("legacy ION free compatibility audit failed: " + ", ".join(failed))

    report = {
        "status": "legacy-ion-free-compat-staged",
        "runtime_fix": "accept-samsung-c0044901-on-ack-dmabuf-ion",
        "changed": changed,
        "flashable": False,
        "hardware_validated": False,
        "observed_ioctl": "0xc0044901",
        "semantics": "legacy handle release accepted as no-op; dma-buf fd remains lifetime owner",
        "checks": checks,
    }
    (out / "phase15-legacy-ion-free-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
