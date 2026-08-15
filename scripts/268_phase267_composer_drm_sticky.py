#!/usr/bin/env python3
"""Phase268: sticky vendor-composer -> DRM client boundary diagnostic.

Phase267R Run 5 hardware proved that SDE bus setup, SDE block init, DRM object
creation, DRM minor creation, and device_add publication all complete.  The
remaining boundary is userspace vendor composer entering DRM and progressing
through generic drm_open, msm_open/context_init, DRM ioctls, and atomic_check.

This overlay is diagnostic-only.  It does not change display, DRM, SDE, IOMMU,
probe ordering, return values, config, or device publication semantics.

The existing Phase211/212 DRMPOST call sites are reused.  Phase268:
- admits only DRMPOST 211/212 plus P268 into the focused recorder;
- explicitly keeps DRMPOST 211/212 noncritical so raw trace traffic cannot
  crowd out late retained state;
- identifies the vendor display composer process from the existing truncated
  graphics exec trace;
- latches composer-specific DRM path/open/ioctl/atomic state before capacity
  suppression;
- emits compact P268 A/B/C snapshots at the same late heartbeat checkpoints
  used by Phase267 sticky state.
"""
from __future__ import annotations

import sys
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
MSM_DRV = Path("drivers/a52_display/msm/msm_drv.c")
OPEN = Path("fs/open.c")
DRM_FILE = Path("drivers/gpu/drm/drm_file.c")
PHASE267 = "A52_PHASE267_PREDRM_STICKY_RETENTION_V1"
MARKER = "A52_PHASE268_COMPOSER_DRM_STICKY_V1"
TGID_MARKER = "A52_PHASE268_DRM_TGID_TRACE_V1"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


CRIT_OLD = '''/* A52_PHASE267_PREDRM_DIRECT_BOUNDARY_V3: dedicated diagnostics only. */
return !strncmp(message, "P267 ", 5) ||
       !strncmp(message, "F261 ", 5) ||
'''
CRIT_NEW = '''/* A52_PHASE267_PREDRM_DIRECT_BOUNDARY_V3: dedicated diagnostics only. */
/* A52_PHASE268_COMPOSER_DRM_STICKY_V1
 * DRMPOST 211/212 are admitted only so sticky state can consume them before
 * capacity suppression.  Keep those raw events noncritical to protect the
 * late retained window.  Only compact P268 snapshots are critical.
 */
if (!strncmp(message, "DRMPOST 211", 11) ||
    !strncmp(message, "DRMPOST 212", 11))
	return false;
return !strncmp(message, "P268 ", 5) ||
       !strncmp(message, "P267 ", 5) ||
       !strncmp(message, "F261 ", 5) ||
'''

ADMIT_OLD = '''if (strncmp(fmt, "P267", 4) &&
    strcmp(fmt, "%s enter fn=%s") &&
    strcmp(fmt, "%s exit fn=%s us=%llu") &&
    strncmp(fmt, "KMSPOST", 7) &&
    strncmp(fmt, "F261", 4) &&
'''
ADMIT_NEW = '''if (strncmp(fmt, "P268", 4) &&
    strncmp(fmt, "DRMPOST 211", 11) &&
    strncmp(fmt, "DRMPOST 212", 11) &&
    strncmp(fmt, "P267", 4) &&
    strcmp(fmt, "%s enter fn=%s") &&
    strcmp(fmt, "%s exit fn=%s us=%llu") &&
    strncmp(fmt, "KMSPOST", 7) &&
    strncmp(fmt, "F261", 4) &&
'''

