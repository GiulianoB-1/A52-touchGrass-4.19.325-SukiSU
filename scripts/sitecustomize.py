"""Phase262 hook: apply config parity after the cumulative Phase259/260/261 script."""
from __future__ import annotations

import atexit
import importlib.util
import sys
from pathlib import Path

MARKER = "A52_PHASE262_FW_LOADER_FALLBACK_AB_V1"


def _run() -> None:
    if "--self-test" in sys.argv[1:]:
        return
    here = Path(__file__).resolve().parent
    repo = here.parent
    root = repo / "gki/common"
    adreno = root / "drivers/gpu/msm/adreno.c"
    if not adreno.is_file():
        return
    text = adreno.read_text(encoding="utf-8", errors="replace")
    if "A52_PHASE261_ADRENO_OPEN_V1" not in text:
        return
    phase = here / "262_fw_loader_fallback_ab.py"
    spec = importlib.util.spec_from_file_location("a52_phase262_fw_fallback", phase)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Phase262: {phase}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.apply(root)


if Path(sys.argv[0]).name == "259_compile_repair.py":
    atexit.register(_run)
