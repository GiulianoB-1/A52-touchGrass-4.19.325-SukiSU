#!/usr/bin/env python3
"""Phase 256: restore KGSL userspace-node prerequisites and late-framework visibility.

Hardware Phase255 proves that KGSL core/probe/device_create all succeed while
SurfaceFlinger receives -ENOENT for /dev/kgsl-3d0.  The golden TouchGrass boot
uses tmpfs xattrs/POSIX ACLs for Android /dev labeling and the downstream KGSL
Kconfig contract selects the Adreno TZ + GPUBW devfreq governors.  Phase256:

* restores CONFIG_TMPFS_XATTR and CONFIG_TMPFS_POSIX_ACL;
* restores the QCOM_KGSL/QCOM_KGSL_IOMMU Kconfig-controlled build contract;
* imports the pinned TouchGrass Adreno TZ + GPUBW devfreq governors when the
  generated 5.10 tree does not already contain them;
* records the kgsl-3d0 device-add/uevent result;
* records zygote/system_server/SystemUI/launcher/bootanimation task renames and
  exits with a retained F256 prefix.

It does not create /dev/kgsl-3d0 manually, enable devtmpfs, weaken SELinux,
change DT, alter ueventd/ramdisk, or force any probe/uevent return value.
"""
from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TOUCHGRASS_COMMIT = "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
RAW_BASE = (
    "https://raw.githubusercontent.com/micr0softstore/"
    f"samsung_android_kernel_a52xq/{TOUCHGRASS_COMMIT}/"
)
MARKER = "A52_PHASE256_KGSL_DEVNODE_FRAMEWORK_V1"
CONFIG_DELTA = frozenset((
    "CONFIG_TMPFS_POSIX_ACL",
    "CONFIG_TMPFS_XATTR",
    "CONFIG_QCOM_KGSL",
    "CONFIG_QCOM_KGSL_IOMMU",
    "CONFIG_DEVFREQ_GOV_QCOM_ADRENO_TZ",
    "CONFIG_DEVFREQ_GOV_QCOM_GPUBW_MON",
    "CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR",
))
DEVFREQ_FILES = (
    "drivers/devfreq/governor_msm_adreno_tz.c",
    "drivers/devfreq/governor_bw_vbif.c",
    "drivers/devfreq/governor_gpubw_mon.c",
    "include/linux/msm_adreno_devfreq.h",
)

DEVFREQ_KCONFIG = f'''\n# {MARKER}\nconfig DEVFREQ_GOV_QCOM_ADRENO_TZ\n\ttristate "Qualcomm Technologies Inc Adreno Trustzone"\n\tdepends on QCOM_KGSL\n\thelp\n\t  TouchGrass-compatible TrustZone governor for Adreno GPU devfreq.\n\nconfig DEVFREQ_GOV_QCOM_GPUBW_MON\n\ttristate "GPU BW voting governor"\n\tdepends on DEVFREQ_GOV_QCOM_ADRENO_TZ\n\thelp\n\t  TouchGrass-compatible GPU bandwidth voting governor.\n'''


def fetch(relative: str) -> bytes:
    url = RAW_BASE + relative
    last: Exception | None = None
    for attempt in range(4):
        request = urllib.request.Request(
            url, headers={"User-Agent": "A52-Phase256-pinned-port"}
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read()
            if not data or b"404: Not Found" in data[:64]:
                raise RuntimeError(f"empty/not-found golden file: {relative}")
            return data
        except (OSError, urllib.error.URLError, RuntimeError) as exc:
            last = exc
            if attempt != 3:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch pinned TouchGrass file {relative}: {last}")


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    result: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def locate(args: list[str]) -> Path:
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, Path.cwd()):
        recorder = root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
        kgsl = root / "drivers/gpu/msm/kgsl.c"
        exec_c = root / "fs/exec.c"
        core = root / "drivers/base/core.c"
        if not all(path.is_file() for path in (recorder, kgsl, exec_c, core)):
            continue
        rec = recorder.read_text(encoding="utf-8")
        if "A52_PHASE255_POSTBOOT_VISIBILITY_V1" not in rec:
            continue
        if "GFXPOST 225 ks1" not in kgsl.read_text(encoding="utf-8"):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(
            "expected one generated Phase255 source root, found "
            + (", ".join(map(str, hits)) or "none")
        )
    return hits[0]