STATE_ANCHOR = "static atomic_t a52_r267_reg_stage = ATOMIC_INIT(0);\n"
STATE_NEW = r'''/* A52_PHASE268_COMPOSER_DRM_STICKY_V1
 * Composer-specific state derived from already existing Phase211/212 events.
 * All fields are observation-only atomics so early evidence survives rollover.
 */
static atomic_t a52_r268_comp_pid = ATOMIC_INIT(-1);
static atomic_t a52_r268_exec_seen = ATOMIC_INIT(0);

static atomic_t a52_r268_path_count = ATOMIC_INIT(0);
static atomic_t a52_r268_path_kind = ATOMIC_INIT(0);
static atomic_t a52_r268_path_fd = ATOMIC_INIT(-61);
static atomic_t a52_r268_path_pending = ATOMIC_INIT(0);
static atomic_t a52_r268_path_errors = ATOMIC_INIT(0);

static atomic_t a52_r268_drm_count = ATOMIC_INIT(0);
static atomic_t a52_r268_drm_stage = ATOMIC_INIT(0);
static atomic_t a52_r268_drm_minor = ATOMIC_INIT(-1);
static atomic_t a52_r268_drm_rc = ATOMIC_INIT(-61);
static atomic_t a52_r268_drm_pending = ATOMIC_INIT(0);
static atomic_t a52_r268_drm_errors = ATOMIC_INIT(0);

static atomic_t a52_r268_msm_open_count = ATOMIC_INIT(0);
static atomic_t a52_r268_msm_open_rc = ATOMIC_INIT(-61);
static atomic_t a52_r268_msm_open_pending = ATOMIC_INIT(0);
static atomic_t a52_r268_msm_open_errors = ATOMIC_INIT(0);

static atomic_t a52_r268_ioctl_count = ATOMIC_INIT(0);
static atomic_t a52_r268_ioctl_nr = ATOMIC_INIT(-1);
static atomic_t a52_r268_ioctl_rc = ATOMIC_INIT(-61);
static atomic_t a52_r268_ioctl_pending = ATOMIC_INIT(0);
static atomic_t a52_r268_ioctl_errors = ATOMIC_INIT(0);

static atomic_t a52_r268_atomic_count = ATOMIC_INIT(0);
static atomic_t a52_r268_atomic_rc = ATOMIC_INIT(-61);
static atomic_t a52_r268_atomic_pending = ATOMIC_INIT(0);
static atomic_t a52_r268_atomic_errors = ATOMIC_INIT(0);
static atomic_t a52_r268_close_count = ATOMIC_INIT(0);

static int a52_r268_hex(const char *message, const char *key, int fallback)
{
	const char *p;
	int value = 0;
	int digit;
	bool seen = false;

	if (!message || !key)
		return fallback;
	p = strstr(message, key);
	if (!p)
		return fallback;
	p += strlen(key);
	if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X'))
		p += 2;
	while (*p) {
		if (*p >= '0' && *p <= '9')
			digit = *p - '0';
		else if (*p >= 'a' && *p <= 'f')
			digit = *p - 'a' + 10;
		else if (*p >= 'A' && *p <= 'F')
			digit = *p - 'A' + 10;
		else
			break;
		seen = true;
		value = (value << 4) | digit;
		p++;
	}
	return seen ? value : fallback;
}

static bool a52_r268_is_composer_pid(int pid)
{
	return pid >= 0 && pid == atomic_read(&a52_r268_comp_pid);
}

static bool a52_r268_entry_is_composer(const char *message)
{
	int pid;

	pid = a52_r228_dec(message, "g=", -1);
	if (pid < 0)
		pid = a52_r228_dec(message, "pid=", -1);
	if (pid < 0)
		pid = a52_r228_dec(message, "p=", -1);
	return a52_r268_is_composer_pid(pid);
}

static int a52_r268_path_kind_of(const char *message)
{
	if (strstr(message, "/dev/dri/render"))
		return 2;
	if (strstr(message, "/dev/dri/card"))
		return 1;
	if (strstr(message, "/dev/dri/"))
		return 3;
	if (strstr(message, "/sys/class/drm/"))
		return 4;
	return 0;
}

static void a52_r268_track_drm(const char *message)
{
	int n;
	int rc;
	int kind;

	if (!message)
		return;

	/* Phase212 stores only the first 40 path bytes.  The QTI composer binary
	 * is already uniquely identifiable by this prefix before "composer". */
	if (!strncmp(message, "DRMPOST 212 exec ", 17) &&
	    (strstr(message, "vendor.qti.hardware.displ") ||
	     strstr(message, "composer"))) {
		atomic_set(&a52_r268_comp_pid, a52_r228_dec(message, "p=", -1));
		atomic_inc(&a52_r268_exec_seen);
		return;
	}

	if (!strncmp(message, "DRMPOST 212 path ", 17)) {
		kind = a52_r268_path_kind_of(message);
		if (kind && a52_r268_entry_is_composer(message)) {
			n = a52_r228_dec(message, "n=", 0);
			atomic_inc(&a52_r268_path_count);
			atomic_set(&a52_r268_path_pending, n);
			atomic_set(&a52_r268_path_kind, kind);
		}
		return;
	}
	if (!strncmp(message, "DRMPOST 212 path-ret ", 21)) {
		n = a52_r228_dec(message, "n=", 0);
		if (n && n == atomic_read(&a52_r268_path_pending)) {
			rc = a52_r228_dec(message, "fd=", -61);
			atomic_set(&a52_r268_path_fd, rc);
			atomic_set(&a52_r268_path_pending, 0);
			if (rc < 0)
				atomic_inc(&a52_r268_path_errors);
		}
		return;
	}

	if (!strncmp(message, "DRMPOST 212 drm-open ", 21)) {
		if (a52_r268_entry_is_composer(message)) {
			n = a52_r228_dec(message, "n=", 0);
			atomic_inc(&a52_r268_drm_count);
			atomic_set(&a52_r268_drm_pending, n);
			atomic_set(&a52_r268_drm_stage, 1);
			atomic_set(&a52_r268_drm_minor, a52_r228_dec(message, "id=", -1));
		}
		return;
	}
	if (!strncmp(message, "DRMPOST 212 drm-acquire ", 24) ||
	    !strncmp(message, "DRMPOST 212 drm-minor ", 22) ||
	    !strncmp(message, "DRMPOST 212 drm-helper ", 23) ||
	    !strncmp(message, "DRMPOST 212 drm-setup ", 22)) {
		n = a52_r228_dec(message, "n=", 0);
		if (n && n == atomic_read(&a52_r268_drm_pending)) {
			if (!strncmp(message, "DRMPOST 212 drm-acquire ", 24))
				atomic_set(&a52_r268_drm_stage, 2);
			else if (!strncmp(message, "DRMPOST 212 drm-minor ", 22))
				atomic_set(&a52_r268_drm_stage, 3);
			else if (!strncmp(message, "DRMPOST 212 drm-helper ", 23))
				atomic_set(&a52_r268_drm_stage, 4);
			else
				atomic_set(&a52_r268_drm_stage, 5);
			if (strstr(message, "rc=")) {
				rc = a52_r228_dec(message, "rc=", -61);
				atomic_set(&a52_r268_drm_rc, rc);
				if (!strncmp(message, "DRMPOST 212 drm-acquire ", 24) && rc < 0) {
					atomic_inc(&a52_r268_drm_errors);
					atomic_set(&a52_r268_drm_pending, 0);
				}
			}
		}
		return;
	}
	if (!strncmp(message, "DRMPOST 212 drm-open-ret ", 25)) {
		n = a52_r228_dec(message, "n=", 0);
		if (n && n == atomic_read(&a52_r268_drm_pending)) {
			rc = a52_r228_dec(message, "rc=", -61);
			atomic_set(&a52_r268_drm_stage, 6);
			atomic_set(&a52_r268_drm_rc, rc);
			atomic_set(&a52_r268_drm_pending, 0);
			if (rc < 0)
				atomic_inc(&a52_r268_drm_errors);
		}
		return;
	}

	if (!strncmp(message, "DRMPOST 211 open ", 17)) {
		if (a52_r268_entry_is_composer(message)) {
			n = a52_r228_dec(message, "n=", 0);
			atomic_inc(&a52_r268_msm_open_count);
			atomic_set(&a52_r268_msm_open_pending, n);
		}
		return;
	}
	if (!strncmp(message, "DRMPOST 211 open-exit ", 22)) {
		n = a52_r228_dec(message, "n=", 0);
		if (n && n == atomic_read(&a52_r268_msm_open_pending)) {
			rc = a52_r228_dec(message, "rc=", -61);
			atomic_set(&a52_r268_msm_open_rc, rc);
			atomic_set(&a52_r268_msm_open_pending, 0);
			if (rc < 0)
				atomic_inc(&a52_r268_msm_open_errors);
		}
		return;
	}

	if (!strncmp(message, "DRMPOST 211 ioctl ", 18) ||
	    !strncmp(message, "DRMPOST 211 compat ", 18)) {
		if (a52_r268_entry_is_composer(message)) {
			n = a52_r228_dec(message, "n=", 0);
			atomic_inc(&a52_r268_ioctl_count);
			atomic_set(&a52_r268_ioctl_pending, n);
			atomic_set(&a52_r268_ioctl_nr, a52_r268_hex(message, "nr=", -1));
		}
		return;
	}
	if (!strncmp(message, "DRMPOST 211 ioctl-exit ", 23) ||
	    !strncmp(message, "DRMPOST 211 compat-exit ", 23)) {
		n = a52_r228_dec(message, "n=", 0);
		if (n && n == atomic_read(&a52_r268_ioctl_pending)) {
			rc = a52_r228_dec(message, "rc=", -61);
			atomic_set(&a52_r268_ioctl_rc, rc);
			atomic_set(&a52_r268_ioctl_pending, 0);
			if (rc < 0)
				atomic_inc(&a52_r268_ioctl_errors);
		}
		return;
	}

	if (!strncmp(message, "DRMPOST 211 check ", 18)) {
		if (a52_r268_entry_is_composer(message)) {
			n = a52_r228_dec(message, "n=", 0);
			atomic_inc(&a52_r268_atomic_count);
			atomic_set(&a52_r268_atomic_pending, n);
		}
		return;
	}
	if (!strncmp(message, "DRMPOST 211 check-exit ", 23)) {
		n = a52_r228_dec(message, "n=", 0);
		if (n && n == atomic_read(&a52_r268_atomic_pending)) {
			rc = a52_r228_dec(message, "rc=", -61);
			atomic_set(&a52_r268_atomic_rc, rc);
			atomic_set(&a52_r268_atomic_pending, 0);
			if (rc < 0)
				atomic_inc(&a52_r268_atomic_errors);
		}
		return;
	}

	if (!strncmp(message, "DRMPOST 211 close ", 18) &&
	    a52_r268_entry_is_composer(message))
		atomic_inc(&a52_r268_close_count);
}

static atomic_t a52_r267_reg_stage = ATOMIC_INIT(0);
'''

