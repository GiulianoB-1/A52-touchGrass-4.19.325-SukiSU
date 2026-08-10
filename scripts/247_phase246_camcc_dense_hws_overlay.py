#!/usr/bin/env python3
"""Phase 247: adapt Lagoon CAMCC auxiliary hw clock list to GKI 5.10 semantics.

TouchGrass 4.19 stores cam_cc_pll2_out_early in a sparse binding-indexed
`hwclks[]` array at CAM_CC_PLL2_OUT_EARLY (ID 6).  Its qcom_cc_really_probe()
explicitly skips NULL entries.  Exact GKI 5.10 uses `clk_hws` as a dense
registration list and does not skip NULL entries.  Phase54 previously renamed
hwclks->clk_hws without densifying the array, so CAMCC can pass NULL to
`devm_clk_hw_register()` during probe.

This overlay changes only the generated Lagoon CAMCC auxiliary-hw array from
sparse to one dense entry.  Phase245 fw_devlink permissive state and Phase246
subsys initcall tracing are retained unchanged.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

CAMCC = Path("drivers/clk/qcom/camcc-lagoon.c")
CORE = Path("drivers/base/core.c")
INIT_MAIN = Path("init/main.c")

MARKER = "A52_PHASE247_CAMCC_DENSE_HWS_V1"
PERMISSIVE = "static u32 fw_devlink_flags = FW_DEVLINK_FLAGS_PERMISSIVE;"
PHASE246 = "CXF246 S n=%d f=%ps"
SPARSE_TOKEN = "[CAM_CC_PLL2_OUT_EARLY] = &cam_cc_pll2_out_early.hw"
DENSE_ENTRY = "&cam_cc_pll2_out_early.hw"

ARRAY_RE = re.compile(
    r"(?P<prefix>struct\s+clk_hw\s*\*\s*cam_cc_lagoon_hws\[\]\s*=\s*\{)"
    r"(?P<body>.*?)"
    r"(?P<suffix>\};)",
    re.S,
)


def validate_camcc(text: str, label: str) -> None:
    if text.count(MARKER) != 1:
        raise RuntimeError(f"{label}: Phase247 marker count is {text.count(MARKER)}, expected 1")
    if SPARSE_TOKEN in text:
        raise RuntimeError(f"{label}: sparse CAM_CC_PLL2_OUT_EARLY designated entry remains")
    matches = list(ARRAY_RE.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"{label}: expected one cam_cc_lagoon_hws array, found {len(matches)}")
    body = matches[0].group("body")
    entries = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("/*")]
    if entries != [DENSE_ENTRY + ","]:
        raise RuntimeError(f"{label}: dense CAMCC hw list is not exactly one pll2_out_early entry: {entries!r}")
    for token in (
        ".clk_hws = cam_cc_lagoon_hws",
        ".num_clk_hws = ARRAY_SIZE(cam_cc_lagoon_hws)",
        "cam_cc_pll2_out_early",
        "cam_cc_lagoon_probe",
        "cam_cc_lagoon_init",
    ):
        if token not in text:
            raise RuntimeError(f"{label}: missing required CAMCC token {token}")
    for forbidden in (".hwclks =", ".num_hwclks ="):
        if forbidden in text:
            raise RuntimeError(f"{label}: stale TouchGrass descriptor field remains: {forbidden}")


def patch_camcc(text: str, label: str) -> str:
    if MARKER in text:
        validate_camcc(text, label)
        return text

    matches = list(ARRAY_RE.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"{label}: expected one cam_cc_lagoon_hws array, found {len(matches)}")
    match = matches[0]
    body = match.group("body")
    if body.count("CAM_CC_PLL2_OUT_EARLY") != 1 or body.count(DENSE_ENTRY) != 1:
        raise RuntimeError(
            f"{label}: expected exactly one sparse PLL2_OUT_EARLY entry; "
            f"binding_refs={body.count('CAM_CC_PLL2_OUT_EARLY')} hw_refs={body.count(DENSE_ENTRY)}"
        )
    residual = re.sub(
        r"\[CAM_CC_PLL2_OUT_EARLY\]\s*=\s*&cam_cc_pll2_out_early\.hw\s*,?",
        "",
        body,
    )
    if residual.strip():
        raise RuntimeError(f"{label}: unexpected additional entries/content in CAMCC hw array: {residual!r}")

    replacement = (
        match.group("prefix")
        + "\n\t/* " + MARKER + ": GKI clk_hws is a dense registration list. */\n"
        + "\t&cam_cc_pll2_out_early.hw,\n"
        + match.group("suffix")
    )
    patched = text[: match.start()] + replacement + text[match.end() :]
    validate_camcc(patched, label)
    return patched


def candidate_roots(args: list[str], cwd: Path) -> list[Path]:
    roots: list[Path] = []
    for value in args:
        if value.startswith("-"):
            continue
        p = Path(value)
        if not p.is_absolute():
            p = cwd / p
        roots.extend((p, p.parent))
    roots.extend((cwd / "workspace/gki-phase199-src", cwd / "gki/common"))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            out.append(root)
    return out


def locate(args: list[str], cwd: Path | None = None) -> Path:
    base = cwd or Path.cwd()
    hits: list[Path] = []
    seen: set[Path] = set()
    for root in candidate_roots(args, base):
        camcc = root / CAMCC
        core = root / CORE
        init_main = root / INIT_MAIN
        if not camcc.is_file() or not core.is_file() or not init_main.is_file():
            continue
        if PERMISSIVE not in core.read_text(encoding="utf-8"):
            continue
        if PHASE246 not in init_main.read_text(encoding="utf-8"):
            continue
        text = camcc.read_text(encoding="utf-8")
        if "cam_cc_lagoon_hws" not in text:
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(
            "expected exactly one generated Phase246 GKI root containing Lagoon CAMCC; found "
            + (", ".join(map(str, hits)) or "none")
        )
    return hits[0]


def self_test() -> None:
    fixture = """// CAMCC fixture
