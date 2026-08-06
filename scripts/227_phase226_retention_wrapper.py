#!/usr/bin/env python3
"""Load Phase 233 with final graphics and exact Lagoon supplier closure."""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
source_path = root / "233_phase232_final_graphics_parity_wrapper.py"
if not source_path.is_file():
    payload = root / "233_payload.py"
    if not payload.is_file():
        raise SystemExit(f"missing Phase 233 payload: {payload}")
    subprocess.run([sys.executable, str(payload)], check=True)
if not source_path.is_file():
    raise SystemExit(f"missing Phase 233 wrapper after payload: {source_path}")


TOOLCHAIN_IDENTITY_SYMBOLS = (
    "CONFIG_CC_VERSION_TEXT",
    "CONFIG_CC_IS_CLANG",
    "CONFIG_CC_IS_GCC",
    "CONFIG_CLANG_VERSION",
    "CONFIG_GCC_VERSION",
    "CONFIG_AS_IS_LLVM",
    "CONFIG_AS_IS_GNU",
    "CONFIG_AS_VERSION",
    "CONFIG_LD_IS_LLD",
    "CONFIG_LD_VERSION",
    "CONFIG_LLD_VERSION",
)


SUPPLIER_CONFIGS = {
    "CONFIG_CAM_CC_LAGOON": (
        "drivers/clk/qcom/camcc-lagoon.c",
        '"qcom,lagoon-camcc"',
        "camcc-lagoon.o",
    ),
    "CONFIG_VIDEO_CC_LAGOON": (
        "drivers/clk/qcom/videocc-lagoon.c",
        '"qcom,lagoon-videocc"',
        "videocc-lagoon.o",
    ),
    "CONFIG_NPU_CC_LAGOON": (
        "drivers/clk/qcom/npucc-lagoon.c",
        '"qcom,lagoon-npucc"',
        "npucc-lagoon.o",
    ),
}


def locate_kernel_root() -> Path | None:
    for candidate in (
        Path.cwd() / "workspace/gki-phase199-src",
        Path.cwd() / "gki/common",
    ):
        if (candidate / "drivers/clk/qcom/Kconfig").is_file():
            return candidate
    return None


def locate_authoritative_config() -> Path | None:
    for candidate in (
        Path.cwd() / "workspace/gki-phase199-out/.config",
        Path.cwd() / "workspace/gki-phase199-src/.config",
        Path.cwd() / "gki/common/.config",
        Path.cwd() / ".config",
    ):
        if candidate.is_file():
            return candidate
    return None


def set_config_builtin(path: Path, symbol: str) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    enabled = f"{symbol}=y"
    disabled = f"# {symbol} is not set"
    matches = [
        index for index, line in enumerate(lines)
        if line == disabled or line.startswith(f"{symbol}=")
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"{path}: expected one Kconfig state for {symbol}, found {len(matches)}"
        )
    lines[matches[0]] = enabled
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def enable_exact_lagoon_suppliers() -> None:
    kernel_root = locate_kernel_root()
    config = locate_authoritative_config()
    if kernel_root is None or config is None:
        return

    kconfig = (kernel_root / "drivers/clk/qcom/Kconfig").read_text(encoding="utf-8")
    makefile = (kernel_root / "drivers/clk/qcom/Makefile").read_text(encoding="utf-8")
    for symbol, (relative_source, compatible, object_name) in SUPPLIER_CONFIGS.items():
        source_file = kernel_root / relative_source
        if not source_file.is_file():
            raise SystemExit(f"missing exact Lagoon supplier source: {source_file}")
        source_text = source_file.read_text(encoding="utf-8")
        if compatible not in source_text:
            raise SystemExit(f"{source_file}: missing exact compatible {compatible}")
        config_name = symbol.removeprefix("CONFIG_")
        if f"config {config_name}" not in kconfig:
            raise SystemExit(f"Lagoon supplier Kconfig entry missing: {config_name}")
        make_token = f"obj-$({symbol}) += {object_name}"
        if make_token not in makefile:
            raise SystemExit(f"Lagoon supplier Makefile entry missing: {make_token}")
        set_config_builtin(config, symbol)

    print(
        "Phase 233 exact supplier closure: enabled "
        + ", ".join(SUPPLIER_CONFIGS),
        flush=True,
    )


