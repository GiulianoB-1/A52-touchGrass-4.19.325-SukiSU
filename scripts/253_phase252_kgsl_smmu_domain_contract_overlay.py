#!/usr/bin/env python3
"""Phase254 chain entrypoint: apply the exact Phase253 contract, then Phase254.

The inherited Phase252 semantic guard already dispatches this historical
filename. Keeping that entrypoint avoids changing the hardware-proven Phase252
ordering while preserving the original Phase253 implementation byte-for-byte in
253_phase252_kgsl_smmu_domain_contract_base.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "253_phase252_kgsl_smmu_domain_contract_base.py"
PHASE254 = HERE / "254_phase253_kgsl_smmu_pinned_cb_handoff_overlay.py"

# Retain Phase253 workflow/source gates on the inherited entrypoint.
PHASE253_CONTRACT_TOKENS = (
    "A52_PHASE253_KGSL_SMMU_DOMAIN_CONTRACT_V1",
    "DOMAIN_ATTR_PROCID",
    "DOMAIN_ATTR_DYNAMIC",
    "DOMAIN_ATTR_CONTEXT_BANK",
    "DOMAIN_ATTR_TTBR0",
    "DOMAIN_ATTR_CONTEXTIDR",
)


def run(script: Path, args: list[str]) -> None:
    if not script.is_file():
        raise RuntimeError(f"missing chained overlay: {script}")
    result = subprocess.run([sys.executable, str(script), *args], check=False)
    if result.returncode:
        raise RuntimeError(f"overlay failed: {script.name} rc={result.returncode}")


def main() -> int:
    args = sys.argv[1:]
    run(BASE, args)
    run(PHASE254, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