struct clk_hw *cam_cc_lagoon_hws[] = {
    [CAM_CC_PLL2_OUT_EARLY] = &cam_cc_pll2_out_early.hw,
};
static const struct qcom_cc_desc cam_cc_lagoon_desc = {
    .clk_hws = cam_cc_lagoon_hws,
    .num_clk_hws = ARRAY_SIZE(cam_cc_lagoon_hws),
};
static int cam_cc_lagoon_probe(void *pdev) { return 0; }
static int cam_cc_lagoon_init(void) { return 0; }
"""
    patched = patch_camcc(fixture, "fixture/camcc-lagoon.c")
    assert SPARSE_TOKEN not in patched
    assert patched.count(MARKER) == 1
    assert patch_camcc(patched, "fixture/idempotent") == patched

    bad = fixture.replace(
        "    [CAM_CC_PLL2_OUT_EARLY] = &cam_cc_pll2_out_early.hw,\n",
        "    [CAM_CC_PLL2_OUT_EARLY] = &cam_cc_pll2_out_early.hw,\n    &unexpected.hw,\n",
    )
    try:
        patch_camcc(bad, "fixture/bad")
    except RuntimeError:
        pass
    else:
        raise AssertionError("unexpected multi-entry sparse fixture was accepted")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "gki/common"
        (root / CAMCC).parent.mkdir(parents=True)
        (root / CORE).parent.mkdir(parents=True)
        (root / INIT_MAIN).parent.mkdir(parents=True)
        (root / CAMCC).write_text(fixture, encoding="utf-8")
        (root / CORE).write_text(PERMISSIVE + "\n", encoding="utf-8")
        (root / INIT_MAIN).write_text(PHASE246 + "\n", encoding="utf-8")
        found = locate([], Path(td))
        assert found.resolve() == root.resolve()

    print("Phase 247 CAMCC dense clk_hws overlay self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    path = root / CAMCC
    before = path.read_text(encoding="utf-8")
    after = patch_camcc(before, str(path))
    path.write_text(after, encoding="utf-8")
    print(
        "Phase 247 CAMCC compatibility applied: sparse binding-indexed hwclks -> dense GKI clk_hws list",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
