#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"

TARGET_VERSION=4.19.325
REPORT="$ARTIFACTS_DIR/pcm-loop-fput-compat-$TARGET_VERSION.txt"

test -d "$KERNEL_DIR/.git" || fail "Kernel source is missing"
test "$(kernel_version)" = "$TARGET_VERSION" || fail "Expected Linux $TARGET_VERSION"

info "Applying ALSA PCM, loop-device, and file-reference compatibility repairs"
python3 - "$KERNEL_DIR" "$REPORT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
report = Path(sys.argv[2])
repairs = []


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} anchor mismatch: {count}")
    return text.replace(old, new, 1)


# snd_pcm_hw_free() takes the runtime buffer-access lock and has an error jump
# to unlock, but the direct merge dropped the matching label and unlock call.
path = root / "sound/core/pcm_native.c"
text = path.read_text()
start = text.index("static int snd_pcm_hw_free(struct snd_pcm_substream *substream)")
end = text.index("\nstatic int snd_pcm_sw_params", start)
segment = text[start:end]
if "snd_pcm_buffer_access_unlock(runtime);" not in segment:
    old = (
        "\tif (pm_qos_request_active(&substream->latency_pm_qos_req))\n"
        "\t\tpm_qos_remove_request(&substream->latency_pm_qos_req);\n"
        "\treturn result;\n"
    )
    new = (
        "\tif (pm_qos_request_active(&substream->latency_pm_qos_req))\n"
        "\t\tpm_qos_remove_request(&substream->latency_pm_qos_req);\n"
        " unlock:\n"
        "\tsnd_pcm_buffer_access_unlock(runtime);\n"
        "\treturn result;\n"
    )
    segment = replace_once(segment, old, new, "PCM hw-free unlock")
    text = text[:start] + segment + text[end:]
    path.write_text(text)
    repairs.append("sound/core/pcm_native.c=restored-buffer-access-unlock")


# The loop merge retained two functions named loop_set_status_from_info(). The
# second one is the lock/freeze wrapper loop_set_status(). Restore that name,
# its unfreeze error path, and remove duplicated offset/size code that belongs
# to the helper. Also calculate the initial configured size after applying the
# requested offset and size limit.
path = root / "drivers/block/loop.c"
text = path.read_text()
definition = "loop_set_status_from_info(struct loop_device *lo,"
positions = []
pos = 0
while True:
    pos = text.find(definition, pos)
    if pos < 0:
        break
    positions.append(pos)
    pos += len(definition)
if len(positions) == 2:
    second = positions[1]
    end = text.index("\nstatic int\nloop_get_status", second)
    segment = text[second:end]
    segment = segment.replace(
        definition,
        "loop_set_status(struct loop_device *lo,",
        1,
    )
    segment = replace_once(
        segment,
        "\terr = loop_set_status_from_info(lo, info);\n"
        "\tif (err)\n"
        "\t\treturn err;\n",
        "\terr = loop_set_status_from_info(lo, info);\n"
        "\tif (err)\n"
        "\t\tgoto out_unfreeze;\n",
        "loop status helper error path",
    )
    duplicate = (
        "\tnew_size = get_size(info->lo_offset, info->lo_sizelimit,\n"
        "\t\t\t    lo->lo_backing_file);\n"
        "\tif ((loff_t)(sector_t)new_size != new_size)\n"
        "\t\treturn -EFBIG;\n"
        "\n"
        "\tlo->lo_offset = info->lo_offset;\n"
        "\tlo->lo_sizelimit = info->lo_sizelimit;\n"
        "\n"
    )
    segment = replace_once(segment, duplicate, "", "loop duplicated size block")
    dio_anchor = "\t/* update dio if lo_offset or transfer is changed */\n"
    if "\tloop_config_discard(lo);\n" not in segment:
        segment = replace_once(
            segment,
            dio_anchor,
            "\tloop_config_discard(lo);\n\n" + dio_anchor,
            "loop discard configuration",
        )
    text = text[:second] + segment + text[end:]
    repairs.append("drivers/block/loop.c=restored-loop-set-status-wrapper")
elif len(positions) != 1:
    raise SystemExit(f"unexpected loop status helper definition count: {len(positions)}")

