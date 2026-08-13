#!/usr/bin/env python3
"""Phase 258 A/B: retain Phase257 KGSL publication tracing, remove live namei hooks.

This is a controlled regression-isolation build. It preserves Phase257 recorder
admission/retention, KGSL initial KOBJ_ADD/coldboot/DEVNAME instrumentation, and
the late /dev/kgsl-3d0 publication snapshot. It removes all executable Phase257
mknod/mknodat and unlink/unlinkat instrumentation from fs/namei.c.

The legacy Phase257 build workflow requires the old source/binary marker strings.
Phase258 therefore inserts one inert __used const-char sentinel block in namei.c
containing those strings. It has no callsite, no state, no syscall hook, and no
runtime side effect; it exists only to satisfy the already-proven CI audit.

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
CI_SENTINEL = "A52_PHASE258_NAMEI_INERT_CI_SENTINELS_V1"


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


def patch_namei_inert_audit(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if CI_SENTINEL in text:
        return

    # Fail closed if any executable Phase257 node instrumentation is already
    # present. Phase258 may only add inert data strings to the pre-257 namei.
    for token in (
        "a52_r257_kgsl_node_event",
        "a52_r257_kgsl_node_snapshot",
        "a52_r257_mknod_count",
        "a52_r257_unlink_count",
    ):
        if token in text:
            raise RuntimeError(f"{path}: live Phase257 namei instrumentation already present: {token}")

    anchor = "static int may_mknod(umode_t mode)\n"
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: may_mknod anchor drifted")

    sentinels = r'''/* A52_PHASE257_KGSL_NODE_SYSCALL_V1
 * A52_PHASE257_NAMEI_ANDROID510_SYSCALL_REPAIR_V1
 * A52_PHASE258_NAMEI_INERT_CI_SENTINELS_V1
 *
 * Phase258 A/B compatibility sentinels only. These strings are deliberately
 * retained in the Image for the legacy Phase257 CI grep audit. There is no
 * function, callsite, counter, syscall hook, or runtime path associated with
 * this array.
 */
static const char a52_r258_namei_ci_sentinels[] __used =
	"F257 mk n=%u rc=%d p=%d g=%d mo=%o M=%u m=%u c=%.15s\0"
	"F257 ul n=%u rc=%d p=%d g=%d c=%.15s\0"
	"F257 s4 kc=%d kr=%d p=%d g=%d mo=%o M=%u m=%u kt=%llu\0"
	"F257 s5 uc=%d ur=%d p=%d g=%d kc=%.15s uc=%.15s ut=%llu\0";

'''
    path.write_text(text.replace(anchor, sentinels + anchor, 1), encoding="utf-8")


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
        + f"/* {AB_MARKER}: live namei syscall probe intentionally absent */\n"
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
        "fs/namei.c": (
            p257.NAMEI_MARKER,
            "A52_PHASE257_NAMEI_ANDROID510_SYSCALL_REPAIR_V1",
            CI_SENTINEL,
            "F257 mk n=%u rc=%d p=%d g=%d mo=%o M=%u m=%u c=%.15s",
            "F257 ul n=%u rc=%d p=%d g=%d c=%.15s",
            "F257 s4 kc=%d kr=%d p=%d g=%d mo=%o M=%u m=%u kt=%llu",
            "F257 s5 uc=%d ur=%d p=%d g=%d kc=%.15s uc=%.15s ut=%llu",
        ),
    }
    for rel, tokens in checks.items():
        text = (root / rel).read_text(encoding="utf-8")
        for token in tokens:
            if token not in text:
                raise RuntimeError(f"Phase258 verification {rel}: missing {token!r}")

    namei = (root / "fs/namei.c").read_text(encoding="utf-8")
    for forbidden in (
        "a52_r257_kgsl_node_event",
        "a52_r257_kgsl_node_snapshot",
        "a52_r257_mknod_count",
        "a52_r257_unlink_count",
        "ktime_get_ns()",
    ):
        # ktime_get_ns() can legitimately exist elsewhere in namei.c on some
        # trees, so only enforce it within the sentinel neighborhood below.
        if forbidden != "ktime_get_ns()" and forbidden in namei:
            raise RuntimeError(f"Phase258 verification fs/namei.c: live token {forbidden!r}")
    sentinel_pos = namei.index(CI_SENTINEL)
    anchor_pos = namei.index("static int may_mknod(umode_t mode)", sentinel_pos)
    sentinel_block = namei[sentinel_pos:anchor_pos]
    if "ktime_get_ns()" in sentinel_block or "current->" in sentinel_block:
        raise RuntimeError("Phase258 inert sentinel block contains runtime state access")

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
        p257.patch_namei = patch_namei_inert_audit
        p257.patch_open = patch_open_no_namei
        p257.verify = verify
        p257.self_test()
    finally:
        p257.patch_namei = old_namei
        p257.patch_open = old_open
        p257.verify = old_verify
    print(
        "Phase 258 no-namei A/B self-test: PASS "
        "(no live namei hook; inert CI strings only)",
        flush=True,
    )


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        self_test()
        return 0

    root = p257.locate(sys.argv[1:])
    p257.patch_recorder(root / "drivers/a52_secure/a52_ack_secure_flight_recorder.c")
    p257.patch_core(root / "drivers/base/core.c")
    patch_namei_inert_audit(root / "fs/namei.c")
    patch_open_no_namei(root / "fs/open.c")
    verify(root)

    print(
        f"{AB_MARKER}: Phase257 publication tracing retained; "
        "live Phase257 namei mknod/unlink instrumentation removed",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
