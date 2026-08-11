#!/usr/bin/env python3
"""Phase 252 Linux 5.10 compatibility pass for the pinned TouchGrass MSM-bus port.

The hardware-proven TouchGrass implementation is Linux 4.19 code.  Phase 252
imports it verbatim first, then this pass applies only mechanical/API-semantic
adaptations required by the pinned GKI 5.10 tree.  The bus voting algorithms,
DT contract, BCM layout, and KGSL-facing APIs remain unchanged.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = "A52_PHASE252_MSM_BUS_GKI510_COMPAT_V1"
PHASE252 = "A52_PHASE252_LEGACY_MSM_BUS_RPMH_V1"


def locate(args: list[str]) -> Path:
    base = Path.cwd()
    candidates: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = base / p
        candidates.extend((p, p.parent))
    candidates.extend((base / "workspace/gki-phase199-src", base / "gki/common"))

    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidates:
        if not (root / "drivers/gpu/msm/kgsl_gmu.c").is_file():
            continue
        if not (root / "drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c").is_file():
            continue
        kconfig = root / "drivers/soc/qcom/Kconfig"
        if not kconfig.is_file() or "config QCOM_BUS_CONFIG_RPMH" not in kconfig.read_text(encoding="utf-8"):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(f"expected one Phase252 generated gki/common root, found {len(hits)}")
    return hits[0]


def replace_exact(path: Path, old: str, new: str, expected: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(
            f"{path}: expected {expected} copies of compatibility anchor, found {count}: {old[:80]!r}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_bus_find_callbacks(root: Path) -> None:
    board = root / "include/linux/msm-bus-board.h"
    replace_exact(
        board,
        "extern int msm_bus_device_match_adhoc(struct device *dev, void *id);",
        "extern int msm_bus_device_match_adhoc(struct device *dev, const void *id);",
    )

    core = root / "drivers/soc/qcom/msm_bus/msm_bus_core.c"
    replace_exact(
        core,
        "int msm_bus_device_match(struct device *dev, void *id)",
        "int msm_bus_device_match(struct device *dev, const void *id)",
    )
    replace_exact(core, "return fabdev->id == *(int *)id;", "return fabdev->id == *(const int *)id;")

    # Keep both topology variants source-compatible even though RPMh is selected.
    for relative in (
        "drivers/soc/qcom/msm_bus/msm_bus_arb_rpmh.c",
        "drivers/soc/qcom/msm_bus/msm_bus_arb_adhoc.c",
    ):
        path = root / relative
        replace_exact(
            path,
            "int msm_bus_device_match_adhoc(struct device *dev, void *id)",
            "int msm_bus_device_match_adhoc(struct device *dev, const void *id)",
        )
        replace_exact(
            path,
            "ret = (bnode->node_info->id == *(unsigned int *)id);",
            "ret = (bnode->node_info->id == *(const unsigned int *)id);",
        )


def patch_fabric_rpmh(root: Path) -> None:
    path = root / "drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c"
    text = path.read_text(encoding="utf-8")

    # Linux 5.10 tcs.h already owns all BCM_TCS_CMD_* definitions.  Keep the
    # canonical 5.10 implementation rather than shadowing it with the 4.19 copy.
    old_macros = """#define BCM_TCS_CMD_COMMIT_SHFT\t\t30
#define BCM_TCS_CMD_COMMIT_MASK\t\t0x40000000
#define BCM_TCS_CMD_VALID_SHFT\t\t29
#define BCM_TCS_CMD_VALID_MASK\t\t0x20000000
#define BCM_TCS_CMD_VOTE_X_SHFT\t\t14
#define BCM_TCS_CMD_VOTE_MASK\t\t0x3FFF
#define BCM_TCS_CMD_VOTE_Y_SHFT\t\t0
#define BCM_TCS_CMD_VOTE_Y_MASK\t\t0xFFFC000

#define BCM_TCS_CMD(commit, valid, vote_x, vote_y) \\
\t(((commit & 0x1) << BCM_TCS_CMD_COMMIT_SHFT) |\\
\t((valid & 0x1) << BCM_TCS_CMD_VALID_SHFT) |\\
\t((vote_x & BCM_TCS_CMD_VOTE_MASK) << BCM_TCS_CMD_VOTE_X_SHFT) |\\
\t((vote_y & BCM_TCS_CMD_VOTE_MASK) << BCM_TCS_CMD_VOTE_Y_SHFT))
"""
    if text.count(old_macros) != 1:
        raise RuntimeError(f"{path}: legacy BCM TCS macro block drifted")
    text = text.replace(
        old_macros,
        "/* Phase252 GKI 5.10: BCM_TCS_CMD_* is provided by <soc/qcom/tcs.h>. */\n",
    )

    old_invalidate = """\tret = rpmh_invalidate(cur_mbox);