def locate_config(root: Path) -> Path:
    candidates = (
        Path.cwd() / "workspace/gki-phase199-out/.config",
        root / ".config",
        Path.cwd() / "gki/common/.config",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("Phase256 authoritative GKI .config not found")


def parse_config(path: Path) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            symbol, value = line.split("=", 1)
            states[symbol] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            states[line[2:-11]] = "n"
    return states


def set_config(config: Path, symbol: str, value: str) -> None:
    text = config.read_text(encoding="utf-8")
    lines = text.splitlines()
    prefixes = (f"{symbol}=", f"# {symbol} is not set")
    matches = [i for i, line in enumerate(lines) if line.startswith(prefixes)]
    if len(matches) > 1:
        raise RuntimeError(f"{config}: duplicate state for {symbol}")
    rendered = f"{symbol}={value}"
    if matches:
        lines[matches[0]] = rendered
    else:
        lines.append(rendered)
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def patch_gpu_kconfig(root: Path) -> None:
    msm = root / "drivers/gpu/msm/Kconfig"
    if not msm.is_file():
        msm.parent.mkdir(parents=True, exist_ok=True)
        msm.write_bytes(fetch("drivers/gpu/msm/Kconfig"))
    msm_text = msm.read_text(encoding="utf-8")
    for token in ("config QCOM_KGSL", "config QCOM_KGSL_IOMMU", "config QCOM_ADRENO_DEFAULT_GOVERNOR"):
        if token not in msm_text:
            raise RuntimeError(f"Phase256 KGSL Kconfig missing {token}")

    source = 'source "drivers/gpu/msm/Kconfig"'
    gpu_parent = root / "drivers/gpu/Kconfig"
    if gpu_parent.is_file():
        text = gpu_parent.read_text(encoding="utf-8")
        if source not in text:
            matches = list(re.finditer(r"(?m)^endmenu\s*$", text))
            if matches:
                pos = matches[-1].start()
                text = text[:pos] + f"\n# {MARKER}\n{source}\n\n" + text[pos:]
            else:
                text = text.rstrip() + f"\n\n# {MARKER}\n{source}\n"
            gpu_parent.write_text(text, encoding="utf-8")
        return

    drivers_parent = root / "drivers/Kconfig"
    if not drivers_parent.is_file():
        raise RuntimeError(
            "Phase256 missing Kconfig parent: neither drivers/gpu/Kconfig nor drivers/Kconfig exists"
        )

    text = drivers_parent.read_text(encoding="utf-8")
    if source in text:
        return

    anchors = (
        'source "drivers/gpu/drm/Kconfig"',
        'source "drivers/gpu/vga/Kconfig"',
    )
    for anchor in anchors:
        if anchor in text:
            text = text.replace(anchor, anchor + f"\n# {MARKER}\n" + source, 1)
            break
    else:
        matches = list(re.finditer(r"(?m)^endmenu\s*$", text))
        if matches:
            pos = matches[-1].start()
            text = text[:pos] + f"\n# {MARKER}\n{source}\n\n" + text[pos:]
        else:
            text = text.rstrip() + f"\n\n# {MARKER}\n{source}\n"
    drivers_parent.write_text(text, encoding="utf-8")


def patch_kgsl_makefile(root: Path) -> None:
    path = root / "drivers/gpu/msm/Makefile"
    text = path.read_text(encoding="utf-8")
    replacements = (
        ("msm_kgsl_core-y += kgsl_iommu.o", "msm_kgsl_core-$(CONFIG_QCOM_KGSL_IOMMU) += kgsl_iommu.o"),
        ("msm_adreno-y += adreno_iommu.o", "msm_adreno-$(CONFIG_QCOM_KGSL_IOMMU) += adreno_iommu.o"),
        ("obj-y += msm_kgsl_core.o", "obj-$(CONFIG_QCOM_KGSL) += msm_kgsl_core.o"),
        ("obj-y += msm_adreno.o", "obj-$(CONFIG_QCOM_KGSL) += msm_adreno.o"),
    )
    changed = False
    for forced, guarded in replacements:
        if guarded in text:
            continue
        count = text.count(forced)
        if count != 1:
            raise RuntimeError(
                f"{path}: expected one forced KGSL Kbuild line {forced!r}, found {count}"
            )
        text = text.replace(forced, guarded, 1)
        changed = True
    if MARKER not in text:
        text = f"# {MARKER}\n" + text
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")


def patch_devfreq(root: Path) -> None:
    for relative in DEVFREQ_FILES:
        target = root / relative
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fetch(relative))
            print(f"P256 staged {relative}", flush=True)

    kconfig = root / "drivers/devfreq/Kconfig"
    text = kconfig.read_text(encoding="utf-8")
    if "config DEVFREQ_GOV_QCOM_ADRENO_TZ" not in text:
        anchor = "endif # PM_DEVFREQ"
        if text.count(anchor) != 1:
            raise RuntimeError("drivers/devfreq/Kconfig PM_DEVFREQ end anchor drifted")
        text = text.replace(anchor, DEVFREQ_KCONFIG + "\n" + anchor, 1)
        kconfig.write_text(text, encoding="utf-8")
    final_kconfig = kconfig.read_text(encoding="utf-8")
    for token in ("config DEVFREQ_GOV_QCOM_ADRENO_TZ", "config DEVFREQ_GOV_QCOM_GPUBW_MON"):
        if token not in final_kconfig:
            raise RuntimeError(f"Phase256 devfreq Kconfig missing {token}")

    makefile = root / "drivers/devfreq/Makefile"
    text = makefile.read_text(encoding="utf-8")
    lines = (
        "obj-$(CONFIG_DEVFREQ_GOV_QCOM_ADRENO_TZ) += governor_msm_adreno_tz.o",
        "obj-$(CONFIG_DEVFREQ_GOV_QCOM_GPUBW_MON) += governor_bw_vbif.o",
        "obj-$(CONFIG_DEVFREQ_GOV_QCOM_GPUBW_MON) += governor_gpubw_mon.o",
    )
    missing = [line for line in lines if line not in text]
    if missing:
        text = text.rstrip() + f"\n# {MARKER}\n" + "\n".join(missing) + "\n"
        makefile.write_text(text, encoding="utf-8")


