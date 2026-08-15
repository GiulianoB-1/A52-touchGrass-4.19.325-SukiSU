#!/usr/bin/env python3
"""Phase 267: diagnostic-only sticky pre-DRM display boundary.

Retains Phase266 semantics. Adds exactly two kinds of observability:
1) enter/exit markers around the existing initial SDE data-bus quota loop;
2) sticky recorder state summarising existing KMS/DRM checkpoints at late
   heartbeat ticks so early display state survives recorder-capacity pressure.

No return values, config symbols, probe ordering, bus votes, IOMMU behavior,
or DRM/SDE control flow are changed.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
SDE = Path("drivers/a52_display/msm/sde/sde_kms.c")
IOMMU = Path("drivers/iommu/iommu.c")
MARKER = "A52_PHASE267_PREDRM_STICKY_BOUNDARY_V1"
PHASE266 = "A52_PHASE266_KGSL_DYNAMIC_IOMMU_GROUP_COMPAT_V1"

RECORD_FN = "void a52_ackfr_record(const char *fmt, ...)\n{\n"
LATCH_OLD = "\ta52_r242_sticky_latch(event.message);\n"
LATCH_NEW = "\ta52_r267_display_latch(event.message);\n" + LATCH_OLD
SNAP_OLD = "\ta52_r242_snapshot(tick);\n"
SNAP_NEW = SNAP_OLD + "\ta52_r267_display_snapshot(tick);\n"

HELPERS = r'''/* A52_PHASE267_PREDRM_STICKY_BOUNDARY_V1
 * Diagnostic-only sticky summary of the display path before DRM publication.
 * Existing early KMS/DRM messages update atomic state before recorder capacity
 * rejection. Compact DRMPOST snapshots are replayed at late heartbeat ticks.
 */
#define A52_R267_UNSEEN (-4096)
static atomic_t a52_r267_bus_enter = ATOMIC_INIT(0);
static atomic_t a52_r267_bus_exit = ATOMIC_INIT(0);
static atomic_t a52_r267_blocks_enter = ATOMIC_INIT(0);
static atomic_t a52_r267_core_rev = ATOMIC_INIT(0);
static atomic_t a52_r267_blocks_exit = ATOMIC_INIT(0);
static atomic_t a52_r267_blocks_rc = ATOMIC_INIT(A52_R267_UNSEEN);
static atomic_t a52_r267_drm_obj_exit = ATOMIC_INIT(0);
static atomic_t a52_r267_drm_obj_rc = ATOMIC_INIT(A52_R267_UNSEEN);
static atomic_t a52_r267_primary_node = ATOMIC_INIT(0);
static atomic_t a52_r267_primary_add = ATOMIC_INIT(0);
static atomic_t a52_r267_primary_add_rc = ATOMIC_INIT(A52_R267_UNSEEN);
static atomic_t a52_r267_dri_path = ATOMIC_INIT(0);
static atomic_t a52_r267_drm_open = ATOMIC_INIT(0);
static atomic_t a52_r267_drm_pid = ATOMIC_INIT(-1);

static void a52_r267_display_latch(const char *message)
{
	int value;

	if (!message)
		return;
	/* Do not let our replay snapshots mutate the sticky state. */
	if (!strncmp(message, "DRMPOST 212 P267A ", 18) ||
	    !strncmp(message, "DRMPOST 212 P267B ", 18))
		return;

	if (!strncmp(message, "DRMPOST 212 P267 bus-enter", 26))
		atomic_inc(&a52_r267_bus_enter);
	if (!strncmp(message, "DRMPOST 212 P267 bus-exit", 25))
		atomic_inc(&a52_r267_bus_exit);
	if (!strncmp(message, "KMSPOST blocks enter", 20))
		atomic_inc(&a52_r267_blocks_enter);
	if (!strncmp(message, "KMSBLK core-rev enter", 21))
		atomic_inc(&a52_r267_core_rev);
	if (!strncmp(message, "KMSPOST blocks exit ", 20)) {
		atomic_inc(&a52_r267_blocks_exit);
		atomic_set(&a52_r267_blocks_rc,
			a52_r228_dec(message, "rc=", A52_R267_UNSEEN));
	}
	if (!strncmp(message, "KMSBLK drm-obj exit ", 20)) {
		atomic_inc(&a52_r267_drm_obj_exit);
		atomic_set(&a52_r267_drm_obj_rc,
			a52_r228_dec(message, "rc=", A52_R267_UNSEEN));
	}
	if (!strncmp(message, "DRMPOST 212 node ", 17) &&
	    strstr(message, "type=0") && strstr(message, "idx=0"))
		atomic_set(&a52_r267_primary_node, 1);
	if (!strncmp(message, "DRMPOST 212 node-add ", 21) &&
	    strstr(message, "type=0") && strstr(message, "idx=0")) {
		atomic_inc(&a52_r267_primary_add);
		atomic_set(&a52_r267_primary_add_rc,
			a52_r228_dec(message, "rc=", A52_R267_UNSEEN));
	}
	if (!strncmp(message, "DRMPOST 212 path ", 17) &&
	    strstr(message, "/dev/dri/")) {
		atomic_inc(&a52_r267_dri_path);
		value = a52_r228_dec(message, "p=", -1);
		if (value >= 0)
			atomic_set(&a52_r267_drm_pid, value);
	}
	if (!strncmp(message, "DRMPOST 212 drm-open ", 21)) {
		atomic_inc(&a52_r267_drm_open);
		value = a52_r228_dec(message, "p=", -1);
		if (value >= 0)
			atomic_set(&a52_r267_drm_pid, value);
	}
}

static bool a52_r267_snapshot_tick(unsigned int tick)
{
	return tick == 120 || tick == 140 || tick == 145 || tick == 150 ||
	       tick == 155 || tick == 160 || tick == 165 || tick == 170 ||
	       tick == 175 || tick == 180 || tick == 190 || tick == 200;
}

static void a52_r267_display_snapshot(unsigned int tick)
{
	if (!a52_r267_snapshot_tick(tick))
		return;

	a52_ackfr_record("DRMPOST 212 P267A t=%u bi=%d bx=%d kb=%d kr=%d kx=%d/%d",
		tick, atomic_read(&a52_r267_bus_enter),
		atomic_read(&a52_r267_bus_exit),
		atomic_read(&a52_r267_blocks_enter),
		atomic_read(&a52_r267_core_rev),
		atomic_read(&a52_r267_blocks_exit),
		atomic_read(&a52_r267_blocks_rc));
	a52_ackfr_record("DRMPOST 212 P267B t=%u ko=%d/%d pn=%d pa=%d/%d dp=%d do=%d p=%d",
		tick, atomic_read(&a52_r267_drm_obj_exit),
		atomic_read(&a52_r267_drm_obj_rc),
		atomic_read(&a52_r267_primary_node),
		atomic_read(&a52_r267_primary_add),
		atomic_read(&a52_r267_primary_add_rc),
		atomic_read(&a52_r267_dri_path),
		atomic_read(&a52_r267_drm_open),
		atomic_read(&a52_r267_drm_pid));
}

'''

SDE_OLD = '''\tfor (i = 0; i < SDE_POWER_HANDLE_DBUS_ID_MAX; i++)
\t\tsde_power_data_bus_set_quota(&priv->phandle, i,
\t\t\tSDE_POWER_HANDLE_CONT_SPLASH_BUS_AB_QUOTA,
\t\t\tSDE_POWER_HANDLE_CONT_SPLASH_BUS_IB_QUOTA);

\ta52_ackfr_record("KMSBLK core-rev enter");
'''
SDE_NEW = '''\t/* A52_PHASE267_PREDRM_STICKY_BOUNDARY_V1: diagnostic only. */
\ta52_ackfr_record("DRMPOST 212 P267 bus-enter n=%d",
\t\tSDE_POWER_HANDLE_DBUS_ID_MAX);
\tfor (i = 0; i < SDE_POWER_HANDLE_DBUS_ID_MAX; i++)
\t\tsde_power_data_bus_set_quota(&priv->phandle, i,
\t\t\tSDE_POWER_HANDLE_CONT_SPLASH_BUS_AB_QUOTA,
\t\t\tSDE_POWER_HANDLE_CONT_SPLASH_BUS_IB_QUOTA);
\ta52_ackfr_record("DRMPOST 212 P267 bus-exit n=%d",
\t\tSDE_POWER_HANDLE_DBUS_ID_MAX);

\ta52_ackfr_record("KMSBLK core-rev enter");
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
    if "A52_PHASE242_CX_STICKY_STATE_V1" not in text:
        raise RuntimeError(f"{label}: Phase242 sticky-state base missing")
    text = one(text, RECORD_FN, HELPERS + RECORD_FN, f"{label}: helpers")
    text = one(text, LATCH_OLD, LATCH_NEW, f"{label}: latch hook")
    text = one(text, SNAP_OLD, SNAP_NEW, f"{label}: late snapshot hook")
    validate_recorder(text, label)
    return text


def patch_sde(text: str, label: str) -> str:
    if MARKER in text:
        validate_sde(text, label)
        return text
    text = one(text, SDE_OLD, SDE_NEW, f"{label}: initial data-bus quota loop")
    validate_sde(text, label)
    return text


def validate_recorder(text: str, label: str) -> None:
    required = (
        MARKER,
        "a52_r267_display_latch(event.message);",
        "a52_r267_display_snapshot(tick);",
        "DRMPOST 212 P267A t=%u bi=%d bx=%d kb=%d kr=%d kx=%d/%d",
        "DRMPOST 212 P267B t=%u ko=%d/%d pn=%d pa=%d/%d dp=%d do=%d p=%d",
        'strstr(message, "type=0")',
        'strstr(message, "idx=0")',
        'strstr(message, "/dev/dri/")',
        'a52_r228_dec(message, "rc=", A52_R267_UNSEEN)',
    )
    for token in required:
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    if text.index("a52_r267_display_latch(event.message);") > text.index(LATCH_OLD):
        raise RuntimeError(f"{label}: Phase267 latch must run before Phase242 latch")


def validate_sde(text: str, label: str) -> None:
    for token in (
        MARKER,
        'DRMPOST 212 P267 bus-enter n=%d',
        'DRMPOST 212 P267 bus-exit n=%d',
        'sde_power_data_bus_set_quota(&priv->phandle, i,',
        'SDE_POWER_HANDLE_CONT_SPLASH_BUS_AB_QUOTA',
        'SDE_POWER_HANDLE_CONT_SPLASH_BUS_IB_QUOTA',
        'KMSBLK core-rev enter',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")
    if text.index('P267 bus-enter') > text.index('sde_power_data_bus_set_quota(&priv->phandle, i,'):
        raise RuntimeError(f"{label}: bus-enter is not before the quota call")
    if text.index('P267 bus-exit') < text.index('sde_power_data_bus_set_quota(&priv->phandle, i,'):
        raise RuntimeError(f"{label}: bus-exit is not after the quota call")


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        roots.extend((path, path.parent))
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
    rec = root / RECORDER
    sde = root / SDE
    iommu = root / IOMMU
    if not rec.is_file() or not sde.is_file() or not iommu.is_file():
        return False
    return PHASE266 in iommu.read_text(encoding="utf-8")


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    matches = [r for r in candidate_roots(args, base) if root_matches(r)]
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in matches:
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    if len(unique) != 1:
        rendered = ", ".join(map(str, unique)) or "none"
        raise RuntimeError(f"expected one generated Phase266 source root, found {len(unique)}: {rendered}")
    return unique[0]


def self_test() -> None:
    rec_fixture = (
        "/* A52_PHASE242_CX_STICKY_STATE_V1 */\n"
        "static int a52_r228_dec(const char *m, const char *k, int d) { return d; }\n"
        "static void a52_r242_sticky_latch(const char *m) {}\n"
        "static void a52_r242_snapshot(unsigned int tick) {}\n"
        + RECORD_FN
        + "\tchar event_message[1];\n"
        + LATCH_OLD
        + "}\n"
        "static void hb(unsigned int tick)\n{\n"
        + SNAP_OLD
        + "}\n"
    )
    rec = patch_recorder(rec_fixture, "fixture/recorder")
    if patch_recorder(rec, "fixture/recorder/idempotent") != rec:
        raise AssertionError("recorder overlay is not idempotent")

    sde = patch_sde(SDE_OLD, "fixture/sde")
    if patch_sde(sde, "fixture/sde/idempotent") != sde:
        raise AssertionError("SDE overlay is not idempotent")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "gki/common"
        for rel, data in (
            (RECORDER, rec_fixture),
            (SDE, SDE_OLD),
            (IOMMU, PHASE266 + "\n"),
        ):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(data, encoding="utf-8")
        if locate([], Path(td)).resolve() != root.resolve():
            raise AssertionError("generated Phase266 root locator failed")
    print("Phase267 pre-DRM sticky boundary self-test: PASS", flush=True)


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        self_test()
        return 0
    root = locate(args)
    rec = root / RECORDER
    sde = root / SDE
    rec.write_text(patch_recorder(rec.read_text(encoding="utf-8"), str(rec)), encoding="utf-8")
    sde.write_text(patch_sde(sde.read_text(encoding="utf-8"), str(sde)), encoding="utf-8")
    print(f"{MARKER}: diagnostic-only display sticky boundary applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