\tif (ret)
\t\tMSM_BUS_ERR(\"%s: Error invalidating mbox: %d\\n\",
\t\t\t\t\t\t__func__, ret);
"""
    if text.count(old_invalidate) != 1:
        raise RuntimeError(f"{path}: rpmh_invalidate 4.19 anchor drifted")
    text = text.replace(
        old_invalidate,
        "\t/* rpmh_invalidate() is void in the pinned GKI 5.10 RPMh API. */\n"
        "\trpmh_invalidate(cur_mbox);\n",
    )

    old_vars = """\tstruct bcm_db aux_data = {0};
\tint ret = 0;
\tint i = 0;
"""
    new_vars = """\tstruct bcm_db aux_data = {0};
\tconst void *aux;
\tsize_t aux_len = 0;
\tint ret = 0;
\tint i = 0;
"""
    if text.count(old_vars) != 1:
        raise RuntimeError(f"{path}: BCM aux variable anchor drifted")
    text = text.replace(old_vars, new_vars)

    old_cmddb = """\tbcmdev->name = pdata->bcmdev->name;
\tif (!cmd_db_read_aux_data_len(bcmdev->name)) {
\t\tMSM_BUS_ERR(\"%s: Error getting bcm info, bcm:%s\",
\t\t\t__func__, bcmdev->name);
\t\tret = -ENXIO;
\t\tgoto exit_bcm_init;
\t}

\tcmd_db_read_aux_data(bcmdev->name, (u8 *)&aux_data,
\t\t\t\t\t\tsizeof(struct bcm_db));
"""
    new_cmddb = """\tbcmdev->name = pdata->bcmdev->name;
\taux = cmd_db_read_aux_data(bcmdev->name, &aux_len);
\tif (IS_ERR(aux)) {
\t\tret = PTR_ERR(aux);
\t\tMSM_BUS_ERR(\"%s: Error getting bcm info, bcm:%s ret:%d\",
\t\t\t__func__, bcmdev->name, ret);
\t\tgoto exit_bcm_init;
\t}
\tif (aux_len < sizeof(aux_data)) {
\t\tMSM_BUS_ERR(\"%s: Partial bcm info, bcm:%s len:%zu\",
\t\t\t__func__, bcmdev->name, aux_len);
\t\tret = -EINVAL;
\t\tgoto exit_bcm_init;
\t}
\tmemcpy(&aux_data, aux, sizeof(aux_data));
"""
    if text.count(old_cmddb) != 1:
        raise RuntimeError(f"{path}: command-db 4.19 anchor drifted")
    text = text.replace(old_cmddb, new_cmddb)

    # Be explicit about memcpy even if transitive kernel headers currently expose it.
    include_anchor = "#include <linux/slab.h>\n"
    if text.count(include_anchor) != 1:
        raise RuntimeError(f"{path}: slab include anchor drifted")
    text = text.replace(include_anchor, include_anchor + "#include <linux/string.h>\n", 1)

    path.write_text(text, encoding="utf-8")


def patch_latency_current_vote(root: Path) -> None:
    path = root / "drivers/soc/qcom/msm_bus/msm_bus_arb_rpmh.c"
    text = path.read_text(encoding="utf-8")
    old = """\tcur_idx = client->curr;
\tclient->curr = idx;
\treq_fal = pdata->usecase_lat[idx].fal_ns;
\treq_idle_time = pdata->usecase_lat[idx].idle_t_ns;
"""
    new = """\tcur_idx = client->curr;
