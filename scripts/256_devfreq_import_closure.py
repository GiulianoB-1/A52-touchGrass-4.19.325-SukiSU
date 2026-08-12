#!/usr/bin/env python3
"""Phase 256 devfreq import closure and Android 5.10 API compatibility.

The Phase256 Adreno TZ/GPUBW governor import depends on Qualcomm downstream
headers absent from the Android 5.10 GKI source. Earlier phases already provide
the real legacy SCM, QTEE shmbridge and secure-buffer implementations. This
helper therefore stages only the missing downstream declarations/trace contract,
then applies the smallest mechanical 4.19 -> 5.10 API translations required by
the imported Adreno TZ governor.

It does not enable CONFIG_QCOM_SECURE_BUFFER, add another secure implementation,
change KGSL policy, alter DT/ramdisk, or weaken security behavior.
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
MARKER = "A52_PHASE256_DEVFREQ_IMPORT_CLOSURE_V2"
SECURE_BUFFER_MARKER = "A52_PHASE256_SECURE_BUFFER_DECL_COMPAT_V1"
ADRENO_TZ_MARKER = "A52_PHASE256_ADRENO_TZ_GKI510_COMPAT_V1"

PINNED_FILES = (
    "include/soc/qcom/scm.h",
    "drivers/devfreq/devfreq_trace.h",
    "include/soc/qcom/qtee_shmbridge.h",
)
SECURE_BUFFER = "include/soc/qcom/secure_buffer.h"
ADRENO_TZ = "drivers/devfreq/governor_msm_adreno_tz.c"


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
        governor = root / ADRENO_TZ
        header = root / "include/linux/msm_adreno_devfreq.h"
        legacy_scm = root / "drivers/a52_secure/a52_legacy_scm.c"
        qtee_impl = root / "drivers/a52_secure/qtee_shmbridge.c"
        secure_impl = root / "drivers/a52_secure/secure_buffer.c"
        if not all(
            path.is_file()
            for path in (governor, header, legacy_scm, qtee_impl, secure_impl)
        ):
            continue
        key = root.resolve()
        if key not in seen:
            seen.add(key)
            hits.append(root)
    if len(hits) != 1:
        raise RuntimeError(
            "expected one generated Phase256 source root with legacy SCM/QTEE/secure-buffer, found "
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


def stage_pinned_headers(root: Path) -> None:
    for relative in PINNED_FILES:
        target = root / relative
        if target.is_file():
            print(f"P256 closure retained existing {relative}", flush=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch(relative))
        print(f"P256 closure staged {relative}", flush=True)


def declarations_only_secure_buffer(text: str) -> str:
    """Keep TouchGrass ABI/types/prototypes but remove CONFIG-disabled stubs."""
    if SECURE_BUFFER_MARKER in text:
        return text

    gate = "#ifdef CONFIG_QCOM_SECURE_BUFFER\n"
    branch = "#else\n"
    if text.count(gate) != 1:
        raise RuntimeError(
            "TouchGrass secure_buffer.h CONFIG_QCOM_SECURE_BUFFER gate drifted"
        )
    prefix, conditional = text.split(gate, 1)
    if conditional.count(branch) != 1:
        raise RuntimeError("TouchGrass secure_buffer.h CONFIG branch drifted")
    declarations, stubs = conditional.split(branch, 1)
    if not stubs.rstrip().endswith("#endif\n#endif"):
        raise RuntimeError("TouchGrass secure_buffer.h closing guards drifted")

    required = (
        "enum vmid",
        "struct dest_vm_and_perm_info",
        "struct mem_prot_info",
        "int msm_secure_table(struct sg_table *table);",
        "int msm_unsecure_table(struct sg_table *table);",
        "int hyp_assign_table(struct sg_table *table,",
        "int try_hyp_assign_table(struct sg_table *table,",
        "extern int hyp_assign_phys(phys_addr_t addr, u64 size,",
        "bool msm_secure_v2_is_supported(void);",
        "const char *msm_secure_vmid_to_string(int secure_vmid);",
        "u32 msm_secure_get_vmid_perms(u32 vmid);",
    )
    for token in required:
        if token not in declarations and token not in prefix:
            raise RuntimeError(f"TouchGrass secure_buffer.h missing {token!r}")

    return (
        prefix
        + f"/* {SECURE_BUFFER_MARKER}\n"
        + " * Android 5.10 compatibility: the generated A52 tree already builds\n"
        + " * drivers/a52_secure/secure_buffer.c. Keep the downstream ABI and\n"
        + " * declarations, but never emit CONFIG-disabled static-inline stubs\n"
        + " * that would redefine that real implementation.\n"
        + " */\n"
        + declarations.rstrip()
        + "\n\n#endif /* __QCOM_SECURE_BUFFER_H__ */\n"
    )


def stage_secure_buffer_header(root: Path) -> None:
    target = root / SECURE_BUFFER
    if target.is_file():
        text = target.read_text(encoding="utf-8")
        if SECURE_BUFFER_MARKER in text:
            print(f"P256 closure retained compatible {SECURE_BUFFER}", flush=True)
            return
        # A native declarations-only header is safe to retain. Only transform the
        # known downstream CONFIG-gated header that carries conflicting stubs.
        if "#ifdef CONFIG_QCOM_SECURE_BUFFER" not in text:
            print(f"P256 closure retained native {SECURE_BUFFER}", flush=True)
            return
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = fetch(SECURE_BUFFER).decode("utf-8")

    target.write_text(declarations_only_secure_buffer(text), encoding="utf-8")
    print(f"P256 closure staged declarations-only {SECURE_BUFFER}", flush=True)


def patch_adreno_tz_text(text: str) -> str:
    if ADRENO_TZ_MARKER in text:
        return text

    old_flush_ca = """dmac_flush_range(tz_buf,\n\t\ttz_buf + PAGE_ALIGN(sizeof(tz_ca_data)));"""
    new_flush_ca = "__flush_dcache_area(tz_buf, PAGE_ALIGN(sizeof(tz_ca_data)));"
    old_flush_levels = "dmac_flush_range(tz_buf, tz_buf + PAGE_ALIGN(size_pwrlevels));"
    new_flush_levels = "__flush_dcache_area(tz_buf, PAGE_ALIGN(size_pwrlevels));"

    checks = (
        (old_flush_ca, 1, "CA dcache flush"),
        (old_flush_levels, 1, "power-level dcache flush"),
        ("kzfree(tz_buf);", 2, "sensitive TZ buffer free"),
        ("case DEVFREQ_GOV_INTERVAL:", 1, "devfreq interval event"),
    )
    for token, expected, label in checks:
        count = text.count(token)
        if count != expected:
            raise RuntimeError(
                f"Phase256 Adreno TZ {label} anchor drifted: expected {expected}, found {count}"
            )

    text = text.replace(old_flush_ca, new_flush_ca, 1)
    text = text.replace(old_flush_levels, new_flush_levels, 1)
    text = text.replace("kzfree(tz_buf);", "kfree_sensitive(tz_buf);")
    text = text.replace(
        "case DEVFREQ_GOV_INTERVAL:", "case DEVFREQ_GOV_UPDATE_INTERVAL:", 1
    )

    marker_anchor = "#include <asm/cacheflush.h>\n"
    if text.count(marker_anchor) != 1:
        raise RuntimeError("Phase256 Adreno TZ cacheflush include anchor drifted")
    text = text.replace(
        marker_anchor,
        marker_anchor
        + f"/* {ADRENO_TZ_MARKER}: mechanical Android 5.10 API translation only. */\n",
        1,
    )
    return text


def patch_adreno_tz(root: Path) -> None:
    target = root / ADRENO_TZ
    text = target.read_text(encoding="utf-8")
    patched = patch_adreno_tz_text(text)
    if patched != text:
        target.write_text(patched, encoding="utf-8")
        print("P256 closure translated Adreno TZ governor to Android 5.10 APIs", flush=True)
    else:
        print("P256 closure retained Android 5.10 Adreno TZ compatibility", flush=True)


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

    secure_header = root / SECURE_BUFFER
    require_tokens(
        secure_header,
        (
            "enum vmid",
            "PERM_READ",
            "PERM_WRITE",
            "int msm_secure_table(struct sg_table *table);",
            "int hyp_assign_table(struct sg_table *table,",
            "extern int hyp_assign_phys(phys_addr_t addr, u64 size,",
            "bool msm_secure_v2_is_supported(void);",
        ),
    )
    secure_text = secure_header.read_text(encoding="utf-8")
    for forbidden in (
        "#ifdef CONFIG_QCOM_SECURE_BUFFER",
        "static inline int msm_secure_table",
        "static inline int hyp_assign_table",
        "static inline int hyp_assign_phys",
        "static inline bool msm_secure_v2_is_supported",
    ):
        if forbidden in secure_text:
            raise RuntimeError(
                f"Phase256 secure_buffer declaration compatibility retained forbidden stub {forbidden!r}"
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
    require_tokens(
        root / "drivers/a52_secure/secure_buffer.c",
        (
            "msm_secure_table",
            "hyp_assign_table",
            "hyp_assign_phys",
            "msm_secure_v2_is_supported",
        ),
    )

    config = locate_config(root).read_text(encoding="utf-8")
    if "CONFIG_QCOM_SCM=y" not in config:
        raise RuntimeError("Phase256 devfreq closure requires CONFIG_QCOM_SCM=y")
    if "CONFIG_QCOM_SECURE_BUFFER=y" in config:
        raise RuntimeError(
            "Phase256 must not add CONFIG_QCOM_SECURE_BUFFER; existing A52 implementation is reused"
        )

    tz_path = root / ADRENO_TZ
    tz = tz_path.read_text(encoding="utf-8")
    for token in (
        ADRENO_TZ_MARKER,
        "#include <soc/qcom/scm.h>",
        "#include <soc/qcom/qtee_shmbridge.h>",
        "__flush_dcache_area(tz_buf, PAGE_ALIGN(sizeof(tz_ca_data)))",
        "__flush_dcache_area(tz_buf, PAGE_ALIGN(size_pwrlevels))",
        "kfree_sensitive(tz_buf);",
        "case DEVFREQ_GOV_UPDATE_INTERVAL:",
    ):
        if token not in tz:
            raise RuntimeError(f"Phase256 Adreno TZ compatibility missing {token!r}")
    for forbidden in (
        "dmac_flush_range(",
        "kzfree(tz_buf);",
        "case DEVFREQ_GOV_INTERVAL:",
    ):
        if forbidden in tz:
            raise RuntimeError(f"Phase256 Adreno TZ legacy API remains: {forbidden!r}")

    gpubw = (root / "drivers/devfreq/governor_gpubw_mon.c").read_text(encoding="utf-8")
    if '#include "devfreq_trace.h"' not in gpubw:
        raise RuntimeError("Phase256 GPUBW governor no longer includes devfreq_trace.h")


def self_test() -> None:
    assert TOUCHGRASS_COMMIT == "6bf351bdf18bdb228db79e66f14a7a9c0178e5d7"
    assert SECURE_BUFFER not in PINNED_FILES

    secure_fixture = """#ifndef __QCOM_SECURE_BUFFER_H__
