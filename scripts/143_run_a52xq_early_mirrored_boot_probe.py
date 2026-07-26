#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import traceback
from pathlib import Path

MODULE_NAME = "142_apply_a52xq_early_mirrored_boot_probe.py"
REPORT = "phase19-ack-early-mirrored-boot-probe-report.json"
HEARTBEAT_START = "EXPORT_SYMBOL_GPL(a52_ackfr_record);"
HEARTBEAT_END = "late_initcall(a52_usr2_late_retry);"
HEARTBEAT_MARKER = "/* A52_ACKFR_BOOT_PHASE_HEARTBEATS */"


def load_module():
    path = Path(__file__).with_name(MODULE_NAME)
    spec = importlib.util.spec_from_file_location("a52_probe142", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load Probe 142: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BACKEND_PATTERN = re.compile(
        r"(?m)^unsigned int a52_ackfr_ramoops_write\(const char \*buf, size_t len,\n"
        r"[\s\S]*?^EXPORT_SYMBOL_GPL\(a52_ackfr_ramoops_write\);"
    )
    return module


def write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def snapshot(logs: Path, label: str, path: Path) -> None:
    if path.is_file():
        shutil.copy2(path, logs / f"probe142-{label}-{path.name}")


def patch_generator_bounded(module, path: Path) -> dict[str, object]:
    text = module.read(path)

    if "A52_ACKFR_EARLY_MIRRORED_BACKEND" in text:
        backend_state = "already-present"
    else:
        matches = list(module.BACKEND_PATTERN.finditer(text))
        if len(matches) != 1:
            raise SystemExit(
                f"unified backend anchor mismatch: expected 1, found {len(matches)}"
            )
        match = matches[0]
        text = text[: match.start()] + module.BACKEND_NEW + text[match.end() :]
        backend_state = "inserted"

    if HEARTBEAT_MARKER in text:
        if text.count(HEARTBEAT_MARKER) != 1:
            raise SystemExit("boot heartbeat marker count is not one")
        heartbeat_state = "already-present"
    else:
        start_count = text.count(HEARTBEAT_START)
        end_count = text.count(HEARTBEAT_END)
        if start_count != 1 or end_count != 1:
            raise SystemExit(
                "bounded boot heartbeat anchors mismatch: "
                f"start={start_count} end={end_count}"
            )
        start = text.index(HEARTBEAT_START)
        end = text.index(HEARTBEAT_END, start) + len(HEARTBEAT_END)
        old_block = text[start:end]
        required_old = (
            "static int __init a52_usr2_device_retry(void)",
            "static int __init a52_usr2_late_retry(void)",
            'a52_usr2_write_control("BOOT_READY")',
        )
        missing_old = [item for item in required_old if item not in old_block]
        if missing_old:
            raise SystemExit(
                "bounded heartbeat source audit failed: " + ", ".join(missing_old)
            )
        text = text[:start] + module.RECORDER_TAIL_NEW + text[end:]
        heartbeat_state = "inserted-bounded"

    audit_anchor = (
        '        "direct_writer_exported": '
        '"EXPORT_SYMBOL_GPL(a52_ackfr_ramoops_write);" in text,\n'
    )
    audit_replacement = (
        audit_anchor
        + '        "early_console_backend": '
        '"a52_persistent_diag_mark(\\"%.*s\\"" in text,\n'
        + '        "early_ftrace_backend": '
        '"a52_persistent_diag_mark_ftrace(\\"%.*s\\"" in text,\n'
    )
    if '"early_ftrace_backend"' in text:
        if text.count('"early_ftrace_backend"') != 1:
            raise SystemExit("unified backend audit marker count is not one")
        audit_state = "already-present"
    else:
        count = text.count(audit_anchor)
        if count != 1:
            raise SystemExit(
                f"unified backend audit anchor mismatch: expected 1, found {count}"
            )
        text = text.replace(audit_anchor, audit_replacement, 1)
        audit_state = "inserted"

    required = (
        "A52_ACKFR_EARLY_MIRRORED_BACKEND",
        HEARTBEAT_MARKER,
        '"early_console_backend"',
        '"early_ftrace_backend"',
        'a52_ackfr_record("BOOT phase=pre_smp")',
        'a52_ackfr_record("BOOT phase=late")',
    )
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit("bounded unified generator audit failed: " + ", ".join(missing))
    module.write(path, text)
    return {
        "source": path.name,
        "backend": backend_state,
        "heartbeats": heartbeat_state,
        "audit": audit_state,
        "anchor_mode": "bounded-start-end",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    module = load_module()
    root = args.gki.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    logs = output.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "probe142.log"
    lines = ["probe142 wrapper begin"]

    generator = Path(__file__).with_name(module.GENERATOR_NAME)
    decoder = Path(__file__).with_name(module.DECODER_NAME)
    main_source = root / module.MAIN_REL
    ramoops_source = root / module.RAMOOPS_REL

    try:
        lines.append("self_test begin")
        module.self_test()
        lines.append("self_test success")

        generator_text = generator.read_text(encoding="utf-8", errors="replace")
        backend_matches = list(module.BACKEND_PATTERN.finditer(generator_text))
        lines.append(f"anchored_backend_matches={len(backend_matches)}")
        if len(backend_matches) != 1:
            raise RuntimeError(
                "anchored unified backend definition count mismatch: "
                f"expected 1, found {len(backend_matches)}"
            )
        match_start = backend_matches[0].start()
        if generator_text[max(0, match_start - 7):match_start] == "extern ":
            raise RuntimeError("anchored backend pattern still matched an extern declaration")

        missing = [str(path) for path in (main_source, ramoops_source) if not path.is_file()]
        if missing:
            raise RuntimeError("missing early mirrored probe sources: " + ", ".join(missing))

        lines.append("patch_ramoops begin")
        ramoops = module.patch_ramoops_early_mirror(ramoops_source)
        lines.append("patch_ramoops success " + json.dumps(ramoops, sort_keys=True))

        lines.append("patch_main begin")
        main = module.patch_main(main_source)
        lines.append("patch_main success " + json.dumps(main, sort_keys=True))

        lines.append("patch_generator_bounded begin")
        generator_result = patch_generator_bounded(module, generator)
        lines.append(
            "patch_generator_bounded success "
            + json.dumps(generator_result, sort_keys=True)
        )

        lines.append("patch_decoder begin")
        decoder_result = module.patch_decoder(decoder)
        lines.append("patch_decoder success " + json.dumps(decoder_result, sort_keys=True))

        report = {
            "status": "ack-early-mirrored-boot-probe-142-staged",
            "hardware_validated": False,
            "payload_capture": False,
            "mirroring": True,
            "banks": ["ramoops-console", "ramoops-ftrace"],
            "early_marker": module.EARLY_MARKER,
            "ramoops": ramoops,
            "main": main,
            "generator": generator_result,
            "decoder": decoder_result,
            "boot_phases": [
                "mm_init", "pre_smp", "pure", "core", "postcore",
                "arch", "subsys", "fs", "device", "late",
            ],
            "scope": (
                "prove the 5.10 kernel reaches mm_init and identify the final "
                "completed initcall level before secure-driver metadata begins"
            ),
        }
        (output / REPORT).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        lines.append("report success")
        write_log(log_path, lines)
        return 0
    except BaseException:
        lines.append("failure")
        lines.extend(traceback.format_exc().splitlines())
        write_log(log_path, lines)
        snapshot(logs, "main", main_source)
        snapshot(logs, "ramoops", ramoops_source)
        snapshot(logs, "generator", generator)
        snapshot(logs, "decoder", decoder)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
