#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

REPORT_NAME = "phase33-a52-display-bindcore-report.json"
RECORDER_REL = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MAKEFILE_REL = Path("drivers/a52_secure/Makefile")
AUDIT_REL = Path("drivers/a52_secure/a52_display_bind_audit.c")
OLD_PROFILE = "heap19-bufops-display-lifecycle-v1"
PROFILE = "heap19-bufops-display-bindcore-v1"
CAPTURE_SHA256 = "ea858f328fd30d2ccb3a06e6bff0a52346e3df8e87672d935c96798d2fc613d1"

MSM_REL = Path("drivers/a52_display/msm/msm_drv.c")
DSI_DISPLAY_REL = Path("drivers/a52_display/msm/dsi/dsi_display.c")
DSI_PHY_REL = Path("drivers/a52_display/msm/dsi/dsi_phy.c")
DSI_CTRL_REL = Path("drivers/a52_display/msm/dsi/dsi_ctrl.c")

MSM_OLD = '''static int __init msm_drm_register(void)
{
\tA52_ACKFR_SCOPE("DISP", "a52.life.msm_drm_register");
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
MSM_NEW = '''static int __init msm_drm_register(void)
{
\tint rc;

\tA52_ACKFR_SCOPE("DISP", "a52.life.msm_drm_register");
\tif (!modeset) {
\t\ta52_ackfr_record("DISP bind reg=msm_drm rc=%d", -EINVAL);
\t\treturn -EINVAL;
\t}

\tDBG("init");
\tmsm_smmu_driver_init();
\tmsm_dsi_register();
\tmsm_edp_register();
\tmsm_hdmi_register();
\trc = platform_driver_register(&msm_platform_driver);
\ta52_ackfr_record("DISP bind reg=msm_drm rc=%d", rc);
\treturn rc;
}
'''

DSI_DISPLAY_OLD = '''static int __init dsi_display_register(void)
{
\tA52_ACKFR_SCOPE("DISP", "a52.life.dsi_display_register");
\tdsi_phy_drv_register();
\tdsi_ctrl_drv_register();

\tdsi_display_parse_boot_display_selection();

\treturn platform_driver_register(&dsi_display_driver);
}
'''
DSI_DISPLAY_NEW = '''static int __init dsi_display_register(void)
{
\tint rc;

\tA52_ACKFR_SCOPE("DISP", "a52.life.dsi_display_register");
\tdsi_phy_drv_register();
\tdsi_ctrl_drv_register();

\tdsi_display_parse_boot_display_selection();

\trc = platform_driver_register(&dsi_display_driver);
\ta52_ackfr_record("DISP bind reg=dsi_display rc=%d", rc);
\treturn rc;
}
'''

DSI_PHY_OLD = '''void dsi_phy_drv_register(void)
{
\tplatform_driver_register(&dsi_phy_platform_driver);
}
'''
DSI_PHY_NEW = '''void dsi_phy_drv_register(void)
{
\tint rc;

\trc = platform_driver_register(&dsi_phy_platform_driver);
\ta52_ackfr_record("DISP bind reg=dsi_phy rc=%d", rc);
}
'''

DSI_CTRL_OLD = '''void dsi_ctrl_drv_register(void)
{
\tplatform_driver_register(&dsi_ctrl_driver);
}
'''
DSI_CTRL_NEW = '''void dsi_ctrl_drv_register(void)
{
\tint rc;

\trc = platform_driver_register(&dsi_ctrl_driver);
\ta52_ackfr_record("DISP bind reg=dsi_ctrl rc=%d", rc);
}
'''

AUDIT_SOURCE = r'''// SPDX-License-Identifier: GPL-2.0-only
/*
 * Metadata-only runtime audit for the A52 display platform-device binding
 * boundary. It never creates, binds, unbinds, reprobes, or mutates a device.
 */
#include <linux/atomic.h>
#include <linux/device.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/jiffies.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/workqueue.h>