def patch_recorder(root: Path) -> None:
    path = root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c"
    text = path.read_text(encoding="utf-8")
    if MARKER in text and 'strncmp(fmt, "F256", 4)' in text and '!strncmp(message, "F256 ", 5)' in text:
        return

    fmt_anchor = 'if (strncmp(fmt, "K255VIS", 7) &&\n'
    if text.count(fmt_anchor) != 1:
        raise RuntimeError("Phase256 recorder format-admission anchor drifted")
    fmt_new = f'''/* {MARKER}\n * Retain the narrow kgsl-devnode/framework milestone stream after capacity.\n */\nif (strncmp(fmt, "F256", 4) &&\n    strncmp(fmt, "K255VIS", 7) &&\n'''
    text = text.replace(fmt_anchor, fmt_new, 1)

    critical_anchor = 'return !strncmp(message, "K255VIS ", 8) ||\n'
    if text.count(critical_anchor) != 1:
        raise RuntimeError("Phase256 recorder post-capacity anchor drifted")
    text = text.replace(
        critical_anchor,
        'return !strncmp(message, "F256 ", 5) ||\n       !strncmp(message, "K255VIS ", 8) ||\n',
        1,
    )
    path.write_text(text, encoding="utf-8")


def patch_device_core(root: Path) -> None:
    path = root / "drivers/base/core.c"
    text = path.read_text(encoding="utf-8")
    if "A52_PHASE256_KGSL_DEVNODE_UEVENT_V1" in text:
        return
    anchor = "\tkobject_uevent(&dev->kobj, KOBJ_ADD);\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: KOBJ_ADD device_add anchor drifted")
    replacement = '''\t/* A52_PHASE256_KGSL_DEVNODE_UEVENT_V1 */\n\tif (!strcmp(dev_name(dev), "kgsl-3d0")) {\n\t\tint a52_r256_uevent_rc;\n\n\t\ta52_ackfr_record("F256 da n=%.16s M=%u m=%u",\n\t\t\tdev_name(dev), MAJOR(dev->devt), MINOR(dev->devt));\n\t\ta52_r256_uevent_rc = kobject_uevent(&dev->kobj, KOBJ_ADD);\n\t\ta52_ackfr_record("F256 ue n=%.16s rc=%d",\n\t\t\tdev_name(dev), a52_r256_uevent_rc);\n\t} else {\n\t\tkobject_uevent(&dev->kobj, KOBJ_ADD);\n\t}\n'''
    text = text.replace(anchor, replacement, 1)
    path.write_text(text, encoding="utf-8")


