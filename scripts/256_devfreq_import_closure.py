#!/usr/bin/env python3
"""Phase 256 devfreq import-closure repair.

The Phase256 Adreno TZ/GPUBW governor import depends on Qualcomm downstream
headers that do not exist in the Android 5.10 GKI source, plus the downstream
devfreq trace header.  Earlier phases already provide the compiled legacy SCM
and QTEE compatibility implementations.  This helper stages only missing
TouchGrass declarations/trace definitions from the same pinned golden commit;
it does not replace an existing generated-tree header or add another SCM
implementation.
"""
from __future__ import annotations

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
MARKER = "A52_PHASE256_DEVFREQ_IMPORT_CLOSURE_V1"

DEPENDENCIES = (
    "include/soc/qcom/scm.h",
    "drivers/devfreq/devfreq_trace.h",
    "include/soc/qcom/qtee_shmbridge.h",
    "include/soc/qcom/secure_buffer.h",
)


def fetch(relative: str) -> bytes:
    url = RAW_BASE + relative
    last: Exception | None = None
    for attempt in range(4):
        request = urllib.request.Request(
            url, headers={"User-Agent": "A52-Phase256-devfreq-closure"}
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


def candidate_roots(args: list[str]) -> list[Path]:
    cwd = Path.cwd()
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


def locate(args: list[str]) -> Path:
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args):
        governor = root / "drivers/devfreq/governor_msm_adreno_tz.c"
        header = root / "include/linux/msm_adreno_devfreq.h"
        legacy_scm = root / "drivers/a52_secure/a52_legacy_scm.c"
        qtee_impl = root / "drivers/a52_secure/qtee_shmbridge.c"
        if not all(path.is_file() for path in (governor, header, legacy_scm, qtee_impl)):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(
            "expected one generated Phase256 source root with legacy SCM/QTEE, found "
            + (", ".join(map(str, hits)) or "none")
        )
    return hits[0]


def locate_config(root: Path) -> Path:
    for path in (
        Path.cwd() / "workspace/gki-phase199-out/.config",
        root / ".config",
        Path.cwd() / "gki/common/.config",
    ):
        if path.is_file():
            return path
    raise RuntimeError("Phase256 devfreq closure could not locate authoritative .config")


def stage(root: Path) -> None:
    for relative in DEPENDENCIES:
        target = root / relative
        if target.is_file():
            print(f"P256 closure retained existing {relative}", flush=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch(relative))
        print(f"P256 closure staged {relative}", flush=True)


def require_tokens(path: Path, tokens: tuple[str, ...]) -> None:
    if not path.is_file():
        raise RuntimeError(f"Phase256 devfreq closure missing {path}")
    text = path.read_text(encoding="utf-8")
    for token in tokens:
        if token not in text:
            raise RuntimeError(f"Phase256 devfreq closure {path} missing {token!r}")


def audit(root: Path) -> None:
    require_tokens(
        root / "include/soc/qcom/scm.h",
        ("struct scm_desc", "SCM_SIP_FNID", "scm_call2(", "scm_is_call_available("),
    )
    require_tokens(
        root / "drivers/devfreq/devfreq_trace.h",
        ("TRACE_EVENT(devfreq_msg", "TRACE_INCLUDE_FILE devfreq_trace"),
    )
    require_tokens(
        root / "include/soc/qcom/qtee_shmbridge.h",
        ("struct qtee_shm", "qtee_shmbridge_allocate_shm", "qtee_shmbridge_free_shm"),
    )
    require_tokens(
        root / "include/soc/qcom/secure_buffer.h",
        ("enum vmid", "PERM_READ", "PERM_WRITE"),
    )

    # Reuse the compatibility implementations already built by earlier phases.
    require_tokens(
        root / "drivers/a52_secure/a52_legacy_scm.c",
        ("scm_call2", "scm_call2_atomic", "scm_is_call_available"),
    )
    require_tokens(
        root / "drivers/a52_secure/qtee_shmbridge.c",
        ("qtee_shmbridge_allocate_shm", "qtee_shmbridge_free_shm"),
    )

    config = locate_config(root).read_text(encoding="utf-8")
    if "CONFIG_QCOM_SCM=y" not in config:
        raise RuntimeError("Phase256 devfreq closure requires CONFIG_QCOM_SCM=y")

    tz = (root / "drivers/devfreq/governor_msm_adreno_tz.c").read_text(encoding="utf-8")
    if "#include <soc/qcom/scm.h>" not in tz:
        raise RuntimeError("Phase256 Adreno TZ governor no longer includes legacy SCM contract")
    if "#include <soc/qcom/qtee_shmbridge.h>" not in tz:
        raise RuntimeError("Phase256 Adreno TZ governor no longer includes QTEE shmbridge contract")

    gpubw = (root / "drivers/devfreq/governor_gpubw_mon.c").read_text(encoding="utf-8")
    if '#include "devfreq_trace.h"' not in gpubw:
        raise RuntimeError("Phase256 GPUBW governor no longer includes devfreq_trace.h")


def self_test() -> None:
    assert TOUCHGRASS_COMMIT == "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
    assert DEPENDENCIES == (
        "include/soc/qcom/scm.h",
        "drivers/devfreq/devfreq_trace.h",
        "include/soc/qcom/qtee_shmbridge.h",
        "include/soc/qcom/secure_buffer.h",
    )
    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        MARKER,
        "CONFIG_QCOM_SCM=y",
        "a52_legacy_scm.c",
        "qtee_shmbridge.c",
        "SCM_SIP_FNID",
        "TRACE_EVENT(devfreq_msg",
    ):
        if token not in source:
            raise RuntimeError(f"Phase256 devfreq closure self-test missing {token!r}")
    print("Phase 256 devfreq import-closure self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    stage(root)
    audit(root)
    print(f"{MARKER}: downstream devfreq header closure applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