#include <linux/a52_ack_secure_flight_recorder.h>

struct a52_bind_target {
\tconst char *tag;
\tconst char *compatible;
};

static const struct a52_bind_target a52_bind_targets[] = {
\t{ "sde", "qcom,sde-kms" },
\t{ "dsi", "qcom,dsi-display" },
\t{ "ctrl", "qcom,dsi-ctrl-hw-v2.4" },
\t{ "phy", "qcom,dsi-phy-v3.0" },
};

static const char *a52_bound_driver(const struct platform_device *pdev)
{
\tif (!pdev || !pdev->dev.driver || !pdev->dev.driver->name)
\t\treturn "-";
\treturn pdev->dev.driver->name;
}

static void a52_audit_compatible(const struct a52_bind_target *target,
\t\t\t\t unsigned int pass)
{
\tstruct device_node *np = NULL;
\tunsigned int index = 0;

\tfor_each_compatible_node(np, NULL, target->compatible) {
\t\tstruct platform_device *pdev;
\t\tbool available;

\t\tavailable = of_device_is_available(np);
\t\tpdev = of_find_device_by_node(np);
\t\ta52_ackfr_record(
\t\t\t"DISP bind p=%u c=%s n=%u av=%u pdev=%u drv=%s",
\t\t\tpass, target->tag, index, available, !!pdev,
\t\t\ta52_bound_driver(pdev));
\t\tif (pdev)
\t\t\tput_device(&pdev->dev);
\t\tindex++;
\t}

\tif (!index)
\t\ta52_ackfr_record("DISP bind p=%u c=%s nodes=0", pass,
\t\t\t\t  target->tag);
}

static void a52_audit_driver(const char *name, unsigned int pass)
{
\tstruct device_driver *drv;

\tdrv = driver_find(name, &platform_bus_type);
\ta52_ackfr_record("DISP bind p=%u driver=%s reg=%u", pass, name,
\t\t\t  !!drv);
\tif (drv)
\t\tput_driver(drv);
}

static void a52_display_bind_audit(unsigned int pass)
{
\tunsigned int i;

\ta52_audit_driver("msm_drm", pass);
\ta52_audit_driver("msm-dsi-display", pass);
\tfor (i = 0; i < ARRAY_SIZE(a52_bind_targets); i++)
\t\ta52_audit_compatible(&a52_bind_targets[i], pass);
}

static atomic_t a52_bind_pass = ATOMIC_INIT(0);
static void a52_display_bind_workfn(struct work_struct *work);
static DECLARE_DELAYED_WORK(a52_display_bind_work, a52_display_bind_workfn);

static void a52_display_bind_workfn(struct work_struct *work)
{
\tunsigned int pass;

\tpass = (unsigned int)atomic_inc_return(&a52_bind_pass);
\ta52_display_bind_audit(pass);
\tif (pass < 4)
\t\tschedule_delayed_work(&a52_display_bind_work,
\t\t\t\t      msecs_to_jiffies(pass == 1 ? 2000 :
\t\t\t\t\t\t\tpass == 2 ? 8000 : 20000));
}