def patch_task_rename(root: Path) -> None:
    path = root / "fs/exec.c"
    text = path.read_text(encoding="utf-8")
    if "A52_PHASE256_FRAMEWORK_TASK_RENAME_V1" in text:
        return
    anchor = '''void __set_task_comm(struct task_struct *tsk, const char *buf, bool exec)\n{\n\ttask_lock(tsk);\n\ttrace_task_rename(tsk, buf);\n\tstrlcpy(tsk->comm, buf, sizeof(tsk->comm));\n\ttask_unlock(tsk);\n\tperf_event_comm(tsk, exec);\n}\n'''
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: __set_task_comm anchor drifted")
    replacement = '''/* A52_PHASE256_FRAMEWORK_TASK_RENAME_V1 */\nstatic bool a52_r256_framework_name(const char *name)\n{\n\treturn name && (!strcmp(name, "zygote") || !strcmp(name, "zygote64") ||\n\t\t!strcmp(name, "system_server") ||\n\t\t!strncmp(name, "com.android.syste", 16) ||\n\t\t!strncmp(name, "com.sec.android.a", 16) ||\n\t\t!strcmp(name, "bootanimation") ||\n\t\t!strcmp(name, "surfaceflinger"));\n}\n\nvoid __set_task_comm(struct task_struct *tsk, const char *buf, bool exec)\n{\n\ttask_lock(tsk);\n\ttrace_task_rename(tsk, buf);\n\tstrlcpy(tsk->comm, buf, sizeof(tsk->comm));\n\ttask_unlock(tsk);\n\tperf_event_comm(tsk, exec);\n\tif (a52_r256_framework_name(buf))\n\t\ta52_ackfr_record("F256 rn p=%d e=%d n=%.16s",\n\t\t\ttask_pid_nr(tsk), exec ? 1 : 0, buf);\n}\n'''
    text = text.replace(anchor, replacement, 1)
    path.write_text(text, encoding="utf-8")


def patch_task_exit(root: Path) -> None:
    path = root / "kernel/exit.c"
    text = path.read_text(encoding="utf-8")
    if "A52_PHASE256_FRAMEWORK_TASK_EXIT_V1" in text:
        return
    anchor = '''void __noreturn do_exit(long code)\n{\n\tstruct task_struct *tsk = current;\n\tint group_dead;\n\n'''
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: do_exit anchor drifted")
    replacement = '''/* A52_PHASE256_FRAMEWORK_TASK_EXIT_V1 */\nstatic bool a52_r256_framework_exit_comm(const char *name)\n{\n\treturn name && (!strcmp(name, "zygote") || !strcmp(name, "zygote64") ||\n\t\t!strcmp(name, "system_server") ||\n\t\t!strncmp(name, "com.android.syste", 16) ||\n\t\t!strncmp(name, "com.sec.android.a", 16) ||\n\t\t!strcmp(name, "bootanimation") ||\n\t\t!strcmp(name, "surfaceflinger"));\n}\n\nvoid __noreturn do_exit(long code)\n{\n\tstruct task_struct *tsk = current;\n\tint group_dead;\n\n\tif (a52_r256_framework_exit_comm(current->comm))\n\t\ta52_ackfr_record("F256 ex p=%d n=%.16s c=%ld",\n\t\t\tcurrent->pid, current->comm, code);\n\n'''
    text = text.replace(anchor, replacement, 1)
    path.write_text(text, encoding="utf-8")