#define __QCOM_SECURE_BUFFER_H__
#include <linux/scatterlist.h>
enum vmid { VMID_HLOS = 3 };
struct dest_vm_and_perm_info { u32 vm; };
struct mem_prot_info { phys_addr_t addr; };
#ifdef CONFIG_QCOM_SECURE_BUFFER
int msm_secure_table(struct sg_table *table);
int msm_unsecure_table(struct sg_table *table);
int hyp_assign_table(struct sg_table *table,
    u32 *source_vm_list, int source_nelems,
    int *dest_vmids, int *dest_perms, int dest_nelems);
int try_hyp_assign_table(struct sg_table *table,
    u32 *source_vm_list, int source_nelems,
    int *dest_vmids, int *dest_perms, int dest_nelems);
extern int hyp_assign_phys(phys_addr_t addr, u64 size,
    u32 *source_vmlist, int source_nelems,
    int *dest_vmids, int *dest_perms, int dest_nelems);
bool msm_secure_v2_is_supported(void);
const char *msm_secure_vmid_to_string(int secure_vmid);
u32 msm_secure_get_vmid_perms(u32 vmid);
#else
static inline int msm_secure_table(struct sg_table *table) { return -1; }
#endif
#endif
"""
    secure = declarations_only_secure_buffer(secure_fixture)
    if "#ifdef CONFIG_QCOM_SECURE_BUFFER" in secure or "static inline" in secure:
        raise AssertionError("secure-buffer declaration-only transform retained stubs")
    if SECURE_BUFFER_MARKER not in secure or "int hyp_assign_table" not in secure:
        raise AssertionError("secure-buffer declaration-only transform lost ABI declarations")
    if declarations_only_secure_buffer(secure) != secure:
        raise AssertionError("secure-buffer transform is not idempotent")

    tz_fixture = """#include <asm/cacheflush.h>
