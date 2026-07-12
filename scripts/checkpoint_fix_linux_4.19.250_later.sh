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


# The 4.19.250 merge retained the new clone_private_mount() invalid gotos but
# lost their cleanup label, and also retained the older Samsung copy of
# has_locked_children(). Restore the cleanup and keep one KDP-aware helper.
namespace = root / "fs/namespace.c"
text = namespace.read_text()
clone_start = text.index("struct vfsmount *clone_private_mount")
clone_end = text.index("EXPORT_SYMBOL_GPL(clone_private_mount);", clone_start)
clone_segment = text[clone_start:clone_end]
cleanup = "invalid:\n\tup_read(&namespace_sem);\n\treturn ERR_PTR(-EINVAL);\n"
if cleanup not in clone_segment:
    return_block = (
        "#ifdef CONFIG_KDP_NS\n"
        "\treturn new_mnt->mnt;\n"
        "#else\n"
        "\treturn &new_mnt->mnt;\n"
        "#endif\n"
        "}\n"
    )
    if clone_segment.count(return_block) != 1:
        raise SystemExit(
            f"clone_private_mount cleanup anchor mismatch: {clone_segment.count(return_block)}"
        )
    clone_segment = clone_segment.replace(
        return_block,
        return_block[:-2] + "\n" + cleanup + "}\n",
        1,
    )
    text = text[:clone_start] + clone_segment + text[clone_end:]
    namespace.write_text(text)
    repairs.append("fs/namespace.c=restored-clone-private-mount-invalid-cleanup")

text = namespace.read_text()
helper_sig = "static bool has_locked_children(struct mount *mnt, struct dentry *dentry)\n"
helper_positions = []
pos = 0
while True:
    pos = text.find(helper_sig, pos)
    if pos < 0:
        break
    helper_positions.append(pos)
    pos += len(helper_sig)

if len(helper_positions) == 2:
    second = helper_positions[1]
    brace = text.index("{", second)
    depth = 0
    end = None
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise SystemExit("could not locate duplicate has_locked_children end")
    while end < len(text) and text[end] in "\r\n":
        end += 1
    text = text[:second] + text[end:]
    namespace.write_text(text)
    repairs.append("fs/namespace.c=removed-duplicate-has-locked-children")
elif len(helper_positions) != 1:
    raise SystemExit(
        f"unexpected has_locked_children definition count: {len(helper_positions)}"
    )

final_ns = namespace.read_text()
if final_ns.count(helper_sig) != 1:
    raise SystemExit("namespace helper count validation failed")
clone_start = final_ns.index("struct vfsmount *clone_private_mount")
clone_end = final_ns.index("EXPORT_SYMBOL_GPL(clone_private_mount);", clone_start)
if cleanup not in final_ns[clone_start:clone_end]:
    raise SystemExit("clone_private_mount cleanup validation failed")


# Preserve Samsung's MMC bus callbacks consumed by sd.c. The stable merge kept
# the initializers but dropped the matching fields from struct mmc_bus_ops.
core = root / "drivers/mmc/core/core.h"
text = core.read_text()
struct_start = text.index("struct mmc_bus_ops {\n")
struct_end = text.index("};\n", struct_start) + 3
segment = text[struct_start:struct_end]

members = {
    "deferred_resume": "\tint (*deferred_resume)(struct mmc_host *host);\n",
    "change_bus_speed": "\tint (*change_bus_speed)(struct mmc_host *host, unsigned long *freq);\n",
    "change_bus_speed_deferred": (
        "\tint (*change_bus_speed_deferred)(struct mmc_host *host,\n"
        "\t\t\t\t\t\t\tunsigned long *freq);\n"
    ),
}

if members["deferred_resume"] not in segment:
    anchor = "\tint (*resume)(struct mmc_host *);\n"
    if segment.count(anchor) != 1:
        raise SystemExit(f"MMC resume anchor mismatch: {segment.count(anchor)}")
    segment = segment.replace(anchor, anchor + members["deferred_resume"], 1)
    repairs.append("drivers/mmc/core/core.h=restored-deferred-resume-callback")

speed_block = members["change_bus_speed"] + members["change_bus_speed_deferred"]
if members["change_bus_speed"] not in segment or members["change_bus_speed_deferred"] not in segment:
    anchor_candidates = (
        "\tbool (*cache_enabled)(struct mmc_host *);\n",
        "\tint (*sw_reset)(struct mmc_host *);\n",
    )
    anchor = next((item for item in anchor_candidates if segment.count(item) == 1), None)
    if anchor is None:
        raise SystemExit("MMC speed callback anchor is not recognized")
    segment = segment.replace(anchor, anchor + speed_block, 1)
    repairs.append("drivers/mmc/core/core.h=restored-bus-speed-callbacks")

text = text[:struct_start] + segment + text[struct_end:]
core.write_text(text)

final_core = core.read_text()
final_start = final_core.index("struct mmc_bus_ops {\n")
final_end = final_core.index("};\n", final_start) + 3
final_segment = final_core[final_start:final_end]
for name, member in members.items():
    if final_segment.count(member) != 1:
        raise SystemExit(f"MMC {name} callback validation failed")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

git -C "$KERNEL_DIR" diff --check -- \
  fs/file_table.c \
  fs/namespace.c \
  drivers/media/dvb-core/dmxdev.c \
  drivers/mmc/core/core.h
info "Linux $TARGET_VERSION later compile mismatches repaired"