def repair_retention_snapshot(config: Path) -> None:
    snapshot = Path.cwd() / "artifacts/a52xq-graphics-startup-trace/config/before-phase217.config"
    if not snapshot.is_file():
        raise RuntimeError(f"Phase256 retention snapshot missing: {snapshot}")
    before = parse_config(snapshot)
    after = parse_config(config)
    changed = {
        symbol for symbol in set(before) | set(after)
        if before.get(symbol, "n") != after.get(symbol, "n")
    }
    if changed != CONFIG_DELTA:
        raise RuntimeError(
            "Phase256 retention repair refused config drift; expected exactly "
            f"{sorted(CONFIG_DELTA)}, got {sorted(changed)}"
        )
    for symbol in CONFIG_DELTA - {"CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR"}:
        if before.get(symbol, "n") != "n" or after.get(symbol) != "y":
            raise RuntimeError(
                f"Phase256 config state mismatch {symbol}: "
                f"before={before.get(symbol, 'n')} after={after.get(symbol, 'n')}"
            )
    if after.get("CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR") != '"msm-adreno-tz"':
        raise RuntimeError("Phase256 Adreno default governor string mismatch")
    snapshot.write_bytes(config.read_bytes())
    print("Phase 256 retention snapshot updated for exact seven-symbol delta", flush=True)


def configure(root: Path) -> Path:
    config = locate_config(root)
    for symbol in (
        "CONFIG_TMPFS_POSIX_ACL",
        "CONFIG_TMPFS_XATTR",
        "CONFIG_QCOM_KGSL",
        "CONFIG_QCOM_KGSL_IOMMU",
        "CONFIG_DEVFREQ_GOV_QCOM_ADRENO_TZ",
        "CONFIG_DEVFREQ_GOV_QCOM_GPUBW_MON",
    ):
        set_config(config, symbol, "y")
    set_config(config, "CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR", '"msm-adreno-tz"')
    return config


