#!/usr/bin/env python3
"""Phase 258 A/B: retain Phase257 KGSL publication tracing, remove namei hooks.

This is a controlled regression-isolation build. It preserves Phase257 recorder
admission/retention, KGSL initial KOBJ_ADD/coldboot/DEVNAME instrumentation, and
the late /dev/kgsl-3d0 publication snapshot. It deliberately leaves fs/namei.c
byte-for-byte untouched by Phase257: no mknod/mknodat or unlink/unlinkat hook,
no F257 mk/ul records, and no s4/s5 node snapshot.

No KGSL, GPU, IOMMU, devtmpfs, SELinux, ramdisk, ueventd, major/minor, or return
value semantics are changed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "257_phase256_kgsl_publication_pipeline_overlay.py"
AB_MARKER = "A52_PHASE258_NO_NAMEI_AB_V1"


def load_base():
    if not BASE.is_file():
        raise RuntimeError(f"missing Phase257 base overlay: {BASE}")
    spec = importlib.util.spec_from_file_location("a52_phase257_base_for_258", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Phase257 base overlay")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


p257 = load_base()


def patch_namei_noop(path: Path) -> None:
    before = path.read_bytes()
    text = before.decode("utf-8")
    forbidden = (
        p257.NAMEI_MARKER,
        "A52_PHASE257_NAMEI_ANDROID510_SYSCALL_REPAIR_V1",
        "a52_r257_kgsl_node_event",
        "a52_r257_kgsl_node_snapshot",
        "F257 mk",
        "F257 ul",
        "F257 s4",
        "F257 s5",
    )
    present = [token for token in forbidden if token in text]
    if present:
        raise RuntimeError(
            f"{path}: Phase258 requires pristine pre-Phase257 namei; found {present}"
        )
    if path.read_bytes() != before:
        raise RuntimeError(f"{path}: Phase258 no-op unexpectedly changed namei")


def patch_open_no_namei(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if AB_MARKER in text:
        return

    decl_anchor = "extern void a52_r225_kgsl_late_snapshot(int open_rc);\n"
    text = p257.replace_once(
        text,
        decl_anchor,
        decl_anchor
        + f"/* {p257.OPEN_MARKER} */\n"
        + f"/* {AB_MARKER}: namei syscall probe intentionally absent */\n"
        + "extern void a52_r257_kgsl_pub_snapshot(int open_rc);\n",
        f"{path}: Phase258 publication-only declarations",
    )

    call_anchor = '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0"))
\t\ta52_r225_kgsl_late_snapshot(fd);
'''
    call_new = '''\tif (!strcmp(tmp->name, "/dev/kgsl-3d0")) {
\t\ta52_r225_kgsl_late_snapshot(fd);
\t\ta52_r257_kgsl_pub_snapshot(fd);
\t}
'''
    text = p257.replace_once(
        text,
        call_anchor,
        call_new,
        f"{path}: Phase258 publication-only late re-emission",
    )
    path.write_text(text, encoding="utf-8")


def verify(root: Path) -> None:
    checks = {
        "drivers/a52_secure/a52_ack_secure_flight_recorder.c": (
            p257.MARKER,
            'strncmp(fmt, "F257", 4)',
            '!strncmp(message, "F257 ", 5)',
        ),
        "drivers/base/core.c": (
            p257.CORE_MARKER,
            p257.REPLAY_MARKER,
            p257.META_MARKER,
            "a52_r257_kgsl_pub_snapshot",
            "F257 add",
            "F257 wr",
            "F257 md",
            "F257 s1",
            "F257 s2",
            "F257 s3",
            "kobject_synth_uevent",
        ),
        "fs/open.c": (
            p257.OPEN_MARKER,
            AB_MARKER,
            "a52_r257_kgsl_pub_snapshot(fd)",
            '"/dev/kgsl-3d0"',
        ),
    }
    for rel, tokens in checks.items():
        text = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                raise RuntimeError(f"Phase258 verification {rel}: missing {token!r}")

    namei = (root / "fs/namei.c").read_text(encoding="utf-8")
    for token in (
        p257.NAMEI_MARKER,
        "A52_PHASE257_NAMEI_ANDROID510_SYSCALL_REPAIR_V1",
        "a52_r257_kgsl_node_event",
        "a52_r257_kgsl_node_snapshot",
        "F257 mk",
        "F257 ul",
        "F257 s4",
        "F257 s5",
    ):
        if token in namei:
            raise RuntimeError(f"Phase258 verification fs/namei.c: forbidden {token!r}")

    open_text = (root / "fs/open.c").read_text(encoding="utf-8")
    if "a52_r257_kgsl_node_snapshot" in open_text:
        raise RuntimeError("Phase258 open.c still references the removed namei snapshot")

    core = (root / "drivers/base/core.c").read_text(encoding="utf-8")
    if core.count("A52_PHASE256_KGSL_DEVNODE_UEVENT_V1") != 1:
        raise RuntimeError("Phase258 changed Phase256 KOBJ_ADD marker cardinality")


def self_test() -> None:
    old_namei = p257.patch_namei
    old_open = p257.patch_open
    old_verify = p257.verify
    try:
        p257.patch_namei = patch_namei_noop
        p257.patch_open = patch_open_no_namei
        p257.verify = verify
        p257.self_test()
    finally:
        p257.patch_namei = old_namei
        p257.patch_open = old_open
        p257.verify = old_verify
    print(
        "Phase 258 no-namei A/B self-test: PASS "
        "(Phase257 publication retained; namei byte path untouched)",
        flush=True,
    )


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    root = p257.locate(sys.argv[1:])
    namei = root / "fs/namei.c"
    namei_before = namei.read_bytes()

    p257.patch_recorder(root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c")
    p257.patch_core(root / "drivers/base/core.c")
    patch_namei_noop(namei)
    patch_open_no_namei(root / "fs/open.c")
    verify(root)

    if namei.read_bytes() != namei_before:
        raise RuntimeError("Phase258 invariant failed: fs/namei.c changed")

    print(
        f"{AB_MARKER}: Phase257 publication tracing retained; "
        "Phase257 namei mknod/unlink instrumentation removed",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