TRACK_OLD = '''\ta52_r267_track_display(message);\n\tif (!strncmp(message, "TRIPOST ", 8))\n'''
TRACK_NEW = '''\ta52_r267_track_display(message);\n\ta52_r268_track_drm(message);\n\tif (!strncmp(message, "TRIPOST ", 8))\n'''

SNAPSHOT_ANCHOR = "static int a52_r228_clip(int value)\n"
SNAPSHOT_NEW = r'''static void a52_r268_snapshot(unsigned int tick)
{
	if (!(tick == 120U || tick == 150U || tick == 160U ||
	      tick == 170U || tick == 180U))
		return;

	a52_ackfr_record("P268 A t=%u cp=%d ex=%d pa=%d/%d/%d dr=%d/%d/%d/%d",
		tick, atomic_read(&a52_r268_comp_pid), atomic_read(&a52_r268_exec_seen),
		atomic_read(&a52_r268_path_count), atomic_read(&a52_r268_path_kind),
		atomic_read(&a52_r268_path_fd), atomic_read(&a52_r268_drm_count),
		atomic_read(&a52_r268_drm_stage), atomic_read(&a52_r268_drm_minor),
		atomic_read(&a52_r268_drm_rc));
	a52_ackfr_record("P268 B t=%u mo=%d/%d io=%d/%d/%d ac=%d/%d cl=%d",
		tick, atomic_read(&a52_r268_msm_open_count),
		atomic_read(&a52_r268_msm_open_rc), atomic_read(&a52_r268_ioctl_count),
		atomic_read(&a52_r268_ioctl_nr), atomic_read(&a52_r268_ioctl_rc),
		atomic_read(&a52_r268_atomic_count), atomic_read(&a52_r268_atomic_rc),
		atomic_read(&a52_r268_close_count));
	a52_ackfr_record("P268 C t=%u pn=%d,%d,%d,%d,%d er=%d,%d,%d,%d,%d",
		tick, atomic_read(&a52_r268_path_pending), atomic_read(&a52_r268_drm_pending),
		atomic_read(&a52_r268_msm_open_pending), atomic_read(&a52_r268_ioctl_pending),
		atomic_read(&a52_r268_atomic_pending), atomic_read(&a52_r268_path_errors),
		atomic_read(&a52_r268_drm_errors), atomic_read(&a52_r268_msm_open_errors),
		atomic_read(&a52_r268_ioctl_errors), atomic_read(&a52_r268_atomic_errors));
}

static int a52_r228_clip(int value)
'''