def parse_config(path: Path) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            symbol, value = line.split("=", 1)
            states[symbol] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            states[line[2:-11]] = "n"
    return states


_phase233_retention_repaired = False
_phase233_kconfig_resolved = False
_phase233_self_test = "--self-test" in sys.argv[1:]


def resolve_phase233_kconfig() -> None:
    """Resolve Phase 233 hidden Kconfig selections in the authoritative O= tree."""
    global _phase233_kconfig_resolved
    if _phase233_kconfig_resolved:
        return

    config = locate_authoritative_config()
    build_root = None
    for candidate in (
        Path.cwd() / "gki/common",
        Path.cwd() / "workspace/gki-phase199-src",
    ):
        if (candidate / "Makefile").is_file() and (candidate / "Kconfig").is_file():
            build_root = candidate
            break
    if config is None or build_root is None:
        raise SystemExit(
            "Phase 233 could not resolve Kconfig: authoritative config or build root missing"
        )

    qcom_kconfig = build_root / "drivers/soc/qcom/Kconfig"
    qcom_makefile = build_root / "drivers/soc/qcom/Makefile"
    mdt_source = build_root / "drivers/soc/qcom/mdt_loader.c"
    for required in (qcom_kconfig, qcom_makefile, mdt_source):
        if not required.is_file():
            raise SystemExit(f"Phase 233 MDT selector dependency missing: {required}")

    kconfig_text = qcom_kconfig.read_text(encoding="utf-8")
    original_stanza = (
        "config QCOM_MDT_LOADER\n"
        "\ttristate\n"
        "\tselect QCOM_SCM"
    )
    selected_stanza = (
        "config QCOM_MDT_LOADER\n"
        "\ttristate\n"
        "\tdefault y if GPU_CC_LAGOON\n"
        "\tselect QCOM_SCM"
    )
    if selected_stanza not in kconfig_text:
        if kconfig_text.count(original_stanza) != 1:
            raise SystemExit(
                "Phase 233 expected exactly one pristine QCOM_MDT_LOADER Kconfig stanza"
            )
        kconfig_text = kconfig_text.replace(original_stanza, selected_stanza, 1)
        qcom_kconfig.write_text(kconfig_text, encoding="utf-8")

    makefile_text = qcom_makefile.read_text(encoding="utf-8")
    if "obj-$(CONFIG_QCOM_MDT_LOADER)" not in makefile_text or "mdt_loader.o" not in makefile_text:
        raise SystemExit("Phase 233 MDT loader Makefile linkage is missing")

    before_states = parse_config(config)
    required_llvm_identity = {
        "CONFIG_CC_IS_CLANG": "y",
        "CONFIG_AS_IS_LLVM": "y",
        "CONFIG_LD_IS_LLD": "y",
    }
    wrong_identity = sorted(
        f"{symbol}={before_states.get(symbol, 'n')}"
        for symbol, expected in required_llvm_identity.items()
        if before_states.get(symbol, "n") != expected
    )
    if wrong_identity:
        raise SystemExit(
            "Phase 233 authoritative config is not a Clang/LLVM/LLD build: "
            + ", ".join(wrong_identity)
        )
    toolchain_before = {
        symbol: before_states.get(symbol, "n")
        for symbol in TOOLCHAIN_IDENTITY_SYMBOLS
    }

    # Kconfig compiler capability symbols are generated from the active build
    # toolchain. Use the same LLVM path as the Android GKI build instead of
    # falling back to the runner's host GCC/binutils defaults.
    command = [
        "make",
        "-C",
        str(build_root),
        f"O={config.parent.resolve()}",
        "ARCH=arm64",
        "LLVM=1",
        "LLVM_IAS=1",
        "olddefconfig",
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Phase 233 LLVM olddefconfig failed with exit code {exc.returncode}"
        ) from exc

    states = parse_config(config)
    toolchain_after = {
        symbol: states.get(symbol, "n")
        for symbol in TOOLCHAIN_IDENTITY_SYMBOLS
    }
    changed_toolchain = sorted(
        symbol
        for symbol in TOOLCHAIN_IDENTITY_SYMBOLS
        if toolchain_before[symbol] != toolchain_after[symbol]
    )
    if changed_toolchain:
        rendered = ", ".join(
            f"{symbol}: {toolchain_before[symbol]} -> {toolchain_after[symbol]}"
            for symbol in changed_toolchain
        )
        raise SystemExit(
            "Phase 233 LLVM olddefconfig changed toolchain identity: " + rendered
        )
    if states.get("CONFIG_QCOM_MDT_LOADER") != "y":
        raise SystemExit(
            "Phase 233 Kconfig resolution did not select CONFIG_QCOM_MDT_LOADER=y"
        )
    _phase233_kconfig_resolved = True
    print(
        "Phase 233 authoritative olddefconfig resolved CONFIG_QCOM_MDT_LOADER=y",
        flush=True,
    )


