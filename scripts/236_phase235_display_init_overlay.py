#!/usr/bin/env python3
"""Phase 236: trace MSM DRM registration/probe before component-master assembly.

This overlay runs immediately after the Phase 235 recorder overlay inside the
cumulative Phase 230 hook. It keeps the Phase 210 R48/RS48/CRC32C transport and
all Phase 235 RSCC/component instrumentation, while admitting a new DISPINIT
class plus the already-existing bounded BOOT phase milestones.
"""
from __future__ import annotations

import sys
from pathlib import Path

PHASE235_MARKER = "A52_PHASE235_RSCC_MASTER_RECORDER_V1"
PHASE236_MARKER = "A52_PHASE236_DISPLAY_INIT_RECORDER_V1"
PHASE235_BOOT = "BOOT rs=ready phase=235 focus=rscc-master roots=%u copies=3 crc=crc32c"
PHASE236_BOOT = "BOOT rs=ready phase=236 focus=display-init roots=%u copies=3 crc=crc32c"

RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MSM_DRV_REL = Path("drivers/a52_display/msm/msm_drv.c")

PHASE235_FILTER = '''\tif (strncmp(fmt, "RSCC", 4) &&
\t    strncmp(fmt, "DRMCOMP", 7) &&
\t    strncmp(fmt, "COMP ", 5) &&
\t    strncmp(fmt, "BOOT ctl=", 9) &&
\t    strncmp(fmt, "BOOT rs=ready", 13))
\t\treturn;'''

PHASE236_FILTER = '''\tif (strncmp(fmt, "DISPINIT", 8) &&
\t    strncmp(fmt, "RSCC", 4) &&
\t    strncmp(fmt, "DRMCOMP", 7) &&
\t    strncmp(fmt, "COMP ", 5) &&
\t    strncmp(fmt, "BOOT ctl=", 9) &&
\t    strncmp(fmt, "BOOT rs=ready", 13) &&
\t    strncmp(fmt, "BOOT phase=", 11))
\t\treturn;'''

REGISTER_OLD = '''static int __init msm_drm_register(void)
{
\tif (!modeset)
\t\treturn -EINVAL;

\tDBG("init");
\tmsm_smmu_driver_init();
\tmsm_dsi_register();
\tmsm_edp_register();
\tmsm_hdmi_register();
\treturn platform_driver_register(&msm_platform_driver);
}
'''

REGISTER_NEW = '''static int __init msm_drm_register(void)
{
\tint ret;

\ta52_ackfr_record("DISPINIT register enter modeset=%u",
\t\t\t (unsigned int)modeset);
\tif (!modeset) {
\t\ta52_ackfr_record("DISPINIT register disabled rc=%d", -EINVAL);
\t\treturn -EINVAL;
\t}

\tDBG("init");
\tmsm_smmu_driver_init();
\ta52_ackfr_record("DISPINIT smmu-register done");
\tmsm_dsi_register();
\ta52_ackfr_record("DISPINIT dsi-register done");
\tmsm_edp_register();
\ta52_ackfr_record("DISPINIT edp-register done");
\tmsm_hdmi_register();
\ta52_ackfr_record("DISPINIT hdmi-register done");
\ta52_ackfr_record("DISPINIT platform-register enter");
\tret = platform_driver_register(&msm_platform_driver);
\ta52_ackfr_record("DISPINIT platform-register exit rc=%d", ret);
\treturn ret;
}
'''

PROBE_OLD = '''static int msm_pdev_probe(struct platform_device *pdev)
{
\tint ret;
\tstruct component_match *match = NULL;

\tret = add_display_components(&pdev->dev, &match);
'''