HEARTBEAT_OLD = '''\ta52_r267_display_snapshot(tick);\n\t/* A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1: no Phase 242 heartbeat snapshot */\n'''
HEARTBEAT_NEW = '''\ta52_r267_display_snapshot(tick);\n\ta52_r268_snapshot(tick);\n\t/* A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1: no Phase 242 heartbeat snapshot */\n'''


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        validate_recorder(text, label)
        return text
    if PHASE267 not in text:
        raise RuntimeError(f"{label}: Phase267 sticky base missing")
    text = one(text, CRIT_OLD, CRIT_NEW, f"{label}: critical policy")
    text = one(text, ADMIT_OLD, ADMIT_NEW, f"{label}: DRMPOST admission")
    text = one(text, STATE_ANCHOR, STATE_NEW, f"{label}: sticky state")
    text = one(text, TRACK_OLD, TRACK_NEW, f"{label}: tracker call")
    text = one(text, SNAPSHOT_ANCHOR, SNAPSHOT_NEW, f"{label}: snapshot function")
    text = one(text, HEARTBEAT_OLD, HEARTBEAT_NEW, f"{label}: heartbeat snapshot")
    validate_recorder(text, label)
    return text


def patch_tgid(text: str, rel: Path) -> str:
    if TGID_MARKER in text:
        return text
    original = text
    if rel == OPEN:
        old = '''a52_ackfr_record("DRMPOST 212 path n=%u p=%d c=%.16s %.32s",\n\t\t\t\t\t  trace_id, current->pid, current->comm, tmp->name);'''
        new = '''/* A52_PHASE268_DRM_TGID_TRACE_V1: diagnostic identity only. */\n\t\t\ta52_ackfr_record("DRMPOST 212 path n=%u p=%d c=%.16s %.32s g=%d",\n\t\t\t\t\t  trace_id, current->pid, current->comm, tmp->name, current->tgid);'''
        text = one(text, old, new, f"{rel}: path tgid")
    elif rel == DRM_FILE:
        old = '''a52_ackfr_record("DRMPOST 212 drm-open n=%u id=%u p=%d",\n\t\t\t\t  trace_id, iminor(inode), current->pid);'''
        new = '''/* A52_PHASE268_DRM_TGID_TRACE_V1: diagnostic identity only. */\n\t\ta52_ackfr_record("DRMPOST 212 drm-open n=%u id=%u p=%d g=%d",\n\t\t\t\t  trace_id, iminor(inode), current->pid, current->tgid);'''
        text = one(text, old, new, f"{rel}: drm-open tgid")
    elif rel == MSM_DRV:
        replacements = (
            (
                '''a52_ackfr_record("DRMPOST 211 open n=%u pid=%d comm=%.16s",\n\t\t\t\t  trace_id, current->pid, current->comm);''',
                '''/* A52_PHASE268_DRM_TGID_TRACE_V1: diagnostic identity only. */\n\t\ta52_ackfr_record("DRMPOST 211 open n=%u pid=%d comm=%.16s g=%d",\n\t\t\t\t  trace_id, current->pid, current->comm, current->tgid);''',
                "msm open tgid",
            ),
            (
                '''a52_ackfr_record("DRMPOST 211 ioctl n=%u pid=%d nr=0x%x",\n\t\t\t\t  trace_id, current->pid, _IOC_NR(cmd));''',
                '''a52_ackfr_record("DRMPOST 211 ioctl n=%u pid=%d nr=0x%x g=%d",\n\t\t\t\t  trace_id, current->pid, _IOC_NR(cmd), current->tgid);''',
                "ioctl tgid",
            ),
            (
                '''a52_ackfr_record("DRMPOST 211 compat n=%u pid=%d nr=0x%x",\n\t\t\t\t  trace_id, current->pid, _IOC_NR(cmd));''',
                '''a52_ackfr_record("DRMPOST 211 compat n=%u pid=%d nr=0x%x g=%d",\n\t\t\t\t  trace_id, current->pid, _IOC_NR(cmd), current->tgid);''',
                "compat tgid",
            ),
            (
                '''a52_ackfr_record("DRMPOST 211 check n=%u pid=%d comm=%.16s",\n\t\t\t\t  trace_id, current->pid, current->comm);''',
                '''a52_ackfr_record("DRMPOST 211 check n=%u pid=%d comm=%.16s g=%d",\n\t\t\t\t  trace_id, current->pid, current->comm, current->tgid);''',
                "check tgid",
            ),
            (
                '''a52_ackfr_record("DRMPOST 211 close n=%u pid=%d comm=%.16s",\n\t\t\t\t  trace_id, current->pid, current->comm);''',
                '''a52_ackfr_record("DRMPOST 211 close n=%u pid=%d comm=%.16s g=%d",\n\t\t\t\t  trace_id, current->pid, current->comm, current->tgid);''',
                "close tgid",
            ),
        )
        for old, new, label in replacements:
            text = one(text, old, new, f"{rel}: {label}")
    if text == original:
        raise RuntimeError(f"{rel}: Phase268 tgid patch made no change")
    if TGID_MARKER not in text:
        raise RuntimeError(f"{rel}: Phase268 tgid marker missing")
    return text


