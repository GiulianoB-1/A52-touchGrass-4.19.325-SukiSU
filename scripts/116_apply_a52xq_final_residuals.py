#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DISPLAY_ROOTS = ("drivers/a52_display", "techpack/display")


def read(path: Path) -> str:
    return path.read_text(errors="replace")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def remove_ion_flag_cached(gki: Path) -> int:
    path = gki / "a52-port-compat.h"
    content = read(path)
    content, a = re.subn(
        r"(?m)^#ifndef[ \t]+ION_FLAG_CACHED[ \t]*\n#define[ \t]+ION_FLAG_CACHED[^\n]*\n#endif[^\n]*\n?",
        "",
        content,
    )
    content, b = re.subn(
        r"(?m)^#define[ \t]+ION_FLAG_CACHED(?:[ \t].*)?\n?", "", content
    )
    if a or b:
        write(path, content)
    return a + b


def restore_real_ion_header(gki: Path) -> dict[str, object]:
    target = gki / "include/linux/ion.h"
    if not target.is_file():
        raise SystemExit(f"missing Android 5.10 public ION API header: {target}")
    target_text = read(target)
    if "struct dma_buf *ion_alloc(size_t len" not in target_text:
        raise SystemExit("ACK public ION header does not expose the dma-buf ion_alloc API")

    path = gki / "a52-compat/include/linux/ion_kernel.h"
    content = """/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef __A52_COMPAT_LINUX_ION_KERNEL_H__
#define __A52_COMPAT_LINUX_ION_KERNEL_H__
/* A52_PHASE14_REAL_ION_RUNTIME: use ACK 5.10 ION, never ENODEV stubs. */
#include <linux/ion.h>
#ifndef ION_FLAG_CACHED
#define ION_FLAG_CACHED 1
#endif
#endif
"""
    changed = not path.is_file() or read(path) != content
    if changed:
        write(path, content)
    return {"changed": changed, "target": str(target.relative_to(gki))}


def patch_qseecom_runtime_helpers(gki: Path) -> dict[str, int]:
    path = gki / "drivers/a52_secure/qseecom.c"
    content = read(path)
    changes = 0

    old = "static int qseecom_destroy_bridge_callback("
    new = "static int __maybe_unused qseecom_destroy_bridge_callback("
    if old in content and new not in content:
        content = content.replace(old, new, 1)
        changes += 1

    marker = "A52_QSEECOM_REAL_SECURE_VM_HELPERS"
    if marker not in content:
        anchor = '#include <soc/qcom/qtee_shmbridge.h>\n'
        if anchor not in content:
            raise SystemExit("qseecom include anchor not found")
        helpers = r'''

/* A52_QSEECOM_REAL_SECURE_VM_HELPERS
 * Workflow 116 previously supplied ENODEV/zero compile stubs for these two
 * vendor ION helpers. Keymaster reaches this path at runtime, so preserve the
 * TouchGrass flag-to-VMID behaviour locally while using ACK's real ion_alloc.
 */
static unsigned int a52_ion_get_flags_num_vm_elems(unsigned long flags)
{
	return hweight_long(flags & ION_FLAGS_CP_MASK);
}

static int a52_ion_flag_to_vmid(unsigned long flag)
{
	if (flag & ION_FLAG_CP_TOUCH) return VMID_CP_TOUCH;
	if (flag & ION_FLAG_CP_BITSTREAM) return VMID_CP_BITSTREAM;
	if (flag & ION_FLAG_CP_PIXEL) return VMID_CP_PIXEL;
	if (flag & ION_FLAG_CP_NON_PIXEL) return VMID_CP_NON_PIXEL;
	if (flag & ION_FLAG_CP_CAMERA) return VMID_CP_CAMERA;
	if (flag & ION_FLAG_CP_SEC_DISPLAY) return VMID_CP_SEC_DISPLAY;
	if (flag & ION_FLAG_CP_APP) return VMID_CP_APP;
	if (flag & ION_FLAG_CP_CAMERA_PREVIEW) return VMID_CP_CAMERA_PREVIEW;
	if (flag & ION_FLAG_CP_SPSS_SP) return VMID_CP_SPSS_SP;
	if (flag & ION_FLAG_CP_SPSS_SP_SHARED) return VMID_CP_SPSS_SP_SHARED;
	if (flag & ION_FLAG_CP_SPSS_HLOS_SHARED) return VMID_CP_SPSS_HLOS_SHARED;
	if (flag & ION_FLAG_CP_CDSP) return VMID_CP_CDSP;
	if (flag & ION_FLAG_CP_HLOS) return VMID_HLOS;
	return -EINVAL;
}

static int a52_ion_populate_vm_list(unsigned long flags, unsigned int *vm_list,
				    int nelems)
{
	unsigned int bit;
	int vmid;

	flags &= ION_FLAGS_CP_MASK;
	if (!flags || !vm_list || nelems <= 0)
		return -EINVAL;

	for_each_set_bit(bit, &flags, BITS_PER_LONG) {
		vmid = a52_ion_flag_to_vmid(1UL << bit);
		if (vmid < 0 || !nelems)
			return -EINVAL;
		vm_list[nelems - 1] = vmid;
		nelems--;
	}
	return 0;
}
'''
        content = content.replace(anchor, anchor + helpers, 1)
        changes += 1

    content, n1 = re.subn(
        r"\bion_get_flags_num_vm_elems\s*\(",
        "a52_ion_get_flags_num_vm_elems(",
        content,
    )
    content, n2 = re.subn(
        r"\bion_populate_vm_list\s*\(",
        "a52_ion_populate_vm_list(",
        content,
    )
    changes += n1 + n2
    write(path, content)
    return {"changes": changes, "vm_count_calls": n1, "vm_list_calls": n2}


