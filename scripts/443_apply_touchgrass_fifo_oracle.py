#!/usr/bin/env python3
from __future__ import annotations

import base64
import sys
import zlib
from pathlib import Path

payload_path = Path(__file__).with_name(Path(__file__).name + ".z64")
payload = base64.b64decode(payload_path.read_text().strip())
source = zlib.decompress(payload).decode("utf-8")

WAIT = (
    "\tret = wait_for_completion_timeout(\n"
    "\t\t\t&dsi_ctrl->irq_info.cmd_dma_done,\n"
    "\t\t\tmsecs_to_jiffies(DSI_CTRL_TX_TO_MS));\n"
)
TIMEOUT = "\tif (ret == 0 && !atomic_read(&dsi_ctrl->dma_irq_trig)) {\n"
FUNC = "static void dsi_ctrl_dma_cmd_wait_for_done(struct work_struct *work)\n"
MARK = "A52_PHASE443_TOUCHGRASS_FIFO_ORACLE_V1"


def _arg_value(name: str) -> str | None:
    try:
        i = sys.argv.index(name)
    except ValueError:
        return None
    if i + 1 >= len(sys.argv):
        return None
    return sys.argv[i + 1]


def _resolve_ctrl(root: Path) -> Path:
    candidates = (
        root / "dsi_ctrl.c",
        root / "techpack/display/msm/dsi/dsi_ctrl.c",
        root / "drivers/a52_display/msm/dsi/dsi_ctrl.c",
    )
    matches = [p for p in candidates if p.is_file()]
    if len(matches) != 1:
        raise SystemExit(
            "Phase443 wrapper: expected exactly one dsi_ctrl.c under " +
            str(root) + ", found " + str(len(matches))
        )
    return matches[0]


def _function_bounds(text: str) -> tuple[int, int]:
    start = text.find(FUNC)
    if start < 0:
        raise SystemExit("Phase443 wrapper: dsi_ctrl_dma_cmd_wait_for_done missing")
    brace = text.find("{", start)
    if brace < 0:
        raise SystemExit("Phase443 wrapper: timeout function opening brace missing")
    depth = 0
    for pos in range(brace, len(text)):
        ch = text[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return start, pos + 1
    raise SystemExit("Phase443 wrapper: timeout function closing brace missing")


def _strip_golden_gap(path: Path) -> str | None:
    text = path.read_text(errors="replace")
    if MARK in text:
        return None

    a, b = _function_bounds(text)
    fn = text[a:b]
    w = fn.find(WAIT)
    if w < 0:
        raise SystemExit("Phase443 wrapper: DMA wait anchor missing")
    after_wait = w + len(WAIT)
    t = fn.find(TIMEOUT, after_wait)
    if t < 0:
        raise SystemExit("Phase443 wrapper: Samsung timeout branch missing")

    gap = fn[after_wait:t]
    if not gap:
        return None

    # The proven Golden lineage inserts its q2/qDONE observer here.  Phase443's
    # original patcher expects WAIT immediately followed by TIMEOUT.  Remove
    # only that known observer gap for the duration of the patch operation.
    required = (
        "a52_g315_armed(dsi_ctrl)",
        "a52_g344_snapshot(&dsi_ctrl->hw, 9)",
        "a52_g439_dump_samples",
        "a52_g424_dump_snapshot",
    )
    missing = [token for token in required if token not in gap]
    if missing:
        raise SystemExit(
            "Phase443 wrapper: unexpected code between wait and timeout: " +
            ", ".join(missing)
        )

    fn = fn[:after_wait] + fn[t:]
    path.write_text(text[:a] + fn + text[b:])
    return gap


def _restore_golden_gap(path: Path, gap: str | None) -> None:
    if not gap:
        return
    text = path.read_text(errors="replace")
    a, b = _function_bounds(text)
    fn = text[a:b]

    if gap in fn:
        return

    w = fn.find(WAIT)
    if w < 0:
        raise SystemExit("Phase443 wrapper: patched DMA wait anchor missing")
    after_wait = w + len(WAIT)

    # Phase443 inserts its active experiment block after WAIT and leaves the
    # inherited Samsung timeout branch below it.  Reinsert the Golden observer
    # immediately before that inherited branch.  Phase443's goto done therefore
    # skips the old Golden q2 dump only for the targeted oracle transaction,
    # while every normal command retains the proven Golden behavior.
    t = fn.find(TIMEOUT, after_wait)
    if t < 0:
        raise SystemExit("Phase443 wrapper: patched Samsung timeout branch missing")

    fn = fn[:t] + gap + fn[t:]
    path.write_text(text[:a] + fn + text[b:])


def _run_payload() -> None:
    exec(
        compile(source, str(Path(__file__).with_suffix(".expanded.py")), "exec"),
        globals(),
    )


# --help and --check-only must be side-effect free.  In particular, CI calls
# --help before any Golden workspace exists, so never inspect source files then.
root_arg = _arg_value("--root")
apply_mode = (
    root_arg is not None
    and "--check-only" not in sys.argv
    and "--help" not in sys.argv
    and "-h" not in sys.argv
)

ctrl_path: Path | None = None
golden_gap: str | None = None
if apply_mode:
    ctrl_path = _resolve_ctrl(Path(root_arg).resolve())
    golden_gap = _strip_golden_gap(ctrl_path)

try:
    _run_payload()
except SystemExit:
    if ctrl_path is not None:
        _restore_golden_gap(ctrl_path, golden_gap)
    raise
except BaseException:
    if ctrl_path is not None:
        _restore_golden_gap(ctrl_path, golden_gap)
    raise
else:
    if ctrl_path is not None:
        _restore_golden_gap(ctrl_path, golden_gap)
