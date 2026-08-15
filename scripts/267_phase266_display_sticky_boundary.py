#!/usr/bin/env python3
"""Phase267S retention wrapper for the direct pre-DRM diagnostic.

Keeps the exact Phase267S display-lifecycle admission and direct P267 call-site
instrumentation in the frozen base script. This wrapper only adds compact
sticky state for early DISPINIT/P267 records and re-emits it at late heartbeat
checkpoints, so early display evidence survives ramoops ring overwrite.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BASE_MARKER = "A52_PHASE267_PREDRM_DIRECT_BOUNDARY_V3"
ADMISSION_MARKER = "A52_PHASE267S_DISPLAY_LIFECYCLE_ADMISSION_V1"
RETENTION_MARKER = "A52_PHASE267_PREDRM_STICKY_RETENTION_V1"
BASE_PATH = Path(__file__).with_name("267_phase266_display_sticky_boundary_base.py")


def load_base():
    spec = importlib.util.spec_from_file_location("a52_phase267_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase267S base: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base()
base_patch_recorder = base.patch_recorder

RETENTION_DECL_ANCHOR = """static void a52_r228_track_message(const char *message)\n"""
RETENTION_DECL_NEW = r"""/* A52_PHASE267_PREDRM_STICKY_RETENTION_V1
 * Phase267R hardware showed that direct P267 records can execute before the
 * retained ramoops window and later be overwritten. Latch only compact
 * display-init / P267 state and re-emit it during the late heartbeat window.
 * Observation only: no display, DRM, probe, ordering or return-value change.
 */
static atomic_t a52_r267_reg_stage = ATOMIC_INIT(0);
static atomic_t a52_r267_reg_rc = ATOMIC_INIT(0);
static atomic_t a52_r267_modeset = ATOMIC_INIT(-1);
static atomic_t a52_r267_plat_stage = ATOMIC_INIT(0);
static atomic_t a52_r267_plat_rc = ATOMIC_INIT(0);
static atomic_t a52_r267_dsi_done = ATOMIC_INIT(0);
static atomic_t a52_r267_smmu_done = ATOMIC_INIT(0);
static atomic_t a52_r267_probe_count = ATOMIC_INIT(0);
static atomic_t a52_r267_probe_sde = ATOMIC_INIT(-1);
static atomic_t a52_r267_probe_mdss = ATOMIC_INIT(-1);
static atomic_t a52_r267_bus_stage = ATOMIC_INIT(0);
static atomic_t a52_r267_bus_n = ATOMIC_INIT(-1);
static atomic_t a52_r267_blocks_stage = ATOMIC_INIT(0);
static atomic_t a52_r267_blocks_rc = ATOMIC_INIT(-61);
static atomic_t a52_r267_blocks_c = ATOMIC_INIT(-1);
static atomic_t a52_r267_blocks_e = ATOMIC_INIT(-1);
static atomic_t a52_r267_blocks_n = ATOMIC_INIT(-1);
static atomic_t a52_r267_blocks_p = ATOMIC_INIT(-1);
static atomic_t a52_r267_obj_stage = ATOMIC_INIT(0);
static atomic_t a52_r267_obj_rc = ATOMIC_INIT(-61);
static atomic_t a52_r267_obj_c = ATOMIC_INIT(-1);
static atomic_t a52_r267_obj_e = ATOMIC_INIT(-1);
static atomic_t a52_r267_obj_n = ATOMIC_INIT(-1);
static atomic_t a52_r267_obj_p = ATOMIC_INIT(-1);
static atomic_t a52_r267_node_count = ATOMIC_INIT(0);
static atomic_t a52_r267_node_type = ATOMIC_INIT(-1);
static atomic_t a52_r267_node_idx = ATOMIC_INIT(-1);
static atomic_t a52_r267_node_rc = ATOMIC_INIT(-61);
static atomic_t a52_r267_add_count = ATOMIC_INIT(0);
static atomic_t a52_r267_add_type = ATOMIC_INIT(-1);
static atomic_t a52_r267_add_idx = ATOMIC_INIT(-1);
static atomic_t a52_r267_add_rc = ATOMIC_INIT(-61);