def replace_legacy_header(
    gki: Path,
    name: str,
    candidates: tuple[str, ...],
    fallback: tuple[str, ...],
) -> dict[str, object]:
    wrapper = gki / "a52-compat/include/linux" / name
    selected = next((p for p in candidates if (gki / p).is_file()), None)
    guard = "__A52_COMPAT_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
    if selected:
        body = (
            "/* A52_PHASE14_DIRECT_HEADER_REDIRECT */\n"
            f'#include "../../../{selected}"\n'
        )
    else:
        body = "/* A52_PHASE14_LEGACY_HEADER_SHIM */\n" + "".join(
            f"#include <{item}>\n" for item in fallback
        )
    content = (
        "/* SPDX-License-Identifier: GPL-2.0-only */\n"
        f"#ifndef {guard}\n#define {guard}\n{body}#endif\n"
    )
    changed = not wrapper.is_file() or read(wrapper) != content
    if changed:
        write(wrapper, content)
    return {"changed": changed, "target": selected}


def remove_legacy_scm_asmeq(gki: Path) -> int:
    path = gki / "drivers/a52_secure/a52_legacy_scm.c"
    content = read(path)
    count = content.count("__asmeq(")
    content = re.sub(r"(?m)^[ \t]*__asmeq\([^\n]*\)[ \t]*\n", "", content)
    if "__asmeq(" in content:
        raise SystemExit("failed to remove legacy SCM __asmeq assertions")
    if count:
        write(path, content)
    return count


def function_bounds(content: str, signature: str) -> tuple[int, int]:
    start = content.find(signature)
    opening = content.find("{", start)
    if start < 0 or opening < 0:
        raise SystemExit(f"missing function {signature}")
    depth = 0
    for index in range(opening, len(content)):
        if content[index] == "{":
            depth += 1
        elif content[index] == "}":
            depth -= 1
            if depth == 0:
                return opening, index
    raise SystemExit(f"unterminated function {signature}")


def guard_adreno_coresight(gki: Path) -> int:
    path = gki / "drivers/gpu/msm/adreno_coresight.c"
    content = read(path)
    if "A52_PHASE14_CONFIG_OFF_CORESIGHT" in content:
        return 0
    opening, closing = function_bounds(
        content, "int adreno_coresight_init(struct adreno_device *adreno_dev)"
    )
    body = content[opening + 1 : closing]
    guarded = (
        "\n#ifndef CONFIG_CORESIGHT\n"
        "\t/* A52_PHASE14_CONFIG_OFF_CORESIGHT */\n"
        "\treturn 0;\n"
        "#else"
        + body
        + "#endif\n"
    )
    write(path, content[: opening + 1] + guarded + content[closing:])
    return 1