def audit(root: Path, config: Path) -> None:
    cfg = parse_config(config)
    expected = {
        "CONFIG_TMPFS_POSIX_ACL": "y",
        "CONFIG_TMPFS_XATTR": "y",
        "CONFIG_QCOM_KGSL": "y",
        "CONFIG_QCOM_KGSL_IOMMU": "y",
        "CONFIG_DEVFREQ_GOV_QCOM_ADRENO_TZ": "y",
        "CONFIG_DEVFREQ_GOV_QCOM_GPUBW_MON": "y",
        "CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR": '"msm-adreno-tz"',
    }
    for symbol, value in expected.items():
        if cfg.get(symbol) != value:
            raise RuntimeError(f"Phase256 final config mismatch {symbol}: {cfg.get(symbol)!r}")
    if cfg.get("CONFIG_DEVTMPFS", "n") != "n":
        raise RuntimeError("Phase256 must not enable CONFIG_DEVTMPFS")
    if cfg.get("CONFIG_UEVENT_HELPER", "n") != "n":
        raise RuntimeError("Phase256 must not enable CONFIG_UEVENT_HELPER")

    kgsl_mk = (root / "drivers/gpu/msm/Makefile").read_text(encoding="utf-8")
    for token in (
        "msm_kgsl_core-$(CONFIG_QCOM_KGSL_IOMMU) += kgsl_iommu.o",
        "msm_adreno-$(CONFIG_QCOM_KGSL_IOMMU) += adreno_iommu.o",
        "obj-$(CONFIG_QCOM_KGSL) += msm_kgsl_core.o",
        "obj-$(CONFIG_QCOM_KGSL) += msm_adreno.o",
    ):
        if token not in kgsl_mk:
            raise RuntimeError(f"Phase256 KGSL Kbuild audit missing {token}")

    devfreq_header = root / "include/linux/msm_adreno_devfreq.h"
    if not devfreq_header.is_file():
        raise RuntimeError("Phase256 missing staged include/linux/msm_adreno_devfreq.h")
    header_text = devfreq_header.read_text(encoding="utf-8")
    for token in ("struct devfreq_msm_adreno_tz_data", "struct msm_adreno_extended_profile", "struct msm_busmon_extended_profile"):
        if token not in header_text:
            raise RuntimeError(f"Phase256 Adreno devfreq header missing {token!r}")

    checks = {
        "drivers/a52_secure/a52_ack_secure_flight_recorder.c": (MARKER, 'strncmp(fmt, "F256", 4)', '!strncmp(message, "F256 ", 5)'),
        "drivers/base/core.c": ("A52_PHASE256_KGSL_DEVNODE_UEVENT_V1", "F256 da", "F256 ue"),
        "fs/exec.c": ("A52_PHASE256_FRAMEWORK_TASK_RENAME_V1", "F256 rn", "system_server"),
        "kernel/exit.c": ("A52_PHASE256_FRAMEWORK_TASK_EXIT_V1", "F256 ex", "bootanimation"),
    }
    for relative, tokens in checks.items():
        text = (root / relative).read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                raise RuntimeError(f"Phase256 source audit {relative} missing {token!r}")


def self_test() -> None:
    assert TOUCHGRASS_COMMIT == "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
    assert CONFIG_DELTA == frozenset((
        "CONFIG_TMPFS_POSIX_ACL",
        "CONFIG_TMPFS_XATTR",
        "CONFIG_QCOM_KGSL",
        "CONFIG_QCOM_KGSL_IOMMU",
        "CONFIG_DEVFREQ_GOV_QCOM_ADRENO_TZ",
        "CONFIG_DEVFREQ_GOV_QCOM_GPUBW_MON",
        "CONFIG_QCOM_ADRENO_DEFAULT_GOVERNOR",
    ))
    assert any(x.endswith("governor_msm_adreno_tz.c") for x in DEVFREQ_FILES)
    assert any(x.endswith("governor_bw_vbif.c") for x in DEVFREQ_FILES)
    assert any(x.endswith("governor_gpubw_mon.c") for x in DEVFREQ_FILES)
    assert any(x == "include/linux/msm_adreno_devfreq.h" for x in DEVFREQ_FILES)
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        "CONFIG_TMPFS_XATTR",
        "CONFIG_TMPFS_POSIX_ACL",
        "CONFIG_QCOM_KGSL_IOMMU",
        "msm-adreno-tz",
        "msm_adreno_devfreq.h",
        "F256 da",
        "F256 ue",
        "F256 rn",
        "F256 ex",
        "system_server",
        "com.android.syste",
        "com.sec.android.a",
        "bootanimation",
        "CONFIG_DEVTMPFS",
        "CONFIG_UEVENT_HELPER",
    ):
        if token not in source:
            raise RuntimeError(f"Phase256 self-test missing {token!r}")
    print("Phase 256 KGSL devnode/framework overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    patch_gpu_kconfig(root)
    patch_kgsl_makefile(root)
    patch_devfreq(root)
    patch_recorder(root)
    patch_device_core(root)
    patch_task_rename(root)
    patch_task_exit(root)
    config = configure(root)
    repair_retention_snapshot(config)
    audit(root, config)
    print(
        f"{MARKER}: KGSL devnode prerequisites, devfreq contract and framework milestones applied",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())