static void a52_r267_track_display(const char *message)
{
	if (!message)
		return;

	if (!strncmp(message, "DISPINIT register enter", 23)) {
		atomic_set(&a52_r267_reg_stage, 1);
		atomic_set(&a52_r267_modeset,
			a52_r228_dec(message, "modeset=", atomic_read(&a52_r267_modeset)));
	} else if (!strncmp(message, "DISPINIT register disabled", 26)) {
		atomic_set(&a52_r267_reg_stage, 2);
		atomic_set(&a52_r267_reg_rc,
			a52_r228_dec(message, "rc=", atomic_read(&a52_r267_reg_rc)));
	}
	if (!strncmp(message, "DISPINIT platform-register enter", 32))
		atomic_set(&a52_r267_plat_stage, 1);
	else if (!strncmp(message, "DISPINIT platform-register exit", 31)) {
		atomic_set(&a52_r267_plat_stage, 2);
		atomic_set(&a52_r267_plat_rc,
			a52_r228_dec(message, "rc=", atomic_read(&a52_r267_plat_rc)));
	}
	if (!strncmp(message, "DISPINIT dsi-register done", 26))
		atomic_set(&a52_r267_dsi_done, 1);
	if (!strncmp(message, "DISPINIT smmu-register done", 27))
		atomic_set(&a52_r267_smmu_done, 1);
	if (!strncmp(message, "DISPINIT probe enter", 20)) {
		atomic_inc(&a52_r267_probe_count);
		atomic_set(&a52_r267_probe_sde,
			a52_r228_dec(message, "sde=", atomic_read(&a52_r267_probe_sde)));
		atomic_set(&a52_r267_probe_mdss,
			a52_r228_dec(message, "mdss=", atomic_read(&a52_r267_probe_mdss)));
	}

	if (!strncmp(message, "P267 bus-enter", 14)) {
		atomic_set(&a52_r267_bus_stage, 1);
		atomic_set(&a52_r267_bus_n,
			a52_r228_dec(message, "n=", atomic_read(&a52_r267_bus_n)));
	} else if (!strncmp(message, "P267 bus-exit", 13)) {
		atomic_set(&a52_r267_bus_stage, 2);
		atomic_set(&a52_r267_bus_n,
			a52_r228_dec(message, "n=", atomic_read(&a52_r267_bus_n)));
	} else if (!strncmp(message, "P267 blocks-enter", 17)) {
		atomic_set(&a52_r267_blocks_stage, 1);
	} else if (!strncmp(message, "P267 blocks-exit", 16)) {
		atomic_set(&a52_r267_blocks_stage, 2);
		atomic_set(&a52_r267_blocks_rc, a52_r228_dec(message, "rc=", -61));
		atomic_set(&a52_r267_blocks_c, a52_r228_dec(message, "c=", -1));
		atomic_set(&a52_r267_blocks_e, a52_r228_dec(message, "e=", -1));
		atomic_set(&a52_r267_blocks_n, a52_r228_dec(message, "n=", -1));
		atomic_set(&a52_r267_blocks_p, a52_r228_dec(message, "p=", -1));
	} else if (!strncmp(message, "P267 drm-obj-enter", 18)) {
		atomic_set(&a52_r267_obj_stage, 1);
	} else if (!strncmp(message, "P267 drm-obj-exit", 17)) {
		atomic_set(&a52_r267_obj_stage, 2);
		atomic_set(&a52_r267_obj_rc, a52_r228_dec(message, "rc=", -61));
		atomic_set(&a52_r267_obj_c, a52_r228_dec(message, "c=", -1));
		atomic_set(&a52_r267_obj_e, a52_r228_dec(message, "e=", -1));
		atomic_set(&a52_r267_obj_n, a52_r228_dec(message, "n=", -1));
		atomic_set(&a52_r267_obj_p, a52_r228_dec(message, "p=", -1));
	} else if (!strncmp(message, "P267 node-add", 13)) {
		atomic_inc(&a52_r267_add_count);
		atomic_set(&a52_r267_add_type, a52_r228_dec(message, "type=", -1));
		atomic_set(&a52_r267_add_idx, a52_r228_dec(message, "idx=", -1));
		atomic_set(&a52_r267_add_rc, a52_r228_dec(message, "rc=", -61));
	} else if (!strncmp(message, "P267 node ", 10)) {
		atomic_inc(&a52_r267_node_count);
		atomic_set(&a52_r267_node_type, a52_r228_dec(message, "type=", -1));
		atomic_set(&a52_r267_node_idx, a52_r228_dec(message, "idx=", -1));
		atomic_set(&a52_r267_node_rc, a52_r228_dec(message, "rc=", -61));
	}
}

static void a52_r228_track_message(const char *message)
"""

RETENTION_TRACK_OLD = """\tif (!message || !strncmp(message, "TRIPOST ", 8))\n\t\treturn;\n"""
RETENTION_TRACK_NEW = """\tif (!message)\n\t\treturn;\n\ta52_r267_track_display(message);\n\tif (!strncmp(message, "TRIPOST ", 8))\n\t\treturn;\n"""

RETENTION_SNAPSHOT_ANCHOR = """static int a52_r228_clip(int value)\n"""
RETENTION_SNAPSHOT_NEW = r"""static bool a52_r267_snapshot_tick(unsigned int tick)
{
	return tick == 120U || tick == 150U || tick == 160U ||
	       tick == 170U || tick == 180U;
}