def patch_sde_crtc(gki: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for root in DISPLAY_ROOTS:
        path = gki / root / "msm/sde/sde_crtc.c"
        if not path.is_file():
            continue
        content = read(path)
        changes = 0
        for old, new in (
            ("\tstruct sde_hw_ds *hw_ds;", "\tstruct sde_hw_ds *hw_ds = NULL;"),
            ("\tstruct sde_hw_ds_cfg *cfg;", "\tstruct sde_hw_ds_cfg *cfg = NULL;"),
            ("\tstruct drm_plane *plane;", "\tstruct drm_plane *plane = NULL;"),
        ):
            if old in content and new not in content:
                content = content.replace(old, new, 1)
                changes += 1
        if changes:
            write(path, content)
        result[str(path.relative_to(gki))] = changes
    if not result:
        raise SystemExit("no staged SDE CRTC source found")
    return result


def validate(gki: Path) -> dict[str, bool]:
    ion = read(gki / "a52-compat/include/linux/ion_kernel.h")
    qsee = read(gki / "drivers/a52_secure/qseecom.c")
    public_ion = read(gki / "include/linux/ion.h")
    return {
        "real_ion_header": (
            "A52_PHASE14_REAL_ION_RUNTIME" in ion
            and "ERR_PTR(-ENODEV)" not in ion
        ),
        "ack_ion_redirect": "#include <linux/ion.h>" in ion,
        "ack_dma_buf_allocator": "struct dma_buf *ion_alloc(size_t len" in public_ion,
        "real_vm_helpers": "A52_QSEECOM_REAL_SECURE_VM_HELPERS" in qsee,
        "no_old_vm_helper_calls": not re.search(
            r"(?<!a52_)ion_(get_flags_num_vm_elems|populate_vm_list)\s*\(", qsee
        ),
        "qsee_callback_maybe_unused": (
            "static int __maybe_unused qseecom_destroy_bridge_callback(" in qsee
        ),
        "legacy_scm_clean": (
            "__asmeq(" not in read(gki / "drivers/a52_secure/a52_legacy_scm.c")
        ),
        "coresight_guard": (
            "A52_PHASE14_CONFIG_OFF_CORESIGHT"
            in read(gki / "drivers/gpu/msm/adreno_coresight.c")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gki", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    gki = args.gki.resolve()
    args.output.mkdir(parents=True, exist_ok=True)

    report = {
        "status": "phase14-keymaster-runtime-fix-staged",
        "flashable": False,
        "hardware_validated": False,
        "scope": "replace compile-only QSEE ION stubs with ACK ION and real secure VM helpers",
        "ion_macro_removals": remove_ion_flag_cached(gki),
        "ion_runtime": restore_real_ion_header(gki),
        "qseecom_runtime": patch_qseecom_runtime_helpers(gki),
        "header_replacements": {
            "dma-contiguous.h": replace_legacy_header(
                gki,
                "dma-contiguous.h",
                ("include/linux/dma-contiguous.h",),
                ("linux/dma-mapping.h", "linux/cma.h"),
            ),
            "dma-debug.h": replace_legacy_header(
                gki,
                "dma-debug.h",
                ("include/linux/dma-debug.h", "kernel/dma/debug.h"),
                ("linux/dma-mapping.h",),
            ),
        },
        "legacy_scm_asmeq_removed": remove_legacy_scm_asmeq(gki),
        "adreno_coresight_config_off_guarded": guard_adreno_coresight(gki),
        "sde_crtc": patch_sde_crtc(gki),
    }
    report["validation"] = validate(gki)
    bad = [name for name, passed in report["validation"].items() if not passed]
    (args.output / "phase14-final-residuals-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    if bad:
        raise SystemExit("Workflow 116 staging validation failed: " + ", ".join(bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