# Reject offset and size-limit values that cannot fit in loff_t before assigning
# them in the shared helper.
helper_start = text.index("loop_set_status_from_info(struct loop_device *lo,")
helper_end = text.index("\nstatic int loop_configure", helper_start)
helper = text[helper_start:helper_end]
if "info->lo_offset > LLONG_MAX" not in helper:
    anchor = "\tif ((unsigned int) info->lo_encrypt_key_size > LO_KEY_SIZE)\n\t\treturn -EINVAL;\n\n"
    addition = (
        anchor
        + "\t/* Avoid assigning unsigned values that overflow loff_t. */\n"
        + "\tif (info->lo_offset > LLONG_MAX || info->lo_sizelimit > LLONG_MAX)\n"
        + "\t\treturn -EOVERFLOW;\n\n"
    )
    helper = replace_once(helper, anchor, addition, "loop offset overflow guard")
    text = text[:helper_start] + helper + text[helper_end:]
    repairs.append("drivers/block/loop.c=restored-offset-overflow-check")

# The merged configure path calculated capacity before applying config.info.
# Move the calculation after the helper and validate sector_t representability.
config_start = text.index("static int loop_configure(struct loop_device *lo")
config_end = text.index("\nstatic int __loop_clr_fd", config_start)
config = text[config_start:config_end]
early_size = "\tsize = get_loop_size(lo, file);\n\n"
if early_size in config:
    config = replace_once(config, early_size, "", "loop early size calculation")
    anchor = (
        "\terror = loop_set_status_from_info(lo, &config->info);\n"
        "\tif (error)\n"
        "\t\tgoto out_unlock;\n\n"
    )
    replacement = (
        anchor
        + "\tsize = get_size(lo->lo_offset, lo->lo_sizelimit, file);\n"
        + "\tif ((loff_t)(sector_t)size != size) {\n"
        + "\t\terror = -EFBIG;\n"
        + "\t\tgoto out_unlock;\n"
        + "\t}\n\n"
    )
    config = replace_once(config, anchor, replacement, "loop configured size calculation")
    text = text[:config_start] + config + text[config_end:]
    repairs.append("drivers/block/loop.c=calculated-size-after-config-info")
path.write_text(text)


# Linux 4.19.325 introduced fput_many(file, refs). The merge kept its body but
# named it fput(), creating both an undeclared refs variable and a duplicate
# fput definition.
path = root / "fs/file_table.c"
text = path.read_text()
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
    path.write_text(text)
    repairs.append("fs/file_table.c=restored-fput-many-signature")


# Exact postconditions.
pcm = (root / "sound/core/pcm_native.c").read_text()
start = pcm.index("static int snd_pcm_hw_free(struct snd_pcm_substream *substream)")
end = pcm.index("\nstatic int snd_pcm_sw_params", start)
pcm_segment = pcm[start:end]
if pcm_segment.count("goto unlock;") != 1 or pcm_segment.count(" unlock:") != 1:
    raise SystemExit("PCM hw-free unlock label validation failed")
if pcm_segment.count("snd_pcm_buffer_access_unlock(runtime);") != 1:
    raise SystemExit("PCM buffer unlock validation failed")

loop = (root / "drivers/block/loop.c").read_text()
if loop.count("loop_set_status_from_info(struct loop_device *lo,") != 1:
    raise SystemExit("loop helper definition count validation failed")
if loop.count("loop_set_status(struct loop_device *lo,") != 1:
    raise SystemExit("loop status wrapper definition validation failed")
status_start = loop.index("loop_set_status(struct loop_device *lo,")
status_end = loop.index("\nstatic int\nloop_get_status", status_start)
status = loop[status_start:status_end]
if "new_size = get_size" in status or "return err;\n\n\t/* Mask" in status:
    raise SystemExit("stale loop status merge fragments remain")
if "goto out_unfreeze;" not in status or "loop_config_discard(lo);" not in status:
    raise SystemExit("loop status cleanup/configuration validation failed")

files = (root / "fs/file_table.c").read_text()
if files.count("void fput_many(struct file *file, unsigned int refs)") != 1:
    raise SystemExit("fput_many definition validation failed")
if files.count("void fput(struct file *file)") != 1:
    raise SystemExit("fput definition count validation failed")

report.write_text("\n".join(repairs or ["repairs=already-present"]) + "\n")
print(report.read_text(), end="")
PY

info "ALSA PCM, loop-device, and file-reference compatibility repairs applied"