PROBE_NEW = '''static int msm_pdev_probe(struct platform_device *pdev)
{
\tint ret;
\tstruct component_match *match = NULL;

\ta52_ackfr_record("DISPINIT probe enter dev=%s sde=%u mdss=%u",
\t\t\t dev_name(&pdev->dev),
\t\t\t pdev->dev.of_node && of_device_is_compatible(
\t\t\t\t pdev->dev.of_node, "qcom,sde-kms"),
\t\t\t pdev->dev.of_node && of_device_is_compatible(
\t\t\t\t pdev->dev.of_node, "qcom,mdss"));
\tret = add_display_components(&pdev->dev, &match);
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def candidate_roots(arguments: list[str]) -> list[Path]:
    roots: list[Path] = []
    for value in arguments:
        if value.startswith("-"):
            continue
        path = Path(value)
        roots.extend((path, path.parent))
    roots.extend((Path("workspace/gki-phase199-src"), Path("gki/common")))
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def locate_root(arguments: list[str]) -> Path:
    matches: list[Path] = []
    for root in candidate_roots(arguments):
        recorder = root / RECORDER_REL
        msm_drv = root / MSM_DRV_REL
        if not recorder.is_file() or not msm_drv.is_file():
            continue
        recorder_text = recorder.read_text(encoding="utf-8")
        if PHASE235_MARKER not in recorder_text and PHASE236_MARKER not in recorder_text:
            continue
        matches.append(root)
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(str(path) for path in unique) or "none"
        raise RuntimeError(f"expected one generated Phase 235 kernel root, found {len(unique)}: {rendered}")
    return unique[0]


def patch_recorder(text: str, label: str) -> str:
    if PHASE236_MARKER in text:
        validate_recorder(text, label)
        return text
    if PHASE235_MARKER not in text:
        raise RuntimeError(f"{label}: Phase 235 recorder marker missing")
    text = replace_once(
        text,
        PHASE235_MARKER,
        PHASE235_MARKER + "\n\t * " + PHASE236_MARKER,
        f"{label}: Phase 236 marker",
    )
    text = replace_once(
        text,
        PHASE235_FILTER,
        PHASE236_FILTER,
        f"{label}: Phase 236 event filter",
    )
    text = replace_once(
        text,
        PHASE235_BOOT,
        PHASE236_BOOT,
        f"{label}: Phase 236 boot identity",
    )
    validate_recorder(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        PHASE235_MARKER,
        PHASE236_MARKER,
        PHASE236_FILTER,
        PHASE236_BOOT,
        'strncmp(fmt, "DISPINIT", 8)',
        'strncmp(fmt, "BOOT phase=", 11)',
        'strncmp(fmt, "RSCC", 4)',
        'strncmp(fmt, "DRMCOMP", 7)',
        'strncmp(fmt, "COMP ", 5)',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing Phase 236 recorder token: {token}")
    if PHASE235_BOOT in text:
        raise RuntimeError(f"{label}: stale Phase 235 runtime identity remains")


def patch_msm_drv(text: str, label: str) -> str:
    if "A52_PHASE236_DISPLAY_INIT_TRACE_V1" in text:
        validate_msm_drv(text, label)
        return text
    if "DRMCOMP collect enter" not in text or "DRMCOMP master-add enter" not in text:
        raise RuntimeError(f"{label}: inherited Phase 191 DRMCOMP instrumentation missing")
    if "#include <linux/a52_ack_secure_flight_recorder.h>" not in text:
        raise RuntimeError(f"{label}: recorder API include missing")

    text = replace_once(
        text,
        REGISTER_OLD,
        "/* A52_PHASE236_DISPLAY_INIT_TRACE_V1 */\n" + REGISTER_NEW,
        f"{label}: msm_drm_register trace",
    )
    text = replace_once(
        text,
        PROBE_OLD,
        PROBE_NEW,
        f"{label}: msm_pdev_probe entry trace",
    )
    validate_msm_drv(text, label)
    return text


def validate_msm_drv(text: str, label: str) -> None:
    for token in (
        "A52_PHASE236_DISPLAY_INIT_TRACE_V1",
        "DISPINIT register enter modeset=%u",
        "DISPINIT smmu-register done",
        "DISPINIT dsi-register done",
        "DISPINIT edp-register done",
        "DISPINIT hdmi-register done",
        "DISPINIT platform-register enter",
        "DISPINIT platform-register exit rc=%d",
        "DISPINIT probe enter dev=%s sde=%u mdss=%u",
        "DRMCOMP collect enter",
        "DRMCOMP probe collect",
        "DRMCOMP master-add enter",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing display-init trace token: {token}")


def apply(arguments: list[str]) -> Path:
    root = locate_root(arguments)
    recorder_path = root / RECORDER_REL
    msm_drv_path = root / MSM_DRV_REL

    recorder_text = patch_recorder(recorder_path.read_text(encoding="utf-8"), str(recorder_path))
    recorder_path.write_text(recorder_text, encoding="utf-8")

    msm_text = patch_msm_drv(msm_drv_path.read_text(encoding="utf-8"), str(msm_drv_path))
    msm_drv_path.write_text(msm_text, encoding="utf-8")

    print(
        "Phase 236 display-init recorder applied: transport unchanged; admitted "
        "DISPINIT, BOOT phase, RSCC, DRMCOMP, bounded COMP, and BOOT control",
        flush=True,
    )
    return root


def self_test() -> None:
    recorder_fixture = f'''void record(const char *fmt)\n{{\n\t/* {PHASE235_MARKER}\n\t * inherited\n\t */\n{PHASE235_FILTER}\n}}\nconst char *id = "{PHASE235_BOOT}";\n'''
    patched_recorder = patch_recorder(recorder_fixture, "phase236-recorder-fixture")
    if patch_recorder(patched_recorder, "phase236-recorder-idempotence") != patched_recorder:
        raise AssertionError("Phase 236 recorder patch is not idempotent")

    msm_fixture = '''#include <linux/a52_ack_secure_flight_recorder.h>\n/* DRMCOMP collect enter */\n/* DRMCOMP probe collect */\n/* DRMCOMP master-add enter */\n''' + REGISTER_OLD + "\n" + PROBE_OLD
    patched_msm = patch_msm_drv(msm_fixture, "phase236-msm-fixture")
    if patch_msm_drv(patched_msm, "phase236-msm-idempotence") != patched_msm:
        raise AssertionError("Phase 236 msm_drv patch is not idempotent")
    print("Phase 236 display-init overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
