#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

REPORT_NAME = "phase32-a52-heap19-display-lifecycle-report.json"
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
OLD_PROFILE = "heap19-bufops-v1"
PROFILE = "heap19-bufops-display-lifecycle-v1"
CAPTURE_SHA256 = "f5897492c6fbb5f42324bc997e4fb9ed4bf05e64df035996b85141d5d90d2050"

TARGETS: dict[Path, tuple[str, ...]] = {
    Path("drivers/a52_display/msm/msm_drv.c"): (
        "msm_drm_register", "msm_pdev_probe", "msm_drm_bind",
        "msm_drm_init", "_msm_drm_init_helper",
    ),
    Path("drivers/a52_display/msm/dsi/dsi_display.c"): (
        "dsi_display_register", "dsi_display_dev_probe", "dsi_display_init",
        "dsi_display_bind", "dsi_display_res_init", "dsi_display_drm_bridge_init",
    ),
    Path("drivers/a52_display/msm/dsi/dsi_drm.c"): ("dsi_bridge_attach",),
    Path("drivers/a52_display/msm/dsi/dsi_panel.c"): (
        "dsi_panel_get", "dsi_panel_drv_init",
    ),
    Path("drivers/a52_display/msm/dsi/dsi_phy.c"): ("dsi_phy_enable",),
    Path("drivers/a52_display/msm/dsi/dsi_clk_manager.c"): (
        "dsi_clk_update_link_clk_state",
    ),
    Path("drivers/a52_display/msm/sde/sde_kms.c"): (
        "sde_kms_init", "sde_kms_hw_init", "_sde_kms_hw_init_blocks",
        "sde_kms_prepare_commit", "sde_kms_commit", "sde_kms_complete_commit",
    ),
    Path("drivers/a52_display/msm/samsung/ss_dsi_panel_common.c"): (
        "ss_panel_init", "ss_early_display_init",
    ),
}


def load_scope_helper():
    path = Path(__file__).with_name("165_apply_a52_active_display_scopes.py")
    spec = importlib.util.spec_from_file_location("a52_scope165", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load active-scope helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scope_name(function: str) -> str:
    return f"a52.life.{function}"


def patch_profile(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="strict")
    marker = f"profile={PROFILE}"
    if marker in text:
        return "already-present"

    old_marker = f"profile={OLD_PROFILE}"
    count = text.count(old_marker)
    if count != 1:
        raise SystemExit(
            f"recorder profile anchor mismatch: expected one {old_marker!r}, found {count}"
        )
    path.write_text(text.replace(old_marker, marker, 1), encoding="utf-8")
    return "replaced"


def inject(helper, text: str, function: str) -> tuple[str, str]:
    statement = f'A52_ACKFR_SCOPE("DISP", "{scope_name(function)}");'
    if statement in text:
        return text, "already-present"
    openings = helper.definition_openings(text, function)
    if len(openings) != 1:
        raise SystemExit(
            f"display lifecycle definition count mismatch: {function}: {len(openings)}"
        )
    opening = openings[0]
    return text[: opening + 1] + "\n\t" + statement + text[opening + 1 :], "inserted"


def run(gki: Path, output: Path) -> dict[str, object]:
    helper = load_scope_helper()
    recorder = gki / RECORDER_REL
    if not recorder.is_file():
        raise SystemExit(f"recorder source missing: {recorder}")
    profile_state = patch_profile(recorder)

    results: list[dict[str, object]] = []
    inserted = 0
    for rel, functions in TARGETS.items():
        path = gki / rel
        if not path.is_file():
            raise SystemExit(f"compiled A52 display source missing: {path}")
        text, include_changed = helper.add_include(helper.read(path))
        states: list[dict[str, str]] = []
        for function in functions:
            text, state = inject(helper, text, function)
            inserted += int(state == "inserted")
            states.append({"function": function, "scope": scope_name(function), "state": state})
        helper.write(path, text)
        final = helper.read(path)
        if helper.INCLUDE not in final:
            raise SystemExit(f"recorder include missing: {path}")
        for function in functions:
            statement = f'A52_ACKFR_SCOPE("DISP", "{scope_name(function)}");'
            if final.count(statement) != 1:
                raise SystemExit(f"scope audit failed: {path}:{function}")
        results.append({"path": str(rel), "include_changed": include_changed, "functions": states})

    recorder_text = recorder.read_text(encoding="utf-8", errors="strict")
    if recorder_text.count(f"profile={PROFILE}") != 1:
        raise SystemExit("combined recorder profile audit failed")
    if f"profile={OLD_PROFILE}" in recorder_text:
        raise SystemExit("old recorder profile remains")

    total = sum(len(functions) for functions in TARGETS.values())
    report = {
        "status": "a52-heap19-bufops-display-lifecycle-v1-staged",
        "hardware_validated": False,
        "functional_change": "instrumentation-only-on-proven-heap19-bufops-source",
        "persistent_profile": PROFILE,
        "previous_profile": OLD_PROFILE,
        "profile_state": profile_state,
        "active_tree": "drivers/a52_display",
        "scope_domain": "DISP",
        "scope_prefix": "a52.life.",
        "target_file_count": len(TARGETS),
        "target_function_count": total,
        "inserted_function_count": inserted,
        "payload_capture": False,
        "secure_memory_changes": False,
        "display_control_flow_changes": False,
        "evidence_basis": {
            "capture_sha256": CAPTURE_SHA256,
            "raw_snapshot_bytes": 1048576,
            "screen_result": "black",
            "candidate_profile": OLD_PROFILE,
            "heap19_get_flags_return": 0,
            "qseecom_create_bridge_for_secbuf_return": 0,
            "qseecom_dmabuf_map_return": 0,
            "qseecom_vaddr_map_return": 0,
            "qseecom_dmabuf_cache_operations_return": 0,
            "qseecom_load_app_region_return": 0,
            "finding": (
                "The heap-19 and QSEECOM path now succeeds. Preserve it unchanged and "
                "instrument the earlier DRM, DSI, KMS, panel-acquisition and commit lifecycle."
            ),
        },
        "files": results,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-combined-lifecycle-") as tmp:
        root = Path(tmp)
        recorder = root / RECORDER_REL
        recorder.parent.mkdir(parents=True, exist_ok=True)
        recorder.write_text(
            f'const char *s = "policy=critical-after-capacity profile={OLD_PROFILE} commit=%08x\\n";\n',
            encoding="utf-8",
        )
        for rel, functions in TARGETS.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            body = ["#include <linux/kernel.h>\n\n"]
            for index, function in enumerate(functions):
                qualifier = "static " if index % 2 else ""
                body.append(
                    f"{qualifier}int {function}(void *arg);\n"
                    f"{qualifier}int {function}(void *arg)\n"
                    "{\n#if defined(TEST_A)\n\tif (arg) return 1;\n"
                    "#else\n\tif (!arg) return 0;\n#endif\n\treturn arg != 0;\n}\n\n"
                )
            path.write_text("".join(body), encoding="utf-8")
        first = run(root, root / "report")
        expected = sum(len(v) for v in TARGETS.values())
        if first["inserted_function_count"] != expected or first["profile_state"] != "replaced":
            raise SystemExit("first-pass self-test failed")
        second = run(root, root / "report2")
        if second["inserted_function_count"] != 0 or second["profile_state"] != "already-present":
            raise SystemExit("idempotence self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
    print(json.dumps(run(args.gki.resolve(), args.output.resolve()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