def validate_recorder(text: str, label: str) -> None:
    for token in (
        MARKER,
        '!strncmp(message, "P268 ", 5)',
        '!strncmp(message, "DRMPOST 211", 11)',
        '!strncmp(message, "DRMPOST 212", 11)',
        'strncmp(fmt, "P268", 4)',
        'strncmp(fmt, "DRMPOST 211", 11)',
        'strncmp(fmt, "DRMPOST 212", 11)',
        'a52_r268_track_drm(message);',
        'P268 A t=%u cp=%d ex=%d pa=%d/%d/%d dr=%d/%d/%d/%d',
        'P268 B t=%u mo=%d/%d io=%d/%d/%d ac=%d/%d cl=%d',
        'P268 C t=%u pn=%d,%d,%d,%d,%d er=%d,%d,%d,%d,%d',
        'a52_r268_snapshot(tick);',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def locate(args: list[str]) -> Path:
    candidates: list[Path] = []
    for arg in args:
        if arg.startswith("-"):
            continue
        p = Path(arg)
        candidates.extend((p, p.parent))
    candidates.extend((Path.cwd() / "gki/common", Path.cwd() / "workspace/gki-phase199-src"))
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidates:
        root = root.resolve(strict=False)
        if root in seen:
            continue
        seen.add(root)
        rec = root / RECORDER
        if rec.is_file() and PHASE267 in rec.read_text(encoding="utf-8"):
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(f"expected one generated Phase267 root, found {len(hits)}: {hits}")
    return hits[0]


def self_test() -> None:
    rec = (
        PHASE267 + "\n" + CRIT_OLD + ADMIT_OLD +
        STATE_ANCHOR +
        "static void a52_r228_track_message(const char *message)\n{\n"
        "\tif (!message)\n\t\treturn;\n" + TRACK_OLD + "}\n" +
        SNAPSHOT_ANCHOR + "{ return value; }\n" +
        "void heartbeat(unsigned int tick)\n{\n" + HEARTBEAT_OLD + "}\n"
    )
    patched = patch_recorder(rec, "fixture/recorder")
    validate_recorder(patched, "fixture/recorder")
    if patch_recorder(patched, "fixture/idempotent") != patched:
        raise AssertionError("Phase268 recorder patch is not idempotent")
    print("Phase268 composer->DRM sticky self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    rec = root / RECORDER
    rec.write_text(patch_recorder(rec.read_text(encoding="utf-8"), str(rec)), encoding="utf-8")
    for rel in (OPEN, DRM_FILE, MSM_DRV):
        path = root / rel
        if not path.is_file():
            raise RuntimeError(f"missing Phase268 dependency: {path}")
        path.write_text(patch_tgid(path.read_text(encoding="utf-8"), rel), encoding="utf-8")
    print(f"{MARKER}: composer DRM sticky boundary applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
