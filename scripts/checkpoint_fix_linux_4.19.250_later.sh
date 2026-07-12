#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.250
REPORT="$ARTIFACTS_DIR/later-compile-api-fix-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION before later compile repair"

python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
report = Path(sys.argv[2])
repairs = []


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# Linux stable introduced fput_many(file, refs). The merged source retained its
# body under the fput name, leaving refs undeclared and creating a second fput.
file_table = root / "fs/file_table.c"
text = file_table.read_text()
old = (
    "void fput(struct file *file)\n"
    "{\n"
    "\tif (atomic_long_sub_and_test(refs, &file->f_count)) {\n"
)
new = (
    "void fput_many(struct file *file, unsigned int refs)\n"
    "{\n"
    "\tif (atomic_long_sub_and_test(refs, &file->f_count)) {\n"
)
if old in text:
    text = replace_once(text, old, new, "fput_many definition")
    file_table.write_text(text)
    repairs.append("fs/file_table.c=restored-fput-many-signature")

final = file_table.read_text()
if final.count("void fput_many(struct file *file, unsigned int refs)") != 1:
    raise SystemExit("fput_many definition validation failed")
if final.count("void fput(struct file *file)") != 1:
    raise SystemExit("fput definition count validation failed")


# The Samsung DVB extension queries demux capabilities, but the merge replaced
# its declaration list with the stable error-return variables.
dmxdev = root / "drivers/media/dvb-core/dmxdev.c"
text = dmxdev.read_text()
func_start = text.index(
    "int dvb_dmxdev_init(struct dmxdev *dmxdev, struct dvb_adapter *dvb_adapter)"
)
func_end = text.index("EXPORT_SYMBOL(dvb_dmxdev_init);", func_start)
segment = text[func_start:func_end]
if "struct dmx_caps caps;" not in segment:
    declaration = "\tint i, ret;\n"
    if segment.count(declaration) != 1:
        raise SystemExit(
            f"DVB init declaration anchor mismatch: {segment.count(declaration)}"
        )
    segment = segment.replace(
        declaration,
        declaration + "\tstruct dmx_caps caps;\n",
        1,
    )
    text = text[:func_start] + segment + text[func_end:]
    dmxdev.write_text(text)
    repairs.append("drivers/media/dvb-core/dmxdev.c=restored-demux-capability-local")

final = dmxdev.read_text()
func_start = final.index(
    "int dvb_dmxdev_init(struct dmxdev *dmxdev, struct dvb_adapter *dvb_adapter)"
)
func_end = final.index("EXPORT_SYMBOL(dvb_dmxdev_init);", func_start)
segment = final[func_start:func_end]
if segment.count("struct dmx_caps caps;") != 1:
    raise SystemExit("DVB capability declaration validation failed")
if "dmxdev->demux->get_caps(dmxdev->demux, &caps)" not in segment:
    raise SystemExit("DVB capability query is missing")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  fs/file_table.c drivers/media/dvb-core/dmxdev.c
info "Linux $TARGET_VERSION later compile mismatches repaired"