static int __init a52_display_bind_audit_init(void)
{
\ta52_ackfr_record("DISP bind audit=start");
\ta52_display_bind_audit(0);
\tschedule_delayed_work(&a52_display_bind_work, msecs_to_jiffies(500));
\treturn 0;
}
late_initcall(a52_display_bind_audit_init);
'''

INCLUDE = "#include <linux/a52_ack_secure_flight_recorder.h>"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def add_include(text: str) -> tuple[str, bool]:
    if INCLUDE in text:
        return text, False
    offset = 0
    seen_include = False
    insert_at = -1
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if line.startswith("#include"):
            seen_include = True
            insert_at = offset + len(line)
        elif seen_include and stripped and not stripped.startswith(("/*", "*", "//")):
            break
        offset += len(line)
    if insert_at < 0:
        raise SystemExit("initial include anchor not found")
    return text[:insert_at] + INCLUDE + "\n" + text[insert_at:], True


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if new in text:
        return text, False
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor count={count}, expected 1")
    return text.replace(old, new, 1), True


def patch_profile(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="strict")
    if f"profile={PROFILE}" in text:
        return "already-present"
    count = text.count(f"profile={OLD_PROFILE}")
    if count != 1:
        raise SystemExit(f"profile anchor count={count}, expected 1")
    path.write_text(
        text.replace(f"profile={OLD_PROFILE}", f"profile={PROFILE}", 1),
        encoding="utf-8",
    )
    return "replaced"


def patch_source(path: Path, old: str, new: str, label: str) -> bool:
    if not path.is_file():
        raise SystemExit(f"source missing: {path}")
    text, include_changed = add_include(read(path))
    text, body_changed = replace_once(text, old, new, label)
    write(path, text)
    return include_changed or body_changed


def run(gki: Path, output: Path) -> dict[str, object]:
    recorder = gki / RECORDER_REL
    makefile = gki / MAKEFILE_REL
    if not recorder.is_file() or not makefile.is_file():
        raise SystemExit("A52 recorder tree missing")

    profile_state = patch_profile(recorder)
    changed = {
        "msm_register": patch_source(gki / MSM_REL, MSM_OLD, MSM_NEW, "msm register"),
        "dsi_display_register": patch_source(
            gki / DSI_DISPLAY_REL, DSI_DISPLAY_OLD, DSI_DISPLAY_NEW,
            "dsi display register"
        ),
        "dsi_phy_register": patch_source(
            gki / DSI_PHY_REL, DSI_PHY_OLD, DSI_PHY_NEW,
            "dsi phy register"
        ),
        "dsi_ctrl_register": patch_source(
            gki / DSI_CTRL_REL, DSI_CTRL_OLD, DSI_CTRL_NEW,
            "dsi ctrl register"
        ),
    }

    audit = gki / AUDIT_REL
    if audit.exists():
        if audit.read_text(encoding="utf-8", errors="strict") != AUDIT_SOURCE:
            raise SystemExit(f"unexpected existing audit source: {audit}")
        audit_state = "already-present"
    else:
        audit.parent.mkdir(parents=True, exist_ok=True)
        audit.write_text(AUDIT_SOURCE, encoding="utf-8")
        audit_state = "created"

    make_text = makefile.read_text(encoding="utf-8", errors="strict")
    make_marker = "# A52_DISPLAY_BIND_AUDIT_V1\nobj-y += a52_display_bind_audit.o\n"
    if make_marker in make_text:
        make_state = "already-present"
    else:
        if not make_text.endswith("\n"):
            make_text += "\n"
        makefile.write_text(make_text + make_marker, encoding="utf-8")
        make_state = "appended"

    required = {
        gki / MSM_REL: [
            'a52_ackfr_record("DISP bind reg=msm_drm rc=%d", rc);',
            'A52_ACKFR_SCOPE("DISP", "a52.life.msm_drm_register");',
        ],
        gki / DSI_DISPLAY_REL: [
            'a52_ackfr_record("DISP bind reg=dsi_display rc=%d", rc);',
            'A52_ACKFR_SCOPE("DISP", "a52.life.dsi_display_register");',
        ],
        gki / DSI_PHY_REL: ['a52_ackfr_record("DISP bind reg=dsi_phy rc=%d", rc);'],
        gki / DSI_CTRL_REL: ['a52_ackfr_record("DISP bind reg=dsi_ctrl rc=%d", rc);'],
        audit: [
            'late_initcall(a52_display_bind_audit_init);',
            'of_find_device_by_node(np)',
            'driver_find(name, &platform_bus_type)',
            '"DISP bind p=%u c=%s n=%u av=%u pdev=%u drv=%s"',
        ],
    }
    for path, markers in required.items():
        text = path.read_text(encoding="utf-8", errors="strict")
        for marker in markers:
            if text.count(marker) != 1:
                raise SystemExit(f"audit marker count failed: {path}:{marker}")

    rec_text = recorder.read_text(encoding="utf-8", errors="strict")
    if rec_text.count(f"profile={PROFILE}") != 1 or f"profile={OLD_PROFILE}" in rec_text:
        raise SystemExit("profile audit failed")
    if makefile.read_text(encoding="utf-8").count("a52_display_bind_audit.o") != 1:
        raise SystemExit("Makefile audit failed")

    report = {
        "status": "a52-display-bindcore-v1-staged",
        "hardware_validated": False,
        "functional_change": "instrumentation-only",
        "persistent_profile": PROFILE,
        "previous_profile": OLD_PROFILE,
        "profile_state": profile_state,
        "capture_sha256": CAPTURE_SHA256,
        "observed_capture": {
            "screen_result": "black",
            "kernel_alive_seconds_at_least": 55,
            "qseecom_path_success": True,
            "display_registration_scopes_seen": [
                "dsi_display_register",
                "msm_drm_register",
            ],
            "display_probe_or_bind_scopes_seen": [],
            "finding": (
                "Display platform drivers register, but the persistent lifecycle "
                "recorder observes no expected platform probe, component bind, KMS "
                "initialization, or panel initialization stage."
            ),
        },
        "instrumentation": {
            "registration_return_codes": [
                "dsi_phy", "dsi_ctrl", "dsi_display", "msm_drm"
            ],
            "driver_registration_audit": ["msm_drm", "msm-dsi-display"],
            "compatible_node_audit": [
                "qcom,sde-kms", "qcom,dsi-display",
                "qcom,dsi-ctrl-hw-v2.4", "qcom,dsi-phy-v3.0",
            ],
            "audit_passes": 5,
            "mutates_device_state": False,
            "creates_platform_devices": False,
            "forces_reprobe": False,
        },
        "unchanged": {
            "heap19_get_flags": True,
            "qseecom_control_flow": True,
            "dma_buf_mapping": True,
            "display_probe_control_flow": True,
            "panel_commands": True,
            "device_tree": True,
            "ramdisk": True,
            "recovery_dtbo": True,
        },
        "changed": changed,
        "audit_source_state": audit_state,
        "makefile_state": make_state,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / REPORT_NAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="a52-bindcore-") as tmp:
        root = Path(tmp)
        rec = root / RECORDER_REL
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(
            f'const char *s = "policy=critical-after-capacity profile={OLD_PROFILE} commit=%08x\\n";\n',
            encoding="utf-8",
        )
        mf = root / MAKEFILE_REL
        mf.write_text("obj-y += a52_ack_secure_flight_recorder.o\n", encoding="utf-8")

        fixtures = {
            MSM_REL: "#include <linux/kernel.h>\n\n" + MSM_OLD,
            DSI_DISPLAY_REL: "#include <linux/kernel.h>\n\n" + DSI_DISPLAY_OLD,
            DSI_PHY_REL: "#include <linux/kernel.h>\n\n" + DSI_PHY_OLD,
            DSI_CTRL_REL: "#include <linux/kernel.h>\n\n" + DSI_CTRL_OLD,
        }
        for rel, text in fixtures.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        first = run(root, root / "out1")
        if first["profile_state"] != "replaced":
            raise SystemExit("first-pass profile self-test failed")
        if first["audit_source_state"] != "created":
            raise SystemExit("first-pass audit source self-test failed")
        second = run(root, root / "out2")
        if second["profile_state"] != "already-present":
            raise SystemExit("idempotence profile self-test failed")
        if any(second["changed"].values()):
            raise SystemExit("idempotence source self-test failed")
        if second["audit_source_state"] != "already-present":
            raise SystemExit("idempotence audit source self-test failed")


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
