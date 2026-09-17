#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KERNEL="$ROOT/workspace/touchgrass-a52xq"
REPORT="$ROOT/artifacts/phase101-419235-compile-repair.txt"

# Keep all previously proven Phase101 compile repairs unchanged, then apply
# the next narrow Samsung/stable reconciliation discovered by the compiler.
"$SCRIPT_DIR/101_fix_419235_compile_base.sh"

python3 - "$KERNEL" "$REPORT" <<'PY'
from pathlib import Path
import re
import sys

kernel = Path(sys.argv[1])
report = Path(sys.argv[2])


def function_end(source: str, start: int) -> int:
    brace = source.find("{", start)
    if brace < 0:
        raise SystemExit("function opening brace missing")
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    raise SystemExit("function closing brace missing")


# Samsung's dvb_dmxdev_init() carries capability probing for
# DMX_CAP_AUTO_BUFFER_FLUSH and stores the result in a local struct dmx_caps.
# Linux stable changed nearby initialization code. The .235 policy merge kept
# Samsung's capability block but lost only the local `caps` declaration,
# producing two undeclared-identifier errors. Restore that declaration while
# refusing to touch an unfamiliar function shape or remove vendor behavior.
dmxdev = kernel / "drivers/media/dvb-core/dmxdev.c"
text = dmxdev.read_text()
anchor = "int dvb_dmxdev_init("
if text.count(anchor) != 1:
    raise SystemExit("drivers/media/dvb-core/dmxdev.c: dvb_dmxdev_init anchor is not unique")
start = text.index(anchor)
end = function_end(text, start)
fn = text[start:end]

for required in (
    "DMX_CAP_AUTO_BUFFER_FLUSH",
    "overflow_auto_flush",
    "get_caps",
):
    if required not in fn:
        raise SystemExit(
            "drivers/media/dvb-core/dmxdev.c: Samsung capability block is incomplete: "
            + required
        )

decl_re = re.compile(r"(?m)^\s*struct\s+dmx_caps\s+caps\s*;\s*$")
declarations = decl_re.findall(fn)
if len(declarations) == 0:
    brace = fn.find("{")
    if brace < 0:
        raise SystemExit("drivers/media/dvb-core/dmxdev.c: init opening brace missing")
    fn = fn[:brace + 1] + "\n\tstruct dmx_caps caps;" + fn[brace + 1:]
    text = text[:start] + fn + text[end:]
    dmxdev.write_text(text)
    state = "restored-samsung-local-declaration"
elif len(declarations) == 1:
    state = "already-present"
else:
    raise SystemExit(
        "drivers/media/dvb-core/dmxdev.c: duplicate struct dmx_caps caps declarations"
    )

post = dmxdev.read_text()
post_start = post.index(anchor)
post_end = function_end(post, post_start)
post_fn = post[post_start:post_end]
if len(decl_re.findall(post_fn)) != 1:
    raise SystemExit("drivers/media/dvb-core/dmxdev.c: caps declaration postcondition failed")
for required in (
    "DMX_CAP_AUTO_BUFFER_FLUSH",
    "overflow_auto_flush",
    "get_caps",
):
    if required not in post_fn:
        raise SystemExit(
            "drivers/media/dvb-core/dmxdev.c: capability behavior lost after repair: "
            + required
        )

with report.open("a") as fh:
    fh.write(f"dvb_dmxdev_caps={state}\n")
PY

git -C "$KERNEL" diff --check
cat "$REPORT"
echo "Phase101 Linux 4.19.235 DVB compile-shape repair complete"