\tclient->curr = idx;
\tif (cur_idx < 0) {
\t\tcur_fal = 0;
\t\tcur_idle_time = 0;
\t} else {
\t\tcur_fal = pdata->usecase_lat[cur_idx].fal_ns;
\t\tcur_idle_time = pdata->usecase_lat[cur_idx].idle_t_ns;
\t}
\treq_fal = pdata->usecase_lat[idx].fal_ns;
\treq_idle_time = pdata->usecase_lat[idx].idle_t_ns;
"""
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: ALC current-vote anchor drifted")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_debug_timekeeping(root: Path) -> None:
    path = root / "drivers/soc/qcom/msm_bus/msm_bus_dbg_rpmh.c"
    text = path.read_text(encoding="utf-8")

    count = text.count("struct timespec ts;")
    if count != 3:
        raise RuntimeError(f"{path}: expected 3 legacy timespec declarations, found {count}")
    text = text.replace("struct timespec ts;", "struct timespec64 ts;")

    count = text.count("ktime_to_timespec(ktime_get())")
    if count != 3:
        raise RuntimeError(f"{path}: expected 3 legacy ktime conversions, found {count}")
    text = text.replace("ktime_to_timespec(ktime_get())", "ktime_to_timespec64(ktime_get())")

    old_fmt = """i += scnprintf(buf + i, MAX_BUFF_SIZE - i, \"\\n%ld.%09lu\\n\",
\t\tts.tv_sec, ts.tv_nsec);"""
    new_fmt = """i += scnprintf(buf + i, MAX_BUFF_SIZE - i, \"\\n%lld.%09lu\\n\",
\t\t(long long)ts.tv_sec, ts.tv_nsec);"""
    count = text.count(old_fmt)
    if count != 3:
        raise RuntimeError(f"{path}: expected 3 legacy timespec format sites, found {count}")
    text = text.replace(old_fmt, new_fmt)
    path.write_text(text, encoding="utf-8")


def audit(root: Path) -> None:
    bus = root / "drivers/soc/qcom/msm_bus"
    sources = [p for p in bus.rglob("*") if p.suffix in {".c", ".h"}]
    sources += [root / "include/linux/msm-bus-board.h"]

    forbidden = (
        "cmd_db_read_aux_data_len(",
        "ktime_to_timespec(",
        "struct timespec ts;",
        "ret = rpmh_invalidate(",
        "msm_bus_device_match_adhoc(struct device *dev, void *id)",
        "msm_bus_device_match(struct device *dev, void *id)",
    )
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                raise RuntimeError(f"{path}: Phase252 GKI5.10 audit found stale token {token!r}")

    fabric = (bus / "msm_bus_fabric_rpmh.c").read_text(encoding="utf-8")
    for token in (
        "aux = cmd_db_read_aux_data(bcmdev->name, &aux_len);",
        "IS_ERR(aux)",
        "PTR_ERR(aux)",
        "aux_len < sizeof(aux_data)",
        "rpmh_invalidate(cur_mbox);",
        "BCM_TCS_CMD_* is provided by <soc/qcom/tcs.h>",
    ):
        if token not in fabric:
            raise RuntimeError(f"Phase252 fabric compatibility audit missing {token!r}")
    for macro in ("BCM_TCS_CMD_VOTE_MASK", "BCM_TCS_CMD_VOTE_Y_MASK", "BCM_TCS_CMD"):
        if re.search(rf"(?m)^#define {macro}(?:\\s|\\()", fabric):
            raise RuntimeError(f"Phase252 fabric still redefines 5.10 tcs.h macro {macro}")

    arb = (bus / "msm_bus_arb_rpmh.c").read_text(encoding="utf-8")
    for token in (
        "cur_fal = pdata->usecase_lat[cur_idx].fal_ns;",
        "cur_idle_time = pdata->usecase_lat[cur_idx].idle_t_ns;",
        "msm_bus_device_match_adhoc(struct device *dev, const void *id)",
    ):
        if token not in arb:
            raise RuntimeError(f"Phase252 arb compatibility audit missing {token!r}")

    dbg = (bus / "msm_bus_dbg_rpmh.c").read_text(encoding="utf-8")
    if dbg.count("struct timespec64 ts;") != 3 or dbg.count("ktime_to_timespec64(ktime_get())") != 3:
        raise RuntimeError("Phase252 debug timekeeping conversion count mismatch")
    if dbg.count("(long long)ts.tv_sec") != 3:
        raise RuntimeError("Phase252 timespec64 printf conversion count mismatch")

    print(f"{MARKER}: all known TouchGrass-4.19 -> GKI-5.10 MSM-bus API hazards cleared", flush=True)


def self_test() -> None:
    assert "gki/common" in locate.__code__.co_consts
    assert PHASE252 == "A52_PHASE252_LEGACY_MSM_BUS_RPMH_V1"
    # Validate the key semantic choices directly, so workflow preflight catches edits.
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        "const void *id",
        "ktime_to_timespec64",
        "cmd_db_read_aux_data(bcmdev->name, &aux_len)",
        "IS_ERR(aux)",
        "PTR_ERR(aux)",
        "rpmh_invalidate() is void",
        "cur_fal = pdata->usecase_lat[cur_idx].fal_ns",
        "BCM_TCS_CMD_* is provided by <soc/qcom/tcs.h>",
    ):
        if token not in source:
            raise RuntimeError(f"Phase252 GKI5.10 self-test missing {token!r}")
    print("Phase 252 MSM-bus GKI 5.10 compatibility self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    patch_bus_find_callbacks(root)
    patch_fabric_rpmh(root)
    patch_latency_current_vote(root)
    patch_debug_timekeeping(root)
    audit(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
