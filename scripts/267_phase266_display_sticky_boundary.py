#!/usr/bin/env python3
"""Phase 267: diagnostic-only direct pre-DRM boundary.

Retains Phase266 functional semantics and adds one dedicated, low-volume P267
recorder class. Phase266's focused pre-format admission gate suppresses the old
DRMPOST/KMSPOST/KMSBLK classes, so Phase267 admits only strings whose format
starts with "P267" and marks only messages beginning "P267 " as critical.

P267 checkpoints bracket existing operations only:
- initial SDE data-bus quota loop;
- SDE block-init call and DRM-object init result;
- DRM minor sysfs allocation and device_add publication.

No return value, branch, loop bound, config symbol, bus vote, probe ordering,
IOMMU behavior, or DRM/SDE control-flow decision is changed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SDE = Path("drivers/a52_display/msm/sde/sde_kms.c")
DRM = Path("drivers/gpu/drm/drm_drv.c")
RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
IOMMU = Path("drivers/iommu/iommu.c")
MARKER = "A52_PHASE267_PREDRM_DIRECT_BOUNDARY_V3"
PHASE266 = "A52_PHASE266_KGSL_DYNAMIC_IOMMU_GROUP_COMPAT_V1"

CRIT_OLD = 'return !strncmp(message, "F261 ", 5) ||\n'
CRIT_NEW = ('/* A52_PHASE267_PREDRM_DIRECT_BOUNDARY_V3: dedicated diagnostics only. */\n'
            'return !strncmp(message, "P267 ", 5) ||\n'
            '       !strncmp(message, "F261 ", 5) ||\n')
ADMIT_OLD = 'if (strncmp(fmt, "F261", 4) &&\n'
ADMIT_NEW = ('/* A52_PHASE267_PREDRM_DIRECT_BOUNDARY_V3: admit only P267 diagnostics. */\n'
             'if (strncmp(fmt, "P267", 4) &&\n'
             '    strncmp(fmt, "F261", 4) &&\n')

SDE_BUS_OLD = '''\tfor (i = 0; i < SDE_POWER_HANDLE_DBUS_ID_MAX; i++)
\t\tsde_power_data_bus_set_quota(&priv->phandle, i,
\t\t\tSDE_POWER_HANDLE_CONT_SPLASH_BUS_AB_QUOTA,
\t\t\tSDE_POWER_HANDLE_CONT_SPLASH_BUS_IB_QUOTA);

\ta52_ackfr_record("KMSBLK core-rev enter");
'''
SDE_BUS_NEW = '''\t/* A52_PHASE267_PREDRM_DIRECT_BOUNDARY_V3: diagnostic only. */
\ta52_ackfr_record("P267 bus-enter n=%d", SDE_POWER_HANDLE_DBUS_ID_MAX);
\tfor (i = 0; i < SDE_POWER_HANDLE_DBUS_ID_MAX; i++)
\t\tsde_power_data_bus_set_quota(&priv->phandle, i,
\t\t\tSDE_POWER_HANDLE_CONT_SPLASH_BUS_AB_QUOTA,
\t\t\tSDE_POWER_HANDLE_CONT_SPLASH_BUS_IB_QUOTA);
\ta52_ackfr_record("P267 bus-exit n=%d", SDE_POWER_HANDLE_DBUS_ID_MAX);

\ta52_ackfr_record("KMSBLK core-rev enter");
'''

SDE_DRMOBJ_OLD = '''\ta52_ackfr_record("KMSBLK drm-obj enter");
\trc = _sde_kms_drm_obj_init(sde_kms);
\ta52_ackfr_record("KMSBLK drm-obj exit rc=%d crtc=%d enc=%d conn=%d plane=%d",
\t\trc, priv->num_crtcs, priv->num_encoders,
\t\tpriv->num_connectors, priv->num_planes);
'''
SDE_DRMOBJ_NEW = '''\ta52_ackfr_record("KMSBLK drm-obj enter");
\ta52_ackfr_record("P267 drm-obj-enter");
\trc = _sde_kms_drm_obj_init(sde_kms);
\ta52_ackfr_record("P267 drm-obj-exit rc=%d c=%d e=%d n=%d p=%d",
\t\trc, priv->num_crtcs, priv->num_encoders,
\t\tpriv->num_connectors, priv->num_planes);
\ta52_ackfr_record("KMSBLK drm-obj exit rc=%d crtc=%d enc=%d conn=%d plane=%d",
\t\trc, priv->num_crtcs, priv->num_encoders,
\t\tpriv->num_connectors, priv->num_planes);
'''

SDE_BLOCKS_OLD = '''\ta52_ackfr_record("KMSPOST blocks enter");
\trc = _sde_kms_hw_init_blocks(sde_kms, dev, priv);
\ta52_ackfr_record("KMSPOST blocks exit rc=%d crtc=%d enc=%d conn=%d plane=%d",
\t\trc, priv->num_crtcs, priv->num_encoders,
\t\tpriv->num_connectors, priv->num_planes);
'''
SDE_BLOCKS_NEW = '''\ta52_ackfr_record("KMSPOST blocks enter");
\ta52_ackfr_record("P267 blocks-enter");
\trc = _sde_kms_hw_init_blocks(sde_kms, dev, priv);
\ta52_ackfr_record("P267 blocks-exit rc=%d c=%d e=%d n=%d p=%d",
\t\trc, priv->num_crtcs, priv->num_encoders,
\t\tpriv->num_connectors, priv->num_planes);
\ta52_ackfr_record("KMSPOST blocks exit rc=%d crtc=%d enc=%d conn=%d plane=%d",
\t\trc, priv->num_crtcs, priv->num_encoders,
\t\tpriv->num_connectors, priv->num_planes);
'''

DRM_SYSFS_OLD = '''\tif (IS_ERR(minor->kdev)) {
\t\tr = PTR_ERR(minor->kdev);
\t\ta52_ackfr_record("DRMPOST 212 node type=%u idx=%d sysfs=%d",
\t\t\t\t  type, minor->index, r);
\t\treturn r;
\t}

\ta52_ackfr_record("DRMPOST 212 node type=%u idx=%d name=%.16s",
\t\t\t  type, minor->index, dev_name(minor->kdev));
'''
DRM_SYSFS_NEW = '''\t/* A52_PHASE267_PREDRM_DIRECT_BOUNDARY_V3: diagnostic only. */
\tif (IS_ERR(minor->kdev)) {
\t\tr = PTR_ERR(minor->kdev);
\t\ta52_ackfr_record("P267 node type=%u idx=%d rc=%d",
\t\t\t\t  type, minor->index, r);
\t\ta52_ackfr_record("DRMPOST 212 node type=%u idx=%d sysfs=%d",
\t\t\t\t  type, minor->index, r);
\t\treturn r;
\t}

\ta52_ackfr_record("P267 node type=%u idx=%d rc=0", type, minor->index);
\ta52_ackfr_record("DRMPOST 212 node type=%u idx=%d name=%.16s",
\t\t\t  type, minor->index, dev_name(minor->kdev));
'''

DRM_NODE_ADD_OLD = '''\tret = device_add(minor->kdev);
\ta52_ackfr_record("DRMPOST 212 node-add type=%u idx=%d rc=%d",
\t\t\t  type, minor->index, ret);
'''
DRM_NODE_ADD_NEW = '''\tret = device_add(minor->kdev);
\ta52_ackfr_record("P267 node-add type=%u idx=%d rc=%d",
\t\t\t  type, minor->index, ret);
\ta52_ackfr_record("DRMPOST 212 node-add type=%u idx=%d rc=%d",
\t\t\t  type, minor->index, ret);
'''


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        validate_recorder(text, label)
        return text
    if 'A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1' not in text:
        raise RuntimeError(f"{label}: expected Phase266 focused recorder shape missing")
    text = one(text, CRIT_OLD, CRIT_NEW, f"{label}: critical predicate")
    text = one(text, ADMIT_OLD, ADMIT_NEW, f"{label}: admission predicate")
    validate_recorder(text, label)
    return text


def patch_sde(text: str, label: str) -> str:
    if MARKER in text:
        validate_sde(text, label)
        return text
    text = one(text, SDE_BUS_OLD, SDE_BUS_NEW, f"{label}: data-bus loop")
    text = one(text, SDE_DRMOBJ_OLD, SDE_DRMOBJ_NEW, f"{label}: DRM object init")
    text = one(text, SDE_BLOCKS_OLD, SDE_BLOCKS_NEW, f"{label}: KMS blocks call")
    validate_sde(text, label)
    return text


def patch_drm(text: str, label: str) -> str:
    if MARKER in text:
        validate_drm(text, label)
        return text
    text = one(text, DRM_SYSFS_OLD, DRM_SYSFS_NEW, f"{label}: minor allocation")
    text = one(text, DRM_NODE_ADD_OLD, DRM_NODE_ADD_NEW, f"{label}: minor publication")
    validate_drm(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        MARKER,
        '!strncmp(message, "P267 ", 5)',
        'strncmp(fmt, "P267", 4)',
        '!strncmp(message, "F261 ", 5)',
        'strncmp(fmt, "F261", 4)',
        'A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def validate_sde(text: str, label: str) -> None:
    for token in (
        MARKER,
        'P267 bus-enter n=%d',
        'P267 bus-exit n=%d',
        'P267 drm-obj-enter',
        'P267 drm-obj-exit rc=%d c=%d e=%d n=%d p=%d',
        'P267 blocks-enter',
        'P267 blocks-exit rc=%d c=%d e=%d n=%d p=%d',
        'sde_power_data_bus_set_quota(&priv->phandle, i,',
        'KMSBLK core-rev enter',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    if text.index('P267 bus-enter') > text.index('sde_power_data_bus_set_quota(&priv->phandle, i,'):
        raise RuntimeError(f"{label}: bus-enter not before quota loop")
    if text.index('P267 bus-exit') < text.index('sde_power_data_bus_set_quota(&priv->phandle, i,'):
        raise RuntimeError(f"{label}: bus-exit not after quota loop")


def validate_drm(text: str, label: str) -> None:
    for token in (
        MARKER,
        'P267 node type=%u idx=%d rc=%d',
        'P267 node type=%u idx=%d rc=0',
        'P267 node-add type=%u idx=%d rc=%d',
        'DRMPOST 212 node type=%u idx=%d sysfs=%d',
        'DRMPOST 212 node-add type=%u idx=%d rc=%d',
        'ret = device_add(minor->kdev);',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


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
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def root_matches(root: Path) -> bool:
    paths = (root / SDE, root / DRM, root / RECORDER, root / IOMMU)
    return all(p.is_file() for p in paths) and PHASE266 in (root / IOMMU).read_text(encoding="utf-8")


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        if not root_matches(root):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        rendered = ", ".join(map(str, hits)) or "none"
        raise RuntimeError(f"expected one generated Phase266 root, found {len(hits)}: {rendered}")
    return hits[0]


def self_test() -> None:
    rec = ('A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1\n' +
           CRIT_OLD + ADMIT_OLD)
    sde = SDE_BUS_OLD + SDE_DRMOBJ_OLD + SDE_BLOCKS_OLD
    drm = DRM_SYSFS_OLD + DRM_NODE_ADD_OLD
    rec2 = patch_recorder(rec, "fixture/rec")
    sde2 = patch_sde(sde, "fixture/sde")
    drm2 = patch_drm(drm, "fixture/drm")
    assert patch_recorder(rec2, "fixture/rec2") == rec2
    assert patch_sde(sde2, "fixture/sde2") == sde2
    assert patch_drm(drm2, "fixture/drm2") == drm2

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "gki/common"
        for rel, data in ((RECORDER, rec), (SDE, sde), (DRM, drm), (IOMMU, PHASE266 + "\n")):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data, encoding="utf-8")
        if locate([], Path(td)).resolve() != root.resolve():
            raise AssertionError("generated Phase266 root locator failed")
    print("Phase267 direct pre-DRM boundary self-test: PASS", flush=True)


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        self_test()
        return 0
    root = locate(args)
    rec = root / RECORDER
    sde = root / SDE
    drm = root / DRM
    rec.write_text(patch_recorder(rec.read_text(encoding="utf-8"), str(rec)), encoding="utf-8")
    sde.write_text(patch_sde(sde.read_text(encoding="utf-8"), str(sde)), encoding="utf-8")
    drm.write_text(patch_drm(drm.read_text(encoding="utf-8"), str(drm)), encoding="utf-8")
    print(f"{MARKER}: dedicated critical P267 boundary applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