void f(void) {
dmac_flush_range(tz_buf,
\t\ttz_buf + PAGE_ALIGN(sizeof(tz_ca_data)));
kzfree(tz_buf);
dmac_flush_range(tz_buf, tz_buf + PAGE_ALIGN(size_pwrlevels));
kzfree(tz_buf);
case DEVFREQ_GOV_INTERVAL:
    break;
}
"""
    tz = patch_adreno_tz_text(tz_fixture)
    for token in (
        ADRENO_TZ_MARKER,
        "__flush_dcache_area(tz_buf, PAGE_ALIGN(sizeof(tz_ca_data)))",
        "__flush_dcache_area(tz_buf, PAGE_ALIGN(size_pwrlevels))",
        "kfree_sensitive(tz_buf);",
        "DEVFREQ_GOV_UPDATE_INTERVAL",
    ):
        if token not in tz:
            raise AssertionError(f"Adreno TZ compatibility self-test missing {token!r}")
    if patch_adreno_tz_text(tz) != tz:
        raise AssertionError("Adreno TZ compatibility transform is not idempotent")

    source = Path(__file__).read_text(encoding="utf-8")
    for token in (
        MARKER,
        SECURE_BUFFER_MARKER,
        ADRENO_TZ_MARKER,
        "CONFIG_QCOM_SCM=y",
        "CONFIG_QCOM_SECURE_BUFFER=y",
        "a52_legacy_scm.c",
        "qtee_shmbridge.c",
        "secure_buffer.c",
        "kfree_sensitive",
        "DEVFREQ_GOV_UPDATE_INTERVAL",
    ):
        if token not in source:
            raise RuntimeError(f"Phase256 devfreq closure self-test missing {token!r}")
    print("Phase 256 devfreq import/API-compat self-test: PASS", flush=True)


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0
    root = locate(sys.argv[1:])
    stage_pinned_headers(root)
    stage_secure_buffer_header(root)
    patch_adreno_tz(root)
    audit(root)
    print(f"{MARKER}: downstream devfreq closure + Android 5.10 API compatibility applied", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
