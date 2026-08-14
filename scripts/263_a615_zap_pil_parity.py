#!/usr/bin/env python3
"""Phase263: restore Golden TouchGrass A615 ZAP PIL/SSR provider parity.

The downstream KGSL A615 path loads the secure ZAP shader through
subsystem_get(a6xx_core->zap_name). The port already carries the TouchGrass
compatibility headers, but the matching legacy PIL/subsystem provider objects
were never staged into the GKI build.

A prior cumulative experiment replaced the Golden a6xx_zap_load() control flow
with a direct-SCM helper. Phase263 restores the pinned TouchGrass function
verbatim at that boundary, removes the obsolete direct-SCM helper contract,
and stages only the legacy PIL/SSR provider closure needed by
qcom,pil-tz-generic.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

MARKER = "A52_PHASE263_A615_ZAP_PIL_PARITY_V1"
STALE_SCM_HELPER = "a52_a615_zap_scm_load"
GOLDEN_ZAP_SIGNATURE = "static int a6xx_zap_load(struct adreno_device *adreno_dev)"
GOLDEN_ZAP_FUNCTION = '''static int a6xx_zap_load(struct adreno_device *adreno_dev)
{
\tconst struct adreno_a6xx_core *a6xx_core = to_a6xx_core(adreno_dev);
\tvoid *zap;
\tint ret = 0;

\t/* Load the zap shader firmware through PIL if its available */
\tif (a6xx_core->zap_name && !adreno_dev->zap_loaded) {
\t\tzap = subsystem_get(a6xx_core->zap_name);

\t\t/* Return error if the zap shader cannot be loaded */
\t\tif (IS_ERR_OR_NULL(zap)) {
\t\t\tret = (zap == NULL) ? -ENODEV : PTR_ERR(zap);
\t\t\tzap = NULL;
\t\t} else
\t\t\tadreno_dev->zap_loaded = 1;
\t}

\treturn ret;
}'''
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
    """Return identifier occurrences in C text, ignoring comments and literals."""
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


def skip_ws(source: str, pos: int) -> int:
    while pos < len(source) and source[pos].isspace():
        pos += 1
    return pos


def scan_balanced_c(source: str, start: int, opener: str, closer: str) -> int:
    """Return the offset after one balanced C delimiter region."""
    if start >= len(source) or source[start] != opener:
        raise RuntimeError(f"Phase263 internal parser expected {opener!r} at {start}")
    depth = 0
    i = start
    mode = "normal"
    escaped = False
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
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    raise RuntimeError(f"Phase263 unbalanced {opener}{closer} while parsing C source")


def ensure_include(source: str, include: str) -> str:
    rendered = include + "\n"
    if rendered in source or include in source.splitlines():
        return source
    lines = source.splitlines(keepends=True)
    include_rows = [i for i, line in enumerate(lines) if line.startswith("#include ")]
    if not include_rows:
        raise RuntimeError(f"Phase263 cannot place required include: {include}")
    insert_at = include_rows[-1] + 1
    lines.insert(insert_at, rendered)
    return "".join(lines)


def replace_golden_zap_function(source: str) -> str:
    """Restore the pinned TouchGrass a6xx_zap_load() implementation exactly."""
    count = source.count(GOLDEN_ZAP_SIGNATURE)
    if count != 1:
        raise RuntimeError(
            f"Phase263 expected one {GOLDEN_ZAP_SIGNATURE!r}, found {count}"
        )
    start = source.find(GOLDEN_ZAP_SIGNATURE)
    body = skip_ws(source, start + len(GOLDEN_ZAP_SIGNATURE))
    if body >= len(source) or source[body] != "{":
        raise RuntimeError("Phase263 a6xx_zap_load opening brace drifted")
    end = scan_balanced_c(source, body, "{", "}")
    out = source[:start] + GOLDEN_ZAP_FUNCTION + source[end:]
    out = ensure_include(out, "#include <soc/qcom/subsystem_restart.h>")
    if out.count("zap = subsystem_get(a6xx_core->zap_name);") != 1:
        raise RuntimeError("Phase263 Golden a6xx_zap_load subsystem_get contract missing/duplicated")
    if out.count("adreno_dev->zap_loaded = 1;") < 1:
        raise RuntimeError("Phase263 Golden a6xx_zap_load zap_loaded contract missing")
    return out


def stale_static_span(source: str, name: str, name_pos: int) -> tuple[str, int, int]:
    """Classify one live identifier as a static prototype or static definition."""
    line_start = source.rfind("\n", 0, name_pos) + 1
    line_end = source.find("\n", name_pos)
    if line_end < 0:
        line_end = len(source)
    prefix = source[line_start:name_pos]
    line_no = source.count("\n", 0, name_pos) + 1
    if "static" not in prefix.split():
        context = source[line_start:line_end].strip()
        raise RuntimeError(
            f"Phase263 refuses to remove live {name} use at line {line_no}: {context!r}"
        )

    pos = skip_ws(source, name_pos + len(name))
    if pos >= len(source) or source[pos] != "(":
        context = source[line_start:line_end].strip()
        raise RuntimeError(
            f"Phase263 {name} at line {line_no} is not a function declaration/definition: {context!r}"
        )
    params_end = scan_balanced_c(source, pos, "(", ")")
    suffix = skip_ws(source, params_end)
    if suffix >= len(source):
        raise RuntimeError(f"Phase263 {name} at line {line_no} ends unexpectedly")

    if source[suffix] == ";":
        end = suffix + 1
        while end < len(source) and source[end] in " \t":
            end += 1
        if end < len(source) and source[end] == "\n":
            end += 1
        return "prototype", line_start, end

    if source[suffix] == "{":
        end = scan_balanced_c(source, suffix, "{", "}")
        while end < len(source) and source[end] in " \t":
            end += 1
        if end < len(source) and source[end] == "\n":
            end += 1
        if end < len(source) and source[end] == "\n":
            end += 1
        return "definition", line_start, end

    context = source[line_start:line_end].strip()
    raise RuntimeError(
        f"Phase263 refuses unexpected {name} construct at line {line_no}: {context!r}"
    )


def strip_named_static_function_contract(source: str, name: str) -> str:
    """Remove stale static prototypes plus exactly one stale static definition."""
    raw_hits = source.count(name)
    code_hits = c_identifier_positions(source, name)
    if not code_hits:
        return source

    spans = [stale_static_span(source, name, pos) for pos in code_hits]
    definitions = [span for span in spans if span[0] == "definition"]
    prototypes = [span for span in spans if span[0] == "prototype"]
    if len(definitions) != 1:
        raise RuntimeError(
            f"Phase263 refuses to remove {name}: expected exactly one static definition, "
            f"found definitions={len(definitions)} prototypes={len(prototypes)} "
            f"live={len(code_hits)} textual={raw_hits}"
        )

    out = source
    for _kind, start, end in sorted(spans, key=lambda span: span[1], reverse=True):
        out = out[:start] + out[end:]

    survivors = c_identifier_positions(out, name)
    if survivors:
        raise RuntimeError(
            f"Phase263 live stale helper reference survived removal: {name} count={len(survivors)}"
        )
    return out


def restore_golden_zap_control_flow(root: Path) -> None:
    path = root / "drivers/gpu/msm/adreno_a6xx.c"
    if not path.is_file():
        raise RuntimeError(f"Phase263 adreno source missing: {path}")
    before = path.read_text(encoding="utf-8")
    direct_hits = c_identifier_positions(before, STALE_SCM_HELPER)

    restored = replace_golden_zap_function(before)
    after = strip_named_static_function_contract(restored, STALE_SCM_HELPER)

    if c_identifier_positions(after, STALE_SCM_HELPER):
        raise RuntimeError("Phase263 direct-SCM helper survived Golden control-flow restoration")
    if "zap = subsystem_get(a6xx_core->zap_name);" not in after:
        raise RuntimeError("Phase263 Golden subsystem_get call missing after direct-SCM cleanup")
    path.write_text(after, encoding="utf-8")
    print(
        f"Phase263: restored Golden a6xx_zap_load PIL/SSR path; removed "
        f"{len(direct_hits)} live direct-SCM helper occurrences",
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

    sample = '''static int a52_a615_zap_scm_load(void *p);\n\nstatic int a52_a615_zap_scm_load(void *p)\n{\n\tif (p) {\n\t\tpr_info("a52_a615_zap_scm_load brace } in string");\n\t}\n\treturn 0;\n}\n\n/* a52_a615_zap_scm_load was experimental */\nstatic int keep_me(void)\n{\n\treturn 1;\n}\n'''
    assert sample.count(STALE_SCM_HELPER) == 4
    assert len(c_identifier_positions(sample, STALE_SCM_HELPER)) == 2
    stripped = strip_named_static_function_contract(sample, STALE_SCM_HELPER)
    assert len(c_identifier_positions(stripped, STALE_SCM_HELPER)) == 0
    assert "a52_a615_zap_scm_load was experimental" in stripped
    assert "static int keep_me(void)" in stripped
    assert strip_named_static_function_contract(stripped, STALE_SCM_HELPER) == stripped

    live_call = sample + '''\nstatic int bad_reference(void)\n{\n\treturn a52_a615_zap_scm_load(NULL);\n}\n'''
    try:
        strip_named_static_function_contract(live_call, STALE_SCM_HELPER)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Phase263 executable stale helper reference guard did not fail closed")

    direct_source = '''#include <linux/firmware.h>\n#include \"adreno.h\"\n\nstatic int a52_a615_zap_scm_load(struct adreno_device *adreno_dev, const char *name)\n{\n\treturn -ENODEV;\n}\n\nstatic int a6xx_zap_load(struct adreno_device *adreno_dev)\n{\n\tconst struct adreno_a6xx_core *a6xx_core = to_a6xx_core(adreno_dev);\n\tint ret = 0;\n\n\tif (a6xx_core->zap_name && !adreno_dev->zap_loaded)\n\t\tret = a52_a615_zap_scm_load(adreno_dev, a6xx_core->zap_name);\n\n\treturn ret;\n}\n'''
    restored = replace_golden_zap_function(direct_source)
    assert "zap = subsystem_get(a6xx_core->zap_name);" in restored
    assert "#include <soc/qcom/subsystem_restart.h>" in restored
    assert len(c_identifier_positions(restored, STALE_SCM_HELPER)) == 1
    cleaned = strip_named_static_function_contract(restored, STALE_SCM_HELPER)
    assert not c_identifier_positions(cleaned, STALE_SCM_HELPER)
    assert cleaned.count("zap = subsystem_get(a6xx_core->zap_name);") == 1

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

    restore_golden_zap_control_flow(root)

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
    if c_identifier_positions(adreno, STALE_SCM_HELPER):
        raise RuntimeError("Phase263 superseded direct-SCM helper survived final verification")
    if adreno.count("zap = subsystem_get(a6xx_core->zap_name);") != 1:
        raise RuntimeError("Phase263 Golden a6xx_zap_load final contract missing/duplicated")

    print(f"{MARKER}: Golden A615 ZAP PIL/SSR provider staged", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    apply(locate(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