static void a52_r267_display_snapshot(unsigned int tick)
{
	if (!a52_r267_snapshot_tick(tick))
		return;

	a52_ackfr_record("P267 A t=%u rg=%d/%d ms=%d pl=%d/%d ds=%d sm=%d pr=%d/%d/%d",
		tick, atomic_read(&a52_r267_reg_stage), atomic_read(&a52_r267_reg_rc),
		atomic_read(&a52_r267_modeset), atomic_read(&a52_r267_plat_stage),
		atomic_read(&a52_r267_plat_rc), atomic_read(&a52_r267_dsi_done),
		atomic_read(&a52_r267_smmu_done), atomic_read(&a52_r267_probe_count),
		atomic_read(&a52_r267_probe_sde), atomic_read(&a52_r267_probe_mdss));
	a52_ackfr_record("P267 B t=%u bu=%d/%d bl=%d/%d ob=%d/%d nd=%d/%d/%d ad=%d/%d/%d",
		tick, atomic_read(&a52_r267_bus_stage), atomic_read(&a52_r267_bus_n),
		atomic_read(&a52_r267_blocks_stage), atomic_read(&a52_r267_blocks_rc),
		atomic_read(&a52_r267_obj_stage), atomic_read(&a52_r267_obj_rc),
		atomic_read(&a52_r267_node_count), atomic_read(&a52_r267_node_idx),
		atomic_read(&a52_r267_node_rc), atomic_read(&a52_r267_add_count),
		atomic_read(&a52_r267_add_idx), atomic_read(&a52_r267_add_rc));
	a52_ackfr_record("P267 C t=%u bc=%d,%d,%d,%d oc=%d,%d,%d,%d nt=%d at=%d",
		tick, atomic_read(&a52_r267_blocks_c), atomic_read(&a52_r267_blocks_e),
		atomic_read(&a52_r267_blocks_n), atomic_read(&a52_r267_blocks_p),
		atomic_read(&a52_r267_obj_c), atomic_read(&a52_r267_obj_e),
		atomic_read(&a52_r267_obj_n), atomic_read(&a52_r267_obj_p),
		atomic_read(&a52_r267_node_type), atomic_read(&a52_r267_add_type));
}

static int a52_r228_clip(int value)
"""

RETENTION_HEARTBEAT_OLD = """\ta52_r228_tripost_snapshot(tick);\n\t/* A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1: no Phase 242 heartbeat snapshot */\n"""
RETENTION_HEARTBEAT_NEW = """\ta52_r228_tripost_snapshot(tick);\n\ta52_r267_display_snapshot(tick);\n\t/* A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1: no Phase 242 heartbeat snapshot */\n"""


def one(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def validate_retention(text: str, label: str) -> None:
    for token in (
        RETENTION_MARKER,
        "a52_r267_track_display(message);",
        "P267 A t=%u rg=%d/%d ms=%d pl=%d/%d ds=%d sm=%d pr=%d/%d/%d",
        "P267 B t=%u bu=%d/%d bl=%d/%d ob=%d/%d nd=%d/%d/%d ad=%d/%d/%d",
        "P267 C t=%u bc=%d,%d,%d,%d oc=%d,%d,%d,%d nt=%d at=%d",
        "a52_r267_display_snapshot(tick);",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing {token}")


def patch_recorder(text: str, label: str) -> str:
    text = base_patch_recorder(text, label)
    if RETENTION_MARKER not in text:
        text = one(text, RETENTION_DECL_ANCHOR, RETENTION_DECL_NEW,
                   f"{label}: sticky declarations")
        text = one(text, RETENTION_TRACK_OLD, RETENTION_TRACK_NEW,
                   f"{label}: sticky tracker call")
        text = one(text, RETENTION_SNAPSHOT_ANCHOR, RETENTION_SNAPSHOT_NEW,
                   f"{label}: sticky snapshot")
        text = one(text, RETENTION_HEARTBEAT_OLD, RETENTION_HEARTBEAT_NEW,
                   f"{label}: heartbeat snapshot")
    validate_retention(text, label)
    return text


def retention_self_test() -> None:
    rec = ('A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1\n' +
           base.CRIT_OLD + base.ADMIT_OLD +
           'static void a52_r228_track_message(const char *message)\n{\n' +
           RETENTION_TRACK_OLD + '}\n' +
           'static int a52_r228_clip(int value)\n{ return value; }\n' +
           'void heartbeat(void)\n{\n\tunsigned int tick = 0;\n'
           '\ta52_r228_tripost_snapshot(tick);\n'
           '\t/* A52_PHASE243_PHASE242_RUNTIME_DISABLED_V1: no Phase 242 heartbeat snapshot */\n}\n')
    patched = patch_recorder(rec, "fixture/sticky")
    validate_retention(patched, "fixture/sticky")
    if patch_recorder(patched, "fixture/sticky-idempotent") != patched:
        raise AssertionError("Phase267 sticky retention is not idempotent")


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        base.self_test()
        retention_self_test()
        print("Phase267S sticky-retention wrapper self-test: PASS", flush=True)
        return 0

    base.patch_recorder = patch_recorder
    rc = base.main()
    print(f"{RETENTION_MARKER}: late P267 state retention applied", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
