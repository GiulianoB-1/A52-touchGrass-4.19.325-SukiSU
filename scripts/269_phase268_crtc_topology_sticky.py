#!/usr/bin/env python3
"""Phase269: retain zero-CRTC topology and Composer DRM resource enumeration.

Hardware Phase268 proved that vendor composer opens /dev/dri/card0, completes
DRM/MSM open and reaches DRM ioctls, while Phase267 sticky state simultaneously
reports a completed DRM-object init with 0 CRTCs, 2 encoders, 2 connectors and
8 planes.

The transplanted SDE code already contains bounded KMSOBJ diagnostics for the
exact CRTC construction inputs (mixer count, SSPP count, primary-plane choices
and CRTC attempts), but Phase267/268 recorder admission filtered them. Reuse
those existing call sites rather than modifying SDE again.

Phase269 is observation-only:
- admit/latch existing KMSOBJ topology records before recorder capacity checks;
- add low-volume DRM core observations at successful drm_mode_getresources()
  and drm_mode_getplane_res() exits;
- retain only compact P269 A/B/C snapshots as critical late evidence.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RECORDER = Path("drivers/a52_secure/a52_ack_secure_flight_recorder.c")
DRM_DIR = Path("drivers/gpu/drm")
PHASE268 = "A52_PHASE268_COMPOSER_DRM_STICKY_V1"
MARKER = "A52_PHASE269_CRTC_TOPOLOGY_STICKY_V1"
DRM_MARKER = "A52_PHASE269_DRM_RESOURCE_TRACE_V1"
HEADER = "#include <linux/a52_ack_secure_flight_recorder.h>"


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


CRIT_OLD = '''if (!strncmp(message, "DRMPOST 211", 11) ||
    !strncmp(message, "DRMPOST 212", 11))
\treturn false;
return !strncmp(message, "P268 ", 5) ||
'''
CRIT_NEW = '''if (!strncmp(message, "DRMPOST 211", 11) ||
    !strncmp(message, "DRMPOST 212", 11) ||
    !strncmp(message, "KMSOBJ ", 7) ||
    !strncmp(message, "DRMRES 269 ", 11))
\treturn false;
return !strncmp(message, "P269 ", 5) ||
       !strncmp(message, "P268 ", 5) ||
'''

ADMIT_OLD = '''if (strncmp(fmt, "P268", 4) &&
    strncmp(fmt, "DRMPOST 211", 11) &&
'''
ADMIT_NEW = '''if (strncmp(fmt, "P269", 4) &&
    strncmp(fmt, "KMSOBJ", 6) &&
    strncmp(fmt, "DRMRES 269", 10) &&
    strncmp(fmt, "P268", 4) &&
    strncmp(fmt, "DRMPOST 211", 11) &&
'''

STATE_ANCHOR = '''static int a52_r268_path_kind_of(const char *message)
'''
STATE_NEW = r'''/* A52_PHASE269_CRTC_TOPOLOGY_STICKY_V1
 * Reuse the already-compiled KMSOBJ topology stream and low-volume DRM core
 * resource results. No SDE/DRM return value, object state or ordering changes.
 */
static atomic_t a52_r269_topo_seen = ATOMIC_INIT(0);
static atomic_t a52_r269_mixers = ATOMIC_INIT(-1);
static atomic_t a52_r269_sspp = ATOMIC_INIT(-1);
static atomic_t a52_r269_max_crtc = ATOMIC_INIT(-1);
static atomic_t a52_r269_enc = ATOMIC_INIT(-1);
static atomic_t a52_r269_conn = ATOMIC_INIT(-1);
static atomic_t a52_r269_plane_seen = ATOMIC_INIT(0);
static atomic_t a52_r269_primary = ATOMIC_INIT(0);
static atomic_t a52_r269_last_plane = ATOMIC_INIT(-1);
static atomic_t a52_r269_crtc_enter = ATOMIC_INIT(0);
static atomic_t a52_r269_crtc_exit = ATOMIC_INIT(0);
static atomic_t a52_r269_crtc_rc = ATOMIC_INIT(-61);

static atomic_t a52_r269_res_count = ATOMIC_INIT(0);
static atomic_t a52_r269_res_fbs = ATOMIC_INIT(-1);
static atomic_t a52_r269_res_crtcs = ATOMIC_INIT(-1);
static atomic_t a52_r269_res_enc = ATOMIC_INIT(-1);
static atomic_t a52_r269_res_conn = ATOMIC_INIT(-1);
static atomic_t a52_r269_plane_res_count = ATOMIC_INIT(0);
static atomic_t a52_r269_plane_count = ATOMIC_INIT(-1);

static void a52_r269_track_topology(const char *message)
{
\tint primary;

\tif (!message)
\t\treturn;

\tif (!strncmp(message, "KMSOBJ counts ", 14)) {
\t\tatomic_inc(&a52_r269_topo_seen);
\t\tatomic_set(&a52_r269_mixers, a52_r228_dec(message, "mixers=", -1));
\t\tatomic_set(&a52_r269_sspp, a52_r228_dec(message, "sspp=", -1));
\t\tatomic_set(&a52_r269_max_crtc, a52_r228_dec(message, "max-crtc=", -1));
\t\tatomic_set(&a52_r269_enc, a52_r228_dec(message, "enc=", -1));
\t\tatomic_set(&a52_r269_conn, a52_r228_dec(message, "conn=", -1));
\t\treturn;
\t}

\tif (!strncmp(message, "KMSOBJ plane enter ", 19)) {
\t\tatomic_inc(&a52_r269_plane_seen);
\t\tatomic_set(&a52_r269_last_plane, a52_r228_dec(message, "i=", -1));
\t\tprimary = a52_r228_dec(message, "primary=", 0);
\t\tif (primary > 0)
\t\t\tatomic_inc(&a52_r269_primary);
\t\treturn;
\t}

\tif (!strncmp(message, "KMSOBJ crtc enter ", 18)) {
\t\tatomic_inc(&a52_r269_crtc_enter);
\t\treturn;
\t}
\tif (!strncmp(message, "KMSOBJ crtc exit ", 17)) {
\t\tatomic_inc(&a52_r269_crtc_exit);
\t\tatomic_set(&a52_r269_crtc_rc, a52_r228_dec(message, "rc=", -61));
\t\treturn;
\t}

\tif (!strncmp(message, "DRMRES 269 res ", 15) &&
\t    a52_r268_entry_is_composer(message)) {
\t\tatomic_inc(&a52_r269_res_count);
\t\tatomic_set(&a52_r269_res_fbs, a52_r228_dec(message, "f=", -1));
\t\tatomic_set(&a52_r269_res_crtcs, a52_r228_dec(message, "c=", -1));
\t\tatomic_set(&a52_r269_res_enc, a52_r228_dec(message, "e=", -1));
\t\tatomic_set(&a52_r269_res_conn, a52_r228_dec(message, "n=", -1));
\t\treturn;
\t}

\tif (!strncmp(message, "DRMRES 269 plane ", 17) &&
\t    a52_r268_entry_is_composer(message)) {
\t\tatomic_inc(&a52_r269_plane_res_count);
\t\tatomic_set(&a52_r269_plane_count, a52_r228_dec(message, "n=", -1));
\t}
}

static int a52_r268_path_kind_of(const char *message)
'''

TRACK_OLD = '''\ta52_r267_track_display(message);\n\ta52_r268_track_drm(message);\n'''
TRACK_NEW = '''\ta52_r267_track_display(message);\n\ta52_r268_track_drm(message);\n\ta52_r269_track_topology(message);\n'''

SNAP_ANCHOR = '''static int a52_r228_clip(int value)
'''
SNAP_NEW = r'''static void a52_r269_snapshot(unsigned int tick)
{
\tif (!(tick == 120U || tick == 150U || tick == 160U ||
\t      tick == 170U || tick == 180U))
\t\treturn;

\ta52_ackfr_record("P269 A t=%u ts=%d mx=%d ss=%d mc=%d en=%d co=%d",
\t\ttick, atomic_read(&a52_r269_topo_seen), atomic_read(&a52_r269_mixers),
\t\tatomic_read(&a52_r269_sspp), atomic_read(&a52_r269_max_crtc),
\t\tatomic_read(&a52_r269_enc), atomic_read(&a52_r269_conn));
\ta52_ackfr_record("P269 B t=%u ps=%d pp=%d li=%d ce=%d cx=%d cr=%d",
\t\ttick, atomic_read(&a52_r269_plane_seen), atomic_read(&a52_r269_primary),
\t\tatomic_read(&a52_r269_last_plane), atomic_read(&a52_r269_crtc_enter),
\t\tatomic_read(&a52_r269_crtc_exit), atomic_read(&a52_r269_crtc_rc));
\ta52_ackfr_record("P269 C t=%u rr=%d f=%d c=%d e=%d n=%d rp=%d pc=%d",
\t\ttick, atomic_read(&a52_r269_res_count), atomic_read(&a52_r269_res_fbs),
\t\tatomic_read(&a52_r269_res_crtcs), atomic_read(&a52_r269_res_enc),
\t\tatomic_read(&a52_r269_res_conn), atomic_read(&a52_r269_plane_res_count),
\t\tatomic_read(&a52_r269_plane_count));
}

static int a52_r228_clip(int value)
'''

HEARTBEAT_OLD = '''\ta52_r267_display_snapshot(tick);\n\ta52_r268_snapshot(tick);\n'''
HEARTBEAT_NEW = '''\ta52_r267_display_snapshot(tick);\n\ta52_r268_snapshot(tick);\n\ta52_r269_snapshot(tick);\n'''


def validate_recorder(text: str, label: str) -> None:
    for token in (
        MARKER,
        '!strncmp(message, "P269 ", 5)',
        '!strncmp(message, "KMSOBJ ", 7)',
        '!strncmp(message, "DRMRES 269 ", 11)',
        'strncmp(fmt, "P269", 4)',
        'strncmp(fmt, "KMSOBJ", 6)',
        'strncmp(fmt, "DRMRES 269", 10)',
        'a52_r269_track_topology(message);',
        'P269 A t=%u ts=%d mx=%d ss=%d mc=%d en=%d co=%d',
        'P269 B t=%u ps=%d pp=%d li=%d ce=%d cx=%d cr=%d',
        'P269 C t=%u rr=%d f=%d c=%d e=%d n=%d rp=%d pc=%d',
        'a52_r269_snapshot(tick);',
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def patch_recorder(text: str, label: str) -> str:
    if MARKER in text:
        validate_recorder(text, label)
        return text
    if PHASE268 not in text:
        raise RuntimeError(f"{label}: Phase268 base missing")
    text = one(text, CRIT_OLD, CRIT_NEW, f"{label}: critical policy")
    text = one(text, ADMIT_OLD, ADMIT_NEW, f"{label}: admission")
    text = one(text, STATE_ANCHOR, STATE_NEW, f"{label}: topology state")
    text = one(text, TRACK_OLD, TRACK_NEW, f"{label}: tracker call")
    text = one(text, SNAP_ANCHOR, SNAP_NEW, f"{label}: snapshot")
    text = one(text, HEARTBEAT_OLD, HEARTBEAT_NEW, f"{label}: heartbeat")
    validate_recorder(text, label)
    return text


def function_span(text: str, name: str, label: str) -> tuple[int, int]:
    match = re.search(r"\bint\s+" + re.escape(name) + r"\s*\(", text)
    if not match:
        raise RuntimeError(f"{label}: function {name} not found")
    open_brace = text.find("{", match.end())
    if open_brace < 0:
        raise RuntimeError(f"{label}: opening brace for {name} not found")

    depth = 0
    i = open_brace
    state = "code"
    quote = ""
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "code":
            if ch == "/" and nxt == "*":
                state = "block"
                i += 2
                continue
            if ch == "/" and nxt == "/":
                state = "line"
                i += 2
                continue
            if ch in ('"', "'"):
                state = "string"
                quote = ch
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return match.start(), i + 1
        elif state == "block":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
        elif state == "line":
            if ch == "\n":
                state = "code"
        elif state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                state = "code"
        i += 1
    raise RuntimeError(f"{label}: unterminated function {name}")


def add_header(text: str, label: str) -> str:
    if HEADER in text:
        return text
    match = re.search(r"^#include <linux/[^>]+>\n", text, re.M)
    if not match:
        raise RuntimeError(f"{label}: no linux include anchor")
    return text[:match.end()] + HEADER + "\n" + text[match.end():]


def inject_success_record(text: str, name: str, statement: str, label: str) -> str:
    if DRM_MARKER in text and statement in text:
        return text
    start, end = function_span(text, name, label)
    body = text[start:end]
    returns = list(re.finditer(r"(?m)^\treturn\s+(?:ret|0);\s*$", body))
    if not returns:
        raise RuntimeError(f"{label}: no final success return in {name}")
    pos = start + returns[-1].start()
    insert = (
        f"\t/* {DRM_MARKER}: successful userspace resource view. */\n"
        f"\t{statement}\n"
    )
    return text[:pos] + insert + text[pos:]


def locate_function(root: Path, name: str) -> Path:
    hits = []
    for path in sorted((root / DRM_DIR).glob("*.c")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(r"\bint\s+" + re.escape(name) + r"\s*\(", text):
            hits.append(path)
    if len(hits) != 1:
        raise RuntimeError(f"expected one {name} implementation, found {hits}")
    return hits[0]


def patch_drm_resources(root: Path) -> list[Path]:
    specs = [
        (
            "drm_mode_getresources",
            'a52_ackfr_record("DRMRES 269 res g=%d p=%d f=%u c=%u e=%u n=%u", '
            'current->tgid, current->pid, card_res->count_fbs, card_res->count_crtcs, '
            'card_res->count_encoders, card_res->count_connectors);',
        ),
        (
            "drm_mode_getplane_res",
            'a52_ackfr_record("DRMRES 269 plane g=%d p=%d n=%u", '
            'current->tgid, current->pid, plane_resp->count_planes);',
        ),
    ]
    touched: list[Path] = []
    for name, statement in specs:
        path = locate_function(root, name)
        text = path.read_text(encoding="utf-8")
        text = add_header(text, str(path))
        text = inject_success_record(text, name, statement, str(path))
        path.write_text(text, encoding="utf-8")
        if path not in touched:
            touched.append(path)
    return touched


def locate(args: list[str]) -> Path:
    candidates: list[Path] = []
    for arg in args:
        if arg.startswith("-"):
            continue
        path = Path(arg)
        candidates.extend((path, path.parent))
    candidates.extend((Path.cwd() / "gki/common", Path.cwd() / "workspace/gki-phase199-src"))
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidates:
        root = root.resolve(strict=False)
        if root in seen:
            continue
        seen.add(root)
        recorder = root / RECORDER
        if recorder.is_file() and PHASE268 in recorder.read_text(encoding="utf-8"):
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(f"expected one generated Phase268 root, found {len(hits)}: {hits}")
    return hits[0]


def self_test() -> None:
    fixture = '''#include <linux/uaccess.h>\n\nint drm_mode_getresources(struct drm_device *dev, void *data, struct drm_file *file_priv)\n{\n\tstruct drm_mode_card_res *card_res = data;\n\tif (!dev)\n\t\treturn -1;\n\treturn ret;\n}\n\nint drm_mode_getplane_res(struct drm_device *dev, void *data, struct drm_file *file_priv)\n{\n\tstruct drm_mode_get_plane_res *plane_resp = data;\n\tif (!dev)\n\t\treturn -1;\n\treturn 0;\n}\n'''
    fixture = add_header(fixture, "fixture")
    fixture = inject_success_record(
        fixture,
        "drm_mode_getresources",
        'a52_ackfr_record("DRMRES 269 res g=%d p=%d f=%u c=%u e=%u n=%u", current->tgid, current->pid, card_res->count_fbs, card_res->count_crtcs, card_res->count_encoders, card_res->count_connectors);',
        "fixture",
    )
    fixture = inject_success_record(
        fixture,
        "drm_mode_getplane_res",
        'a52_ackfr_record("DRMRES 269 plane g=%d p=%d n=%u", current->tgid, current->pid, plane_resp->count_planes);',
        "fixture",
    )
    assert HEADER in fixture
    assert fixture.count(DRM_MARKER) == 2
    assert "DRMRES 269 res " in fixture and "DRMRES 269 plane " in fixture
    print("Phase269 CRTC topology/resource self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    recorder = root / RECORDER
    recorder.write_text(
        patch_recorder(recorder.read_text(encoding="utf-8"), str(recorder)),
        encoding="utf-8",
    )
    touched = patch_drm_resources(root)
    print(f"{MARKER}: recorder sticky topology applied", flush=True)
    for path in touched:
        print(f"{DRM_MARKER}: patched {path.relative_to(root)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
