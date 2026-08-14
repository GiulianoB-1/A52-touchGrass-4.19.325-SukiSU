#!/usr/bin/env python3
"""Phase263: restore Golden TouchGrass A615 ZAP PIL/SSR provider parity.

The downstream KGSL A615 path calls subsystem_get("a615_zap") from
A6xx ringbuffer start. The port already carries the TouchGrass compatibility
headers, but the matching legacy PIL/subsystem provider objects were never
staged into the GKI build. This overlay imports only the provider closure
needed by qcom,pil-tz-generic and enables the three matching Golden symbols.

Phase262 briefly carried a direct-SCM A615 ZAP loader experiment. Phase263
supersedes that experiment with the Golden PIL/SSR provider path, so the stale
direct helper must be removed before compile rather than hidden with an
unused-function annotation.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52_PHASE263_A615_ZAP_PIL_PARITY_V1"
STALE_SCM_HELPER = "a52_a615_zap_scm_load"
CONFIGS = (
    "CONFIG_MSM_SUBSYSTEM_RESTART=y",
    "CONFIG_MSM_PIL=y",
    "CONFIG_MSM_PIL_SSR_GENERIC=y",
)
SOURCES = (
    "peripheral-loader.c",
    "peripheral-loader.h",
    "subsys-pil-tz.c",
    "subsystem_restart.c",
    "subsystem_notif.c",
    "ramdump.c",
    "microdump_collector.c",
    "minidump_private.h",
)
OBJECTS = (
    "subsystem_notif.o",
    "subsystem_restart.o",
    "ramdump.o",
    "microdump_collector.o",
    "peripheral-loader.o",
    "subsys-pil-tz.o",
)


def locate(argv: list[str]) -> Path:
    if argv and argv[0] != "--self-test":
        root = Path(argv[0]).resolve()
    else:
        root = Path("gki/common").resolve()
    return root


def workspace(root: Path) -> Path:
    return root.parent.parent


def append_once(path: Path, marker: str, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + block.rstrip() + "\n", encoding="utf-8")


def set_config(path: Path, symbol: str) -> None:
    text = path.read_text(encoding="utf-8")
    enabled = f"CONFIG_{symbol}=y"
    disabled = f"# CONFIG_{symbol} is not set"
    lines = text.splitlines()
    if enabled in lines:
        return
    if disabled in lines:
        text = text.replace(disabled, enabled, 1)
    else:
        text = text.rstrip() + "\n" + enabled + "\n"
    path.write_text(text, encoding="utf-8")


def c_identifier_positions(source: str, name: str) -> list[int]:
    """Return identifier occurrences in live C text, ignoring comments/strings."""
    positions: list[int] = []
    i = 0
    mode = "normal"
    escaped = False
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""

        if mode == "line_comment":
            if ch == "\n":
                mode = "normal"
            i += 1
            continue
        if mode == "block_comment":
            if ch == "*" and nxt == "/":
                mode = "normal"
                i += 2
            else:
                i += 1
            continue
        if mode == "string":
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                mode = "normal"
            i += 1
            continue
        if mode == "char":
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "'":
                mode = "normal"
            i += 1
            continue

        if ch == "/" and nxt == "/":
            mode = "line_comment"
            i += 2
            continue
        if ch == "/" and nxt == "*":
            mode = "block_comment"
            i += 2
            continue
        if ch == '"':
            mode = "string"
            escaped = False
            i += 1
            continue
        if ch == "'":
            mode = "char"
            escaped = False
            i += 1
            continue
        if ch == "_" or ch.isalpha():
            start = i
            i += 1
            while i < len(source) and (source[i] == "_" or source[i].isalnum()):
                i += 1
            if source[start:i] == name:
                positions.append(start)
            continue
        i += 1
    return positions


def strip_named_c_function(source: str, name: str) -> str:
    """Remove exactly one stale static C definition, ignoring textual mentions."""
    raw_hits = source.count(name)
    code_hits = c_identifier_positions(source, name)
    if not code_hits:
        return source
    if len(code_hits) != 1:
        raise RuntimeError(
            f"Phase263 refuses to remove {name}: expected one live C identifier, "
            f"found {len(code_hits)} live / {raw_hits} textual occurrences"
        )

    name_pos = code_hits[0]
    line_start = source.rfind("\n", 0, name_pos) + 1
    prefix = source[line_start:name_pos]
    if "static" not in prefix.split():
        raise RuntimeError(f"Phase263 {name} live occurrence is not a static function definition")

    brace = source.find("{", name_pos)
    if brace < 0:
        raise RuntimeError(f"Phase263 {name} definition opening brace missing")
    semicolon = source.find(";", name_pos, brace)
    if semicolon >= 0:
        raise RuntimeError(f"Phase263 {name} looks like a declaration/call, not a definition")

    depth = 0
    i = brace
    mode = "normal"
    escaped = False
    end = -1
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""

        if mode == "line_comment":
            if ch == "\n":
                mode = "normal"
        elif mode == "block_comment":
            if ch == "*" and nxt == "/":
                mode = "normal"
                i += 1
        elif mode == "string":
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                mode = "normal"
        elif mode == "char":
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "'":
                mode = "normal"
        else:
            if ch == "/" and nxt == "/":
                mode = "line_comment"
                i += 1
            elif ch == "/" and nxt == "*":
                mode = "block_comment"
                i += 1
            elif ch == '"':
                mode = "string"
            elif ch == "'":
                mode = "char"
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        i += 1

    if end < 0:
        raise RuntimeError(f"Phase263 {name} definition closing brace missing")

    while end < len(source) and source[end] in " \t":
        end += 1
    if end < len(source) and source[end] == "\n":
        end += 1
    if end < len(source) and source[end] == "\n":
        end += 1

    out = source[:line_start] + source[end:]
    survivors = c_identifier_positions(out, name)
    if survivors:
        raise RuntimeError(
            f"Phase263 live stale helper reference survived removal: {name} count={len(survivors)}"
        )
    return out


def remove_superseded_direct_scm(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno_a6xx.c"
    if not path.is_file():
        raise RuntimeError(f"Phase263 adreno source missing: {path}")
    before = path.read_text(encoding="utf-8")
    if not c_identifier_positions(before, STALE_SCM_HELPER):
        print(f"Phase263: {STALE_SCM_HELPER} already absent from live C", flush=True)
        return
    after = strip_named_c_function(before, STALE_SCM_HELPER)
    path.write_text(after, encoding="utf-8")
    print(
        f"Phase263: removed superseded Phase262 helper {STALE_SCM_HELPER} "
        f"(textual mentions before={before.count(STALE_SCM_HELPER)})",
        flush=True,
    )


def self_test() -> None:
    assert len(SOURCES) == len(set(SOURCES))
    assert len(OBJECTS) == len(set(OBJECTS))
    assert "subsys-pil-tz.c" in SOURCES
    assert "peripheral-loader.c" in SOURCES
    assert "subsystem_restart.c" in SOURCES
    assert CONFIGS == (
        "CONFIG_MSM_SUBSYSTEM_RESTART=y",
        "CONFIG_MSM_PIL=y",
        "CONFIG_MSM_PIL_SSR_GENERIC=y",
    )

    sample = '''static int a52_a615_zap_scm_load(void *p)\n{\n\tif (p) {\n\t\tpr_info("a52_a615_zap_scm_load brace } in string");\n\t}\n\treturn 0;\n}\n\n/* a52_a615_zap_scm_load was experimental */\nstatic int keep_me(void)\n{\n\treturn 1;\n}\n'''
    assert sample.count(STALE_SCM_HELPER) == 3
    assert len(c_identifier_positions(sample, STALE_SCM_HELPER)) == 1
    stripped = strip_named_c_function(sample, STALE_SCM_HELPER)
    assert len(c_identifier_positions(stripped, STALE_SCM_HELPER)) == 0
    assert "a52_a615_zap_scm_load was experimental" in stripped
    assert "static int keep_me(void)" in stripped
    assert strip_named_c_function(stripped, STALE_SCM_HELPER) == stripped

    live_call = sample + '''\nstatic int bad_reference(void)\n{\n\treturn a52_a615_zap_scm_load(NULL);\n}\n'''
    try:
        strip_named_c_function(live_call, STALE_SCM_HELPER)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Phase263 live stale helper reference guard did not fail closed")

    print("Phase 263 A615 ZAP PIL provider parity self-test: PASS", flush=True)


def apply(root: Path) -> None:
    ws = workspace(root)
    tg = ws / "workspace/touchgrass-a52xq/drivers/soc/qcom"
    cfg = ws / "workspace/gki-phase199-out/.config"
    if not root.is_dir():
        raise RuntimeError(f"Phase263 GKI root missing: {root}")
    if not tg.is_dir():
        raise RuntimeError(f"Phase263 TouchGrass qcom source missing: {tg}")
    if not cfg.is_file():
        raise RuntimeError(f"Phase263 build config missing: {cfg}")

    missing = [name for name in SOURCES if not (tg / name).is_file()]
    if missing:
        raise RuntimeError("Phase263 TouchGrass source closure missing: " + ", ".join(missing))

    # Phase263 replaces the experimental direct-SCM ZAP path with the Golden
    # subsystem/PIL provider. Do this before compile so -Werror cannot stop on
    # the now-unused Phase262 helper.
    remove_superseded_direct_scm(root)

    dst = root / "drivers/a52_pil"
    dst.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        shutil.copy2(tg / name, dst / name)

    mk = f"""# {MARKER}\nccflags-y += -include $(srctree)/a52-port-compat.h\nccflags-y += -I$(srctree)/a52-compat/include\nccflags-y += -I$(srctree)/a52-compat/include/uapi\nobj-y += {' '.join(OBJECTS)}\n"""
    (dst / "Makefile").write_text(mk, encoding="utf-8")

    kc = f"""# {MARKER}\nconfig MSM_SUBSYSTEM_RESTART\n\tbool \"A52 legacy subsystem restart provider\"\n\tdefault y\n\nconfig MSM_PIL\n\tbool \"A52 legacy peripheral image loader\"\n\tdefault y\n\tselect FW_LOADER\n\nconfig MSM_PIL_SSR_GENERIC\n\tbool \"A52 legacy generic PIL/SSR provider\"\n\tdefault y\n\tdepends on MSM_PIL && MSM_SUBSYSTEM_RESTART\n"""
    (dst / "Kconfig").write_text(kc, encoding="utf-8")

    append_once(
        root / "drivers/Makefile",
        MARKER,
        f"# {MARKER}\nobj-y += a52_pil/",
    )
    append_once(
        root / "drivers/Kconfig",
        MARKER,
        f"# {MARKER}\nsource \"drivers/a52_pil/Kconfig\"",
    )

    for line in CONFIGS:
        set_config(cfg, line[len("CONFIG_"):].split("=", 1)[0])

    # Fail closed on the actual secure-ZAP contract we intend to restore.
    tz = (dst / "subsys-pil-tz.c").read_text(encoding="utf-8")
    sr = (dst / "subsystem_restart.c").read_text(encoding="utf-8")
    pl = (dst / "peripheral-loader.c").read_text(encoding="utf-8")
    if '"qcom,pil-tz-generic"' not in tz:
        raise RuntimeError("Phase263 imported provider lacks qcom,pil-tz-generic")
    if "subsys_register(&d->subsys_desc)" not in tz:
        raise RuntimeError("Phase263 imported provider lacks subsystem registration")
    if "void *subsystem_get(const char *name)" not in sr:
        raise RuntimeError("Phase263 imported SSR lacks subsystem_get provider")
    if "int pil_boot(struct pil_desc *desc)" not in pl:
        raise RuntimeError("Phase263 imported PIL lacks pil_boot provider")
    final = cfg.read_text(encoding="utf-8").splitlines()
    for line in CONFIGS:
        if line not in final:
            raise RuntimeError(f"Phase263 config did not apply: {line}")

    adreno = (root / "drivers/gpu/msm/adreno_a6xx.c").read_text(encoding="utf-8")
    survivors = c_identifier_positions(adreno, STALE_SCM_HELPER)
    if survivors:
        raise RuntimeError(
            f"Phase263 superseded direct-SCM helper survived final verification: {len(survivors)} live references"
        )

    print(f"{MARKER}: Golden A615 ZAP PIL/SSR provider staged", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