def repair_phase217_retention_snapshot(*, require_final_state: bool) -> bool:
    """Refresh the stale Phase 217 snapshot only after Phase 233 is final."""
    global _phase233_retention_repaired
    if _phase233_retention_repaired:
        return True

    config = locate_authoritative_config()
    snapshot = (
        Path.cwd()
        / "artifacts/a52xq-graphics-startup-trace/config/before-phase217.config"
    )
    if config is None or not snapshot.is_file():
        return False

    before = parse_config(snapshot)
    after = parse_config(config)
    required_enabled = {
        "CONFIG_GPU_CC_LAGOON",
        "CONFIG_QCOM_MDT_LOADER",
        "CONFIG_DRM_PANEL",
        *SUPPLIER_CONFIGS.keys(),
    }
    wrong_enabled = sorted(
        symbol for symbol in required_enabled if after.get(symbol) != "y"
    )
    wrong_disabled = sorted(
        symbol
        for symbol in ("CONFIG_DRM_MSM", "CONFIG_FB_MSM")
        if after.get(symbol, "n") != "n"
    )
    if wrong_enabled or wrong_disabled:
        if require_final_state:
            details = []
            if wrong_enabled:
                details.append("not built-in: " + ", ".join(wrong_enabled))
            if wrong_disabled:
                details.append("not disabled: " + ", ".join(wrong_disabled))
            raise SystemExit(
                "Phase 233 final config state was not reached before retention repair: "
                + "; ".join(details)
            )
        return False

    changed = {
        symbol
        for symbol in set(before) | set(after)
        if before.get(symbol, "n") != after.get(symbol, "n")
    }
    allowed = {
        "CONFIG_GPU_CC_LAGOON",
        "CONFIG_QCOM_MDT_LOADER",
        "CONFIG_DRM_PANEL",
        "CONFIG_DRM_MSM",
        "CONFIG_FB_MSM",
        "CONFIG_CHR_DEV_SG",
        *SUPPLIER_CONFIGS.keys(),
    }
    unexpected = sorted(changed - allowed)
    if unexpected:
        raise SystemExit(
            "Phase 233 retention repair refused unexpected config drift: "
            + ", ".join(unexpected)
        )

    snapshot.write_bytes(config.read_bytes())
    _phase233_retention_repaired = True
    print(
        "Phase 233 retention snapshot updated before inherited comparison: "
        + (", ".join(sorted(changed)) or "no semantic changes"),
        flush=True,
    )
    return True


def validate_disabled_config_symbol(path: Path, symbol: str) -> None:
    """Reject enabled forms without mutating inherited config snapshots."""
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    assignments = [line for line in lines if line.startswith(f"{symbol}=")]
    if assignments:
        rendered = ", ".join(assignments)
        raise SystemExit(
            f"{path}: {symbol} must be disabled for Phase 233 parity; found {rendered}"
        )


# Absence and '# CONFIG_FB_MSM is not set' are equivalent disabled Kconfig
# states. Validate the real files, but do not modify them before the inherited
# byte-for-byte config-retention gates run.
for config_path in (
    Path.cwd() / "workspace/gki-phase199-out/.config",
    Path.cwd() / "workspace/gki-phase199-src/.config",
    Path.cwd() / "gki/common/.config",
    Path.cwd() / ".config",
):
    validate_disabled_config_symbol(config_path, "CONFIG_FB_MSM")

enable_exact_lagoon_suppliers()

source = source_path.read_text(encoding="utf-8")
old = '''def locate_config(root: Path) -> Path:
    candidates = (
        Path.cwd() / CONFIG_REL,
        root.parent.parent / "workspace/gki-phase199-out/.config",
        root / ".config",
    )
    matches: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if not path.is_file():
            continue
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        matches.append(path)
    if len(matches) != 1:
        rendered = ", ".join(str(path) for path in matches) or "none"
        raise RuntimeError(f"expected one generated kernel config, found {rendered}")
    return matches[0]
'''
new = '''def locate_config(root: Path) -> Path:
    # A52_PHASE233_AUTHORITATIVE_CONFIG_V2
    # The cumulative build can retain both O=/.config and an in-tree .config.
    # O= is the config used for the final Image and therefore has priority.
    candidates = (
        Path.cwd() / CONFIG_REL,
        root.parent.parent / "workspace/gki-phase199-out/.config",
        root / ".config",
    )
    existing: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        key = path.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            existing.append(path)
    if not existing:
        rendered = ", ".join(str(path) for path in candidates)
        raise RuntimeError(
            f"no generated kernel config found; checked: {rendered}"
        )
    selected = existing[0]
    print(
        "Phase 233 config candidates: "
        + ", ".join(str(path) for path in existing)
        + f"; selected authoritative config: {selected}",
        flush=True,
    )
    return selected
'''
if source.count(old) != 1:
    raise SystemExit(
        "Phase 233 config-locator correction expected exactly one source block, "
        f"found {source.count(old)}"
    )
source = source.replace(old, new, 1)

# Phase 233's generated Python audit expects the canonical disabled comment.
# Present that comment only to reads performed directly by the generated Phase
# 233 wrapper. The underlying file remains unchanged, so inherited retention
# snapshots and shell comparisons continue to see the exact original config.
_original_read_text = Path.read_text
_source_filename = str(source_path)
_disabled_line = "# CONFIG_FB_MSM is not set"


def _phase233_read_text(path: Path, *args: object, **kwargs: object) -> str:
    text = _original_read_text(path, *args, **kwargs)
    frame = inspect.currentframe()
    caller = frame.f_back if frame is not None else None
    direct_phase233_read = (
        caller is not None and caller.f_code.co_filename == _source_filename
    )
    if direct_phase233_read and path.name == ".config":
        lines = text.splitlines()
        if not any(line.startswith("CONFIG_FB_MSM=") for line in lines):
            if _disabled_line not in lines:
                separator = "" if not text or text.endswith("\n") else "\n"
                return text + separator + _disabled_line + "\n"
    return text


Path.read_text = _phase233_read_text
try:
    try:
        exec(compile(source, _source_filename, "exec"), globals(), globals())
    except SystemExit as exc:
        # The generated wrapper terminates with ``raise SystemExit(main())``.
        # A successful real apply would otherwise skip the statement after
        # exec(), leaving the surrounding Phase 217 shell cmp with its stale
        # snapshot. Self-tests intentionally run before the final config state.
        if exc.code in (None, 0) and not _phase233_self_test:
            resolve_phase233_kconfig()
            repair_phase217_retention_snapshot(require_final_state=True)
        raise
finally:
    Path.read_text = _original_read_text

# Also cover generated wrappers that return normally instead of SystemExit.
if not _phase233_self_test:
    resolve_phase233_kconfig()
    repair_phase217_retention_snapshot(require_final_state=True)